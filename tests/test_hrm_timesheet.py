"""Блок 56. HRM — табель (/admin/timesheet).

Снято 02.11.2026 (в видимом MCP-браузере и по справке help.bord.one/hrm/tabel):
- месяц «Ноябрь 2026» с «Предыдущий месяц / Следующий месяц»; данные = GET
  /timesheet/summary?date_from=<1-е>&date_to=<последнее>, /timesheet/month-totals?year=&month=,
  /timesheet/public-holidays; «Сотрудников: N», «Гос. выходные · MD», «Колонки», «Скачать табель»;
- строка на сотрудника, ячейка дня — кнопка «клик — часы» → окно «Часы за день · <имя> ·
  ДД.ММ.ГГГГ» (вкладки «Часы / Отсутствие», «Обычные часы» = 8, «Сверхурочные»; сумма не больше
  24) → POST /timesheet/ {employee_id, entry_date, hours_by_type {regular, overtime}};
  ячейка «10 •» с подписью «10 ч (об. 8, сверх. 2) · клик — часы», итоги «10.0 ч / об. 8 ·
  сверх. 2», «Дней отработано 1»; повторно — «Удалить» (сразу, без окна) → DELETE;
- одобренная заявка из «Отсутствий» ложится в табель обозначением из политики («QA»);
- «Заполнить по норме», «Проставить 8 часов всем за этот день» не жмём — заденут чужих людей.
Свой сотрудник (принят 1-го числа месяца), своя политика и заявка — удаляются после теста.
"""
import datetime as dt
import io
import re

