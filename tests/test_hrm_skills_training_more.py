"""Блок 66. HRM — навыки (сертификаты, прогресс, карьерные пути) и обучение (записи со стоимостью, удержания,
обязательства).

Снято 03.11.2026 в видимом MCP-браузере:
- «Навыки» → «Сертификаты»: колонки «Сотрудник, Сертификат, Организация, Выдан, Истекает»; «Добавить
  сертификат» → «Сотрудник *», «Навык (опционально)», «Название сертификата *», «Выдавшая организация»,
  «Дата выдачи», «Срок действия», «URL документа» → POST /skills/certifications;
- «Прогресс» — записи обучения (/training/records): «Сотрудник, Обучение, Статус (Записан…), Начало,
  Завершение, Оценка»; «Карьерные пути» — только список (создать на экране нельзя, в API нет удаления —
  тест их не создаёт);
- «Обучение» → «Записи»: «Стоимость, Компания %, Доля сотр., Ежемес. удерж., Удержано, Обяз. до,
  Обязательство»; доли и удержания считаются у ЗАВЕРШЁННОЙ записи (у «Зачислен» — нули): 1 200 MDL при
  доле компании 50% и 6 месяцах → 600 / 600, по 100 MDL с начала следующего месяца (/training/deductions),
  обязательство на 18 месяцев от завершения (/training/obligations); «Применить» удержание — PUT
  /training/deductions/<id>/apply;
- «Удержания» без сотрудника и даты, «Обязательства» — «Срок истёк» у действующего — bord-56.
Свой сотрудник уносит каскадом сертификаты, записи, удержания.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (screen_action, screen_tab, QA_PREFIX, clean_up, create_employee, create_legal_entity, date_field, delete_employee,
                         dropdown, field, hrm_delete, hrm_get, hrm_json, hrm_post, hrm_put, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()


def main(page: Page):
    return page.locator("main")


def tab(page: Page, name: str):
    screen_tab(page, name).click()


def modal(page: Page, heading: str):
    return page.locator("div.fixed.inset-0:visible").filter(
        has=page.get_by_role("heading", name=heading, exact=True)).last


def first_of_next_month(d: dt.date) -> dt.date:
    return (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


def add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    return d.replace(year=d.year + y, month=m + 1, day=min(d.day, 28))


@pytest.fixture
def person(auth_page: Page):
    le = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Развитие+ {stamp()}")
    yield emp
    for s in items(hrm_json(hrm_get(auth_page, "/skills/catalog"))):
        if s["name"].startswith(QA_PREFIX):
            hrm_delete(auth_page, f"/skills/catalog/{s['id']}")
    delete_employee(auth_page, emp["id"])
    clean_up(auth_page)


def paid_course(page: Page, emp_id: int, title: str) -> dict:
    """Завершённая запись обучения 1 200 MDL, доля компании 50%, удержание за 6 месяцев."""
    return hrm_json(hrm_post(page, "/training/records", {
        "employee_id": emp_id, "training_title": title, "training_type": "external", "provider": "QA Academy",
        "total_cost": 1200, "currency": "MDL", "company_share_pct": 50, "deduction_months": 6,
        "started_at": str(TODAY), "completed_at": str(TODAY)}))


# Кейс: «Добавить сертификат» сотруднику с привязкой к навыку — строка = API
@pytest.mark.writes_data
def test_certificate_form(auth_page: Page, person):
    skill = hrm_json(hrm_post(auth_page, "/skills/catalog", {"name": f"{QA_PREFIX} Английский {stamp()}", "category": "language"}))
    title = f"{QA_PREFIX} IELTS {stamp()}"
    expires = TODAY.replace(year=TODAY.year + 2)
    open_hrm(auth_page, "/admin/skills")
    tab(auth_page, "Сертификаты")
    screen_action(auth_page, "Добавить сертификат").click()
    box = modal(auth_page, "Добавить сертификат")
    dropdown(auth_page, box, "Сотрудник *", person["telegram_first_name"])
    box_skill = field(box, "Навык (опционально)")
    box_skill.click()
    auth_page.get_by_role("option").filter(has_text=skill["name"]).click()
    field(box, "Название сертификата *").fill(title)
    field(box, "Выдавшая организация").fill("British Council")
    date_field(box, "Дата выдачи").fill(str(TODAY))
    date_field(box, "Срок действия").fill(str(expires))
    with auth_page.expect_response(lambda r: r.url.endswith("/skills/certifications") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    [cert] = items(hrm_json(hrm_get(auth_page, "/skills/certifications", {"employee_id": person["id"]})))
    assert (cert["name"], cert["issuing_org"], cert["issued_date"], cert["expiry_date"]) == \
        (title, "British Council", str(TODAY), str(expires)), cert
    row = main(auth_page).locator("tbody tr").filter(has_text=title)
    for text in (person["telegram_first_name"], "British Council", TODAY.strftime("%d.%m.%Y"), expires.strftime("%d.%m.%Y")):
        expect(row).to_contain_text(text)


# Кейс: «Прогресс» и «Карьерные пути» — списки = /training/records и /skills/career-paths
@pytest.mark.writes_data
def test_progress_and_career_paths(auth_page: Page, person):
    title = f"{QA_PREFIX} Курс прогресс {stamp()}"
    hrm_json(hrm_post(auth_page, "/training/records", {"employee_id": person["id"], "training_title": title,
                                                       "started_at": str(TODAY)}))
    records = hrm_json(hrm_get(auth_page, "/training/records"))
    open_hrm(auth_page, "/admin/skills")
    tab(auth_page, "Прогресс")
    rows = main(auth_page).locator("tbody tr")
    expect(rows).to_have_count(len(items(records)))
    mine = rows.filter(has_text=title)
    expect(mine).to_contain_text(person["telegram_first_name"])
    expect(mine).to_contain_text(TODAY.strftime("%d.%m.%Y"))

    paths = hrm_json(hrm_get(auth_page, "/skills/career-paths"))
    tab(auth_page, "Карьерные пути")
    if not paths:
        expect(main(auth_page)).to_contain_text("Нет карьерных путей")
    for p in paths:
        expect(main(auth_page)).to_contain_text(p["name"])
        for level in p["levels"]:
            expect(main(auth_page)).to_contain_text(level["title"])


# Кейс: Завершённое платное обучение — доли 600 / 600, 6 удержаний по 100 MDL с начала следующего месяца, обязательство 18 мес.; «Применить» удержание
@pytest.mark.writes_data
def test_paid_training_deductions(auth_page: Page, person):
    title = f"{QA_PREFIX} Курс удержания {stamp()}"
    rec = paid_course(auth_page, person["id"], title)
    assert (rec["status"], rec["company_share_amount"], rec["employee_share_amount"], rec["monthly_deduction"]) == \
        ("completed", 600.0, 600.0, 100.0), rec
    assert rec["obligation_end_date"] == str(add_months(TODAY, 18)), rec
    deductions = hrm_json(hrm_get(auth_page, "/training/deductions", {"employee_id": person["id"]}))
    start = first_of_next_month(TODAY)
    assert [(d["deduction_month"], d["amount"], d["status"]) for d in deductions] == \
        [(str(add_months(start, i).replace(day=1)), 100.0, "pending") for i in range(6)], deductions
    [ob] = [o for o in hrm_json(hrm_get(auth_page, "/training/obligations")) if o["employee_id"] == person["id"]]
    assert (ob["obligation_status"], ob["remaining_deduction"]) == ("active", 600.0), ob

    applied = hrm_put(auth_page, f"/training/deductions/{deductions[0]['id']}/apply")
    assert applied.ok, applied.text()
    rec = items(hrm_json(hrm_get(auth_page, "/training/records", {"employee_id": person["id"]})))[0]
    assert rec["deducted_total"] == 100.0, rec

    open_hrm(auth_page, "/admin/training")
    tab(auth_page, "Записи")
    row = main(auth_page).locator("tbody tr").filter(has_text=title)
    expect(row).to_contain_text(person["telegram_first_name"])
    for amount in (r"1\s200 MDL", r"600 MDL", r"100 MDL"):
        expect(row).to_contain_text(re.compile(amount))
    expect(row).to_contain_text("50%")
    expect(row).to_contain_text(add_months(TODAY, 18).strftime("%d.%m.%Y"))
    tab(auth_page, "Обзор")
    expect(main(auth_page)).to_contain_text("Активные обязательства")
    expect(main(auth_page)).to_contain_text("Удержано за год")


# Кейс: «Удержания» показывают сотрудника и месяц, «Обязательства» — действующее не «Срок истёк»
@pytest.mark.writes_data
def test_deductions_and_obligations_tabs(auth_page: Page, person):
    title = f"{QA_PREFIX} Курс вкладки {stamp()}"
    paid_course(auth_page, person["id"], title)
    open_hrm(auth_page, "/admin/training")
    tab(auth_page, "Обязательства")
    until = add_months(TODAY, 18).strftime("%d.%m.%Y")
    card = main(auth_page).locator("div").filter(has_text=title).filter(has_text=until).last
    expect(card).to_be_visible()
    expect(card).not_to_contain_text("Срок истёк", timeout=3_000)
    tab(auth_page, "Удержания")
    rows = main(auth_page).locator("tbody tr").filter(has_text=person["telegram_first_name"])
    expect(rows).to_have_count(6, timeout=3_000)
    expect(rows.first).to_contain_text(first_of_next_month(TODAY).strftime("%m.%Y"))
