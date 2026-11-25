"""Блок 65. HRM — необратимые и общие для организации действия (разрешено 03.11.2026: «BORD QA тестовая
организация — там можешь делать что хочешь»). Где можно — тест возвращает как было.

Снято 03.11.2026 в видимом MCP-браузере:
- ТМЦ → «Выдача ТМЦ»: строка «Акт — не сформирован · <дата> · <сотрудник> · 1 поз. / 1 ед. · Выдано · Открыть»;
  «Открыть» → окно выдачи («Акт не сформирован»), «Акт выдачи» → «Сформировать акт выдачи» (позиции с
  галочками, «Доп. условия», «…номер акта будет привязан ко всем позициям. Это действие нельзя
  отменить.») → «Подтвердить» → «Формирование…» (PDF, до ~10 с) → POST /tmc/issues/<id>/generate-act →
  «Акт TMC-ACT-<год>-NNN», «Выдача: Сгенерирован», «Просмотреть акт / Печать / Скачать / Не подписан /
  Удалить акт»; «Удалить акт» → «Удалить акт выдачи» (позиции, «вернётся на склад: 1 ед.», «Номер …
  освободится и будет переиспользован», «Будут безвозвратно удалены: выдач — 1 …») → DELETE
  /tmc/issues/<id>/act?kind=issue — удаляет и саму выдачу, остаток возвращается;
- Табель: «Заполнить по норме» (весь список), «Проставить 8 часов всем за этот день» (шапка дня),
  «Заполнить месяц по норме» (строка) — одно окно «Заполнить по норме»: «С / По», «Обычных часов в день»
  8, «Охват» (Отмеченные / Весь отфильтрованный список / Текущая страница), «Перезаписать существующие
  отметки», предпросмотр «Будет заполнено: N ячеек у M сотрудников · Пропущено выходных …»
  (/timesheet/bulk-fill/plan) → «Заполнить» (/bulk-fill) → плашка «Заполнено ячеек: N · Отменить» →
  /bulk-fill/<op>/undo; весь ноябрь на всех — 500 (bord-55: ячейка Maxim Țurcan 02.11);
- «Отсутствия» → «Балансы» → «Обновить» → «Инициализировать балансы для всех сотрудников по активным
  политикам?» → «Да» → POST /leave/balances/initialize;
- Настройки → «Формат экспорта»: «Колонки выгрузки (N)», «Доступные колонки» (стандартные поля, «Семья»)
  — галочки, «×» / «Убрать», «Сбросить», «Сохранить формат» → PUT /export-presets/current {columns};
  выгрузка сотрудников (Excel) идёт ровно выбранными колонками; тест возвращает пустой формат;
- увольнения и решения по нарушению на экране нет — только API: /discipline/dismissals/ (приказ
  DISMISSAL_ORDER-<год>-NNNN), «Деактивировать доступ» делает сотрудника неактивным; отмена увольнения
  сотрудника не возвращает (спорно — в README); PUT resolution у нарушения выдаёт номер документа
  VIOLATION_<РЕШЕНИЕ>-<год>-NNNN.
"""
import datetime as dt
import io
import re