import openpyxl
import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (screen_action, QA_PREFIX, clean_up, create_employee, create_legal_entity, delete_employee, field, header_button,
                         hrm_delete, hrm_get, hrm_json, hrm_post, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()
MONTH_START = TODAY.replace(day=1)
MONTH_END = (MONTH_START + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]


def main(page: Page):
    return page.locator("main")


def emp_row(page: Page, name: str):
    return main(page).locator("tr").filter(has_text=name).first


def work_days(page: Page, n: int) -> list[dt.date]:
    """Первые n будних дней месяца с 5-го числа, мимо гос. выходных."""
    hol = hrm_json(hrm_get(page, "/timesheet/public-holidays", {"year": TODAY.year, "month": TODAY.month}))
    hol_dates = {h["date"] for h in hol.get("days", [])}  # {enabled, country: "MD", days: [{date, name}]}
    days, d = [], MONTH_START + dt.timedelta(days=4)
    while len(days) < n and d <= MONTH_END:
        if d.weekday() < 5 and str(d) not in hol_dates:
            days.append(d)
        d += dt.timedelta(days=1)
    return days


def day_cell(page: Page, name: str, day: dt.date):
    """Ячейка дня — кнопка в строке сотрудника; пустые подписаны «клик — часы», считаем по порядку."""
    return emp_row(page, name).locator("td").nth(day.day + 2).locator("[title]").first


@pytest.fixture
def worker(auth_page: Page):
    le = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Табель {stamp()}", hire_date=str(MONTH_START))
    yield emp
    for req in items(hrm_json(hrm_get(auth_page, "/leave/requests", {"limit": 500}))):
        if req.get("employee_id") == emp["id"]:
            hrm_delete(auth_page, f"/leave/requests/{req['id']}")
    delete_employee(auth_page, emp["id"])
    for pol in items(hrm_json(hrm_get(auth_page, "/leave/policies"))):
        if pol["name"].startswith(QA_PREFIX):
            hrm_delete(auth_page, f"/leave/policies/{pol['id']}")
    clean_up(auth_page)


# Кейс: Табель текущего месяца = API, листание месяцев
def test_grid_and_month_navigation(auth_page: Page):
    with auth_page.expect_response(lambda r: "/api/hrm/timesheet/summary" in r.url) as resp:
        open_hrm(auth_page, "/admin/timesheet")
    assert f"date_from={MONTH_START}" in resp.value.url and f"date_to={MONTH_END}" in resp.value.url, resp.value.url
    expect(main(auth_page)).to_contain_text(f"{MONTHS[TODAY.month - 1]} {TODAY.year}")
    active = items(hrm_json(hrm_get(auth_page, "/employees", {"status_filter": "active", "limit": 1000})))
    expect(main(auth_page)).to_contain_text(f"Сотрудников: {len(active)}")
    for col in ("Итого часов", "Дней отработано", "Дней отсутствия (всего)"):
        expect(main(auth_page).get_by_text(col, exact=True).first).to_be_visible()

    nxt = (MONTH_END + dt.timedelta(days=1))
    with auth_page.expect_response(lambda r: "/api/hrm/timesheet/summary" in r.url and f"date_from={nxt}" in r.url):
        screen_action(auth_page, "Следующий месяц").click()
    expect(main(auth_page)).to_contain_text(f"{MONTHS[nxt.month - 1]} {nxt.year}")
    with auth_page.expect_response(lambda r: "/api/hrm/timesheet/summary" in r.url and f"date_from={MONTH_START}" in r.url):
        screen_action(auth_page, "Предыдущий месяц").click()
    expect(main(auth_page)).to_contain_text(f"{MONTHS[TODAY.month - 1]} {TODAY.year}")


# Кейс: Часы за день — обычные и сверхурочные в ячейке и итогах, удаление отметки
@pytest.mark.writes_data
def test_hours_entry(auth_page: Page, worker):
    name = worker["telegram_first_name"]
    day = work_days(auth_page, 1)[0]
    open_hrm(auth_page, "/admin/timesheet")
    day_cell(auth_page, name, day).click()
    box = auth_page.locator("div.fixed:visible").filter(has_text="Часы за день").last
    expect(box).to_contain_text(f"{name} · {day:%d.%m.%Y}")
    expect(field(box, "Обычные часы")).to_have_value("8")
    field(box, "Сверхурочные").fill("2")
    with auth_page.expect_response(lambda r: r.url.split("?")[0].endswith("/api/hrm/timesheet/") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    body = resp.value.request.post_data_json
    assert (body["employee_id"], body["entry_date"], body["hours_by_type"]) == (worker["id"], str(day), {"regular": 8, "overtime": 2}), body
    # тот же запрос, что проверяет лимит 24 часов, видит отметку — значит, его пустой ответ что-то значит
    saved = items(hrm_json(hrm_get(auth_page, "/timesheet/", {"employee_id": worker["id"], "date_from": str(day), "date_to": str(day)})))
    assert len(saved) == 1 and saved[0]["hours_worked"] == 10, saved
    cell = emp_row(auth_page, name).get_by_title(re.compile(r"^10 ч \(об\. 8, сверх\. 2\)"))
    expect(cell).to_contain_text("10")
    row = emp_row(auth_page, name)
    expect(row).to_contain_text("10.0 ч")
    expect(row).to_contain_text(re.compile(r"об\. 8\s*·\s*сверх\. 2"))

    cell.click()
    box = auth_page.locator("div.fixed:visible").filter(has_text="Часы за день").last
    with auth_page.expect_response(lambda r: "/api/hrm/timesheet/" in r.url and r.request.method == "DELETE") as gone:
        box.get_by_role("button", name="Удалить", exact=True).click()
    assert gone.value.ok and f"entry_date={day}" in gone.value.url, gone.value.url
    expect(emp_row(auth_page, name)).to_contain_text("0.0 ч")


# Кейс: За день не больше 24 часов
def test_hours_limit_24(auth_page: Page, worker):
    name = worker["telegram_first_name"]
    day = work_days(auth_page, 1)[0]
    open_hrm(auth_page, "/admin/timesheet")
    day_cell(auth_page, name, day).click()
    box = auth_page.locator("div.fixed:visible").filter(has_text="Часы за день").last
    expect(box).to_contain_text("не больше 24 часов")
    field(box, "Обычные часы").fill("20")
    field(box, "Сверхурочные").fill("10")
    try:  # кнопка либо неактивна, либо сервер отказывает — итог проверяем по API
        box.get_by_role("button", name="Сохранить", exact=True).click(timeout=3_000)
        auth_page.wait_for_timeout(1_000)
    except Exception:  # noqa: BLE001 — неактивная кнопка: клик не прошёл, так и задумано
        pass
    entries = hrm_json(hrm_get(auth_page, "/timesheet/", {"employee_id": worker["id"], "date_from": str(day), "date_to": str(day)}))
    assert not items(entries), f"30 часов за день сохранились: {entries}"


# Кейс: Одобренный отпуск ложится в табель обозначением из политики
@pytest.mark.writes_data
def test_approved_leave_in_timesheet(auth_page: Page, worker):
    name = worker["telegram_first_name"]
    d1, d2 = work_days(auth_page, 2)
    policy = hrm_json(hrm_post(auth_page, "/leave/policies", {
        "name": f"{QA_PREFIX} Отпуск табеля {stamp()}", "policy_type": "accumulative", "accrual_method": "annual",
        "accrual_amount": 20, "requires_approval": False, "is_paid": True, "timesheet_code": "QA", "category": "paid_leave"}))
    req = hrm_json(auth_page.request.post(
        f"https://hrm.bord.one/api/hrm/leave/requests?employee_id={worker['id']}",
        headers={"Authorization": f"Bearer {auth_page._hrm_sso['token']}"},
        data={"policy_id": policy["id"], "start_date": str(d1), "end_date": str(d2), "reason": f"{QA_PREFIX}"}))
    assert req["status"] == "approved", req
    open_hrm(auth_page, "/admin/timesheet")
    row = emp_row(auth_page, name)
    for d in (d1, d2):
        expect(row.locator("td").nth(d.day + 2)).to_contain_text("QA")
    expect(row).to_contain_text(re.compile(r"0\.0 ч"))
    totals = [t.strip() for t in row.locator("td").all_inner_texts()[-6:]]
    assert "2" in totals, totals  # «Дней отсутствия (всего)» и «Дней оплачиваемого отпуска»


# Кейс: «Скачать табель» — xlsx текущего месяца
def test_export(auth_page: Page):
    open_hrm(auth_page, "/admin/timesheet")
    with auth_page.expect_download() as dl:
        header_button(auth_page, "Скачать табель").click() if header_button(auth_page, "Скачать табель").count() \
            else screen_action(auth_page, "Скачать табель").click()
        menu_item = auth_page.get_by_role("button", name=re.compile("^(Скачать|Excel)"), exact=False)
        auth_page.wait_for_timeout(800)
        if menu_item.count() > 1:
            menu_item.last.click()
    download = dl.value
    assert download.suggested_filename.endswith(".xlsx"), download.suggested_filename
    wb = openpyxl.load_workbook(io.BytesIO(open(download.path(), "rb").read()), read_only=True)
    assert wb.active.max_row and wb.active.max_row > 1


# Кейс: Государственный выходной — день выделен, в подсказке название из API
def test_public_holiday_marked(auth_page: Page):
    open_hrm(auth_page, "/admin/timesheet")
    month = MONTH_START
    for _ in range(12):  # ближайший месяц с гос. выходным (MD)
        hol = hrm_json(hrm_get(auth_page, "/timesheet/public-holidays", {"year": month.year, "month": month.month}))
        if hol.get("days"):
            break
        month = (month + dt.timedelta(days=32)).replace(day=1)
        with auth_page.expect_response(lambda r: "/api/hrm/timesheet/summary" in r.url and f"date_from={month}" in r.url):
            screen_action(auth_page, "Следующий месяц").click()
    else:
        pytest.skip("гос. выходных в ближайший год нет")
    assert (hol["enabled"], hol["country"]) == (True, "MD"), hol
    for h in hol["days"]:
        day = dt.date.fromisoformat(h["date"]).day
        head = main(auth_page).locator("th").filter(has_text=re.compile(rf"^\s*{day}\b")).first
        expect(head).to_have_class(re.compile(r"text-red"))
        expect(head.locator("[title^='Государственный выходной (MD)']")).to_have_attribute(
            "title", re.compile(rf"^Государственный выходной \(MD\): {re.escape(h['name'])}\."))