import openpyxl
import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (screen_action, screen_tab, QA_PREFIX, clean_up, create_employee, create_legal_entity, delete_employee, hrm_delete, hrm_get, hrm_json,
                         hrm_post, hrm_put, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()


def main(page: Page):
    return page.locator("main")


def tab(page: Page, name: str):
    screen_tab(page, name).click()


def dialog(page: Page, text: str):
    return page.locator("div.fixed.inset-0:visible").filter(has_text=text).last


def next_workday(after: dt.date) -> dt.date:
    d = after + dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


@pytest.fixture
def person(auth_page: Page):
    le = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Орг {stamp()}")
    yield emp
    delete_employee(auth_page, emp["id"])
    clean_up(auth_page)


# ───────────────────────── ТМЦ: акт выдачи ─────────────────────────

# Кейс: «Сформировать акт выдачи» — номер TMC-ACT, PDF; «Удалить акт» освобождает номер и возвращает остаток
@pytest.mark.writes_data
def test_tmc_issue_act(auth_page: Page, person):
    item = hrm_json(hrm_post(auth_page, "/tmc/catalog/", {"name": f"{QA_PREFIX} Ноутбук акт {stamp()}", "code": "QA-ACT-" + stamp()[-6:],
                                                          "is_tracked": True, "quantity_on_hand": 3, "unit": "шт", "cost": 500}))
    try:
        hrm_json(hrm_post(auth_page, "/tmc/issues/bulk", {"employee_id": person["id"], "issue_date": str(TODAY),
                                                          "items": [{"catalog_id": item["id"], "quantity": 1}]}))
        open_hrm(auth_page, "/admin/tmc")
        tab(auth_page, "Выдача ТМЦ")
        row = main(auth_page).locator("tr").filter(has_text=person["telegram_first_name"])
        expect(row).to_contain_text("не сформирован")
        row.get_by_role("button", name="Открыть").click()
        card = dialog(auth_page, "Дата выдачи")
        expect(card).to_contain_text("Акт не сформирован")
        card.get_by_role("button", name="Акт выдачи", exact=True).click()
        form = dialog(auth_page, "Сформировать акт выдачи")
        expect(form).to_contain_text("Выбрано: 1 из 1")
        expect(form).to_contain_text("Это действие нельзя отменить.")
        with auth_page.expect_response(lambda r: "/generate-act" in r.url, timeout=60_000) as gen:
            form.get_by_role("button", name="Подтвердить", exact=True).click()
        assert gen.value.ok, gen.value.text()
        act = gen.value.json()
        assert re.fullmatch(rf"TMC-ACT-{TODAY.year}-\d{{3}}", act["act_number"]) and act["file_url"].endswith(".pdf"), act
        card = dialog(auth_page, f"Акт {act['act_number']}")
        expect(card).to_contain_text("Выдача: Сгенерирован")
        for name in ("Просмотреть акт", "Печать", "Скачать", "Удалить акт"):
            expect(card.get_by_role("button", name=name, exact=True)).to_be_visible()
        issue = next(i for i in items(hrm_json(hrm_get(auth_page, "/tmc/issues/", {"employee_id": person["id"]}))))
        assert issue["act_number"] == act["act_number"], issue
        assert next(i for i in items(hrm_json(hrm_get(auth_page, "/tmc/catalog/"))) if i["id"] == item["id"])["quantity_on_hand"] == 2

        card.get_by_role("button", name="Удалить акт", exact=True).click()
        sure = dialog(auth_page, "Удалить акт выдачи")
        expect(sure).to_contain_text(f"Номер {act['act_number']} освободится и будет переиспользован.")
        expect(sure).to_contain_text("вернётся на склад: 1 ед.")
        with auth_page.expect_response(lambda r: "/act?kind=issue" in r.url and r.request.method == "DELETE") as gone:
            sure.get_by_role("button", name="Удалить акт", exact=True).click()
        assert gone.value.ok, gone.value.text()
        result = gone.value.json()
        assert (result["freed_numbers"], result["deleted"]["issues"], result["deleted"]["generated_docs"]) == \
            ([act["act_number"]], 1, 1), result
        assert next(i for i in items(hrm_json(hrm_get(auth_page, "/tmc/catalog/"))) if i["id"] == item["id"])["quantity_on_hand"] == 3
        assert items(hrm_json(hrm_get(auth_page, "/tmc/issues/", {"employee_id": person["id"]}))) == []
    finally:  # если тест упал до «Удалить акт»: акт (если есть) и выдача, потом позиция
        for i in items(hrm_json(hrm_get(auth_page, "/tmc/issues/", {"employee_id": person["id"]}))):
            hrm_delete(auth_page, f"/tmc/issues/{i['id']}/act", {"kind": "issue", "cascade": "true"})
            hrm_delete(auth_page, f"/tmc/issues/{i['id']}")
        hrm_delete(auth_page, f"/tmc/catalog/{item['id']}")


# ───────────────────────── табель: массовое заполнение ─────────────────────────

def fill_dialog(page: Page):
    return dialog(page, "Обычных часов в день")


# Кейс: «Проставить 8 часов всем за этот день» — предпросмотр = /bulk-fill/plan, «Заполнено ячеек: N» → «Отменить» откатывает
@pytest.mark.writes_data
def test_timesheet_fill_day_for_all(auth_page: Page, person):
    day = next_workday(TODAY)
    open_hrm(auth_page, "/admin/timesheet")
    if day.month != TODAY.month:
        screen_action(auth_page, "Следующий месяц").click()
    with auth_page.expect_response(lambda r: r.url.endswith("/timesheet/bulk-fill/plan")) as plan:
        header = main(auth_page).locator("th").filter(has=auth_page.locator('button[title="Проставить 8 часов всем за этот день"]'))
        header.filter(has_text=re.compile(rf"^\s*{day.day}\b")).first.locator(
            'button[title="Проставить 8 часов всем за этот день"]').click()
    body, preview = plan.value.request.post_data_json, plan.value.json()
    assert (body["date_from"], body["date_to"], body["hours"]) == (str(day), str(day), 8), body
    assert person["id"] in [e["employee_id"] for e in body["employments"]], body
    box = fill_dialog(auth_page)
    expect(box).to_contain_text(f"Будет заполнено: {preview['will_fill']} ячеек у {preview['employees']} сотрудников")
    expect(box).to_contain_text("Сверхурочные массово не проставляются")
    with auth_page.expect_response(lambda r: r.url.endswith("/timesheet/bulk-fill") and r.request.method == "POST") as fill:
        box.get_by_role("button", name="Заполнить", exact=True).click()
    assert fill.value.ok, fill.value.text()
    op = fill.value.json()
    assert op["filled"] == preview["will_fill"] and op["op_id"], op
    mine = hrm_json(hrm_get(auth_page, "/timesheet/", {"employee_id": person["id"], "date_from": str(day), "date_to": str(day)}))
    assert [(float(e["hours_worked"]), e["source"]) for e in items(mine)] == [(8.0, "bulk")], mine
    banner = main(auth_page).locator("div").filter(has_text=f"Заполнено ячеек: {op['filled']}").filter(
        has=auth_page.get_by_role("button", name="Отменить", exact=True)).last
    with auth_page.expect_response(lambda r: f"/bulk-fill/{op['op_id']}/undo" in r.url) as undo:
        banner.get_by_role("button", name="Отменить", exact=True).click()
    assert undo.value.ok and undo.value.json()["deleted"] == op["filled"], undo.value.text()
    expect(main(auth_page).get_by_text(f"Заполнено ячеек: {op['filled']}")).to_have_count(0)
    assert items(hrm_json(hrm_get(auth_page, "/timesheet/", {"employee_id": person["id"], "date_from": str(day),
                                                             "date_to": str(day)}))) == []


# Кейс: «Заполнить месяц по норме» в строке своего сотрудника — рабочие дни по 8 ч, выходные пропущены, «Отменить»
@pytest.mark.writes_data
def test_timesheet_fill_month_one_employee(auth_page: Page, person):
    open_hrm(auth_page, "/admin/timesheet")
    row = main(auth_page).locator("tr").filter(has_text=person["telegram_first_name"])
    with auth_page.expect_response(lambda r: r.url.endswith("/timesheet/bulk-fill/plan")) as plan:
        row.locator('button[title="Заполнить месяц по норме"]').click()
    body, preview = plan.value.request.post_data_json, plan.value.json()
    assert (body["employments"], body["scope_label"]) == ([{"employee_id": person["id"], "secondary_employment_id": None}],
                                                          "один сотрудник"), body
    box = fill_dialog(auth_page)
    expect(box).to_contain_text(f"Будет заполнено: {preview['will_fill']} ячеек у 1 сотрудников")
    expect(box).to_contain_text(f"Пропущено выходных: {preview['skipped_weekend']}")
    expect(box.get_by_text("Охват")).to_have_count(0)  # для одной строки охвата нет
    with auth_page.expect_response(lambda r: r.url.endswith("/timesheet/bulk-fill") and r.request.method == "POST") as fill:
        box.get_by_role("button", name="Заполнить", exact=True).click()
    op = fill.value.json()
    assert fill.value.ok and op["filled"] == preview["will_fill"] > 0, op
    entries = items(hrm_json(hrm_get(auth_page, "/timesheet/", {"employee_id": person["id"], "date_from": body["date_from"],
                                                                "date_to": body["date_to"]})))
    assert len(entries) == op["filled"] and all(dt.date.fromisoformat(e["entry_date"]).weekday() < 5 for e in entries), entries
    with auth_page.expect_response(lambda r: f"/bulk-fill/{op['op_id']}/undo" in r.url) as undo:
        screen_action(auth_page, "Отменить", exact=True).click()
    assert undo.value.ok, undo.value.text()
    assert items(hrm_json(hrm_get(auth_page, "/timesheet/", {"employee_id": person["id"], "date_from": body["date_from"],
                                                             "date_to": body["date_to"]}))) == []


# Кейс: «Заполнить по норме» весь список за текущий месяц
@pytest.mark.writes_data
def test_timesheet_fill_norm_all(auth_page: Page, person):
    open_hrm(auth_page, "/admin/timesheet")
    screen_action(auth_page, "Заполнить по норме", exact=True).click()
    box = fill_dialog(auth_page)
    expect(box).to_contain_text("Будет заполнено:")
    with auth_page.expect_response(lambda r: r.url.endswith("/timesheet/bulk-fill") and r.request.method == "POST") as fill:
        box.get_by_role("button", name="Заполнить", exact=True).click()
    try:
        assert fill.value.ok, f"{fill.value.status} {fill.value.text()[:200]}"
    finally:
        if fill.value.ok and fill.value.json().get("op_id"):
            hrm_post(auth_page, f"/timesheet/bulk-fill/{fill.value.json()['op_id']}/undo")


# ───────────────────────── отсутствия: балансы ─────────────────────────

# Кейс: «Балансы» → «Обновить» — подтверждение, инициализация по активным политикам; список = /leave/balances
@pytest.mark.writes_data
def test_leave_balances_refresh(auth_page: Page):
    open_hrm(auth_page, "/admin/leave")
    screen_action(auth_page, re.compile(r"^\s*Балансы")).click()
    screen_action(auth_page, "Обновить", exact=True).click()
    sure = dialog(auth_page, "Инициализировать балансы для всех сотрудников по активным политикам?")
    with auth_page.expect_response(lambda r: "/leave/balances/initialize" in r.url) as init:
        sure.get_by_role("button", name="Да", exact=True).click()
    assert init.value.ok and "created" in init.value.json(), init.value.text()
    balances = items(hrm_json(hrm_get(auth_page, "/leave/balances", {"year": TODAY.year})))
    rows = main(auth_page).locator("tbody tr")
    expect(rows).to_have_count(len(balances))
    for b in balances[:5]:
        expect(rows.filter(has_text=b["employee_name"]).first).to_contain_text(str(int(float(b.get("entitled_days") or b.get("total_days") or 0))))


# ───────────────────────── настройки: формат экспорта ─────────────────────────

# Кейс: «Формат экспорта» — выбранные колонки задают выгрузку сотрудников в Excel; «Сбросить» возвращает формат по умолчанию
@pytest.mark.writes_data
def test_export_format(auth_page: Page, person):
    picked = ["Фамилия Имя", "IDNP", "Дата найма"]
    open_hrm(auth_page, "/admin/settings")
    screen_action(auth_page, "Формат экспорта", exact=True).click()
    box = main(auth_page)
    try:
        expect(box).to_contain_text("Колонки выгрузки (0)")
        for n, name in enumerate(picked, 1):  # выбранная колонка уходит из «Доступных» в «Колонки выгрузки»
            box.locator("label:visible").filter(has_text=re.compile(rf"^\s*{re.escape(name)}\s*$")).first.click()
            expect(box).to_contain_text(f"Колонки выгрузки ({n})")
        with auth_page.expect_response(lambda r: "/export-presets/current" in r.url and r.request.method == "PUT") as put:
            box.get_by_role("button", name="Сохранить формат").click()
        assert put.value.ok, put.value.text()
        assert [c["key"] for c in put.value.request.post_data_json["columns"]] == ["full_name", "national_id", "hire_date"]
        xlsx = hrm_get(auth_page, "/employees/export/active")
        assert xlsx.ok, xlsx.status
        sheet = openpyxl.load_workbook(io.BytesIO(xlsx.body())).active
        assert [c.value for c in sheet[1]] == picked
        assert person["telegram_first_name"] in " ".join(str(c.value) for row in sheet.iter_rows(min_row=2) for c in row)

        box.get_by_role("button", name="Сбросить", exact=True).click()
        expect(box).to_contain_text("Колонки выгрузки (0)")
        with auth_page.expect_response(lambda r: "/export-presets/current" in r.url and r.request.method == "PUT") as reset:
            box.get_by_role("button", name="Сохранить формат").click()
        assert reset.value.request.post_data_json == {"columns": []}
        sheet = openpyxl.load_workbook(io.BytesIO(hrm_get(auth_page, "/employees/export/active").body())).active
        assert len([c for c in sheet[1] if c.value]) > 3  # формат по умолчанию — все колонки
    finally:
        hrm_put(auth_page, "/export-presets/current", {"columns": []})


# ───────────────────────── дисциплина: увольнение и решение (API) ─────────────────────────

# Кейс: Увольнение (API) — приказ DISMISSAL_ORDER, «Деактивировать доступ» делает сотрудника неактивным; отмена
@pytest.mark.writes_data
def test_dismissal_with_access_deactivation(auth_page: Page, person):
    d = hrm_json(hrm_post(auth_page, "/discipline/dismissals/", {
        "employee_id": person["id"], "dismissal_type": "resignation", "reason": f"{QA_PREFIX} уход",
        "last_working_day": str(TODAY + dt.timedelta(days=14))}))
    got = hrm_json(hrm_get(auth_page, f"/discipline/dismissals/{d['id']}"))
    assert (got["status"], got["access_deactivated"]) == ("initiated", False), got
    assert re.fullmatch(rf"DISMISSAL_ORDER-{TODAY.year}-\d{{4}}", got["dismissal_order_number"]), got
    assert hrm_json(hrm_get(auth_page, f"/discipline/dismissals/{d['id']}/tmc-checklist"))["total_items"] == 0
    assert hrm_json(hrm_post(auth_page, f"/discipline/dismissals/{d['id']}/deactivate-access"))["ok"] is True
    emp = hrm_json(hrm_get(auth_page, f"/employees/{person['id']}"))
    assert (emp["is_active"], emp["employment_status"]) == (False, "terminated"), emp
    assert hrm_json(hrm_get(auth_page, f"/discipline/dismissals/{d['id']}"))["access_deactivated"] is True
    hrm_json(hrm_post(auth_page, f"/discipline/dismissals/{d['id']}/cancel"))
    assert hrm_json(hrm_get(auth_page, f"/discipline/dismissals/{d['id']}"))["status"] == "cancelled"
    open_hrm(auth_page, "/admin/discipline")
    tab(auth_page, "Аудит")
    expect(main(auth_page)).to_contain_text(person["telegram_first_name"])


# Кейс: Решение по нарушению (API) — номер документа по журналу VIOLATION_<решение>, растёт на 1
@pytest.mark.writes_data
def test_violation_resolution_number(auth_page: Page, person):
    numbers = []
    for _ in range(2):
        v = hrm_json(hrm_post(auth_page, "/discipline/violations/", {
            "employee_id": person["id"], "violation_type": "qa_resolution", "severity": "minor",
            "description": f"{QA_PREFIX} решение", "incident_date": str(TODAY)}))
        r = hrm_json(hrm_put(auth_page, f"/discipline/violations/{v['id']}", {"resolution": "warning"}))
        numbers.append(r["resolution_document_number"])
        assert hrm_json(hrm_get(auth_page, f"/discipline/violations/{v['id']}"))["resolution"] == "warning"
    seq = [int(n.rsplit("-", 1)[1]) for n in numbers]
    assert all(re.fullmatch(rf"VIOLATION_WARNING-{TODAY.year}-\d{{4}}", n) for n in numbers) and seq[1] == seq[0] + 1, numbers
    actions = items(hrm_json(hrm_get(auth_page, "/discipline/actions/", {"employee_id": person["id"]})))
    assert sorted(a["action_type"] for a in actions) == ["warning", "warning"], actions
