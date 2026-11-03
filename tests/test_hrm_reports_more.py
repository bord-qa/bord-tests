"""Блок 69. HRM — отчёты (отчёт по журналам, «Онбординг», выгрузки), личные настройки уведомлений,
налоговые льготы сотрудника.

Снято 03.11.2026 в видимом MCP-браузере:
- «Отчёты» → «Отчёт по журналам»: фильтры «Все журналы / Все юрлица», даты, «Excel», колонки «#, Номер
  документа, Имя, Фамилия, ИДНП, Юрлицо, Шаблон, Журнал, Дата, Статус» = /reports/contract-numbers
  (документы любого журнала, не только контрактного); «Онбординг» — «Активных онбордингов / Завершено в
  этом месяце / Просроченных задач» = /onboard/dashboard; «Обзор» и «Текучесть» — кнопки «Excel» / «PDF»;
- «Уведомления» → «Настройки»: «Настройте, какие уведомления вы хотите получать и по каким каналам»,
  таблица «Событие · В приложении · Email» (День рождения, Дата приёма, Годовщина, Окончание паспорта,
  Дата увольнения) → PUT /notifications/preferences {event_type, channel, is_enabled}; тест выключает и
  включает обратно только «В приложении» (Email не трогаем);
- карточка сотрудника → «Семья» → «Налоговые льготы» («Всего льгот: N», «Для заявления «Cererea privind
  acordarea scutirilor»…»): «Личная льгота сотрудника» (Не заявлена / Личная льгота / Личная
  повышенная льгота), «Льготы с даты», у членов семьи «Без льготы / Иждивенец / Иждивенец с
  инвалидностью», «Примечание», «Сохранить» → PUT /employees/<id>/tax-exemptions; ниже «Изменения
  раздела» — журнал правок семьи.
"""
import datetime as dt
import io
import re

import openpyxl
import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (QA_PREFIX, clean_up, create_employee, create_legal_entity, date_field, delete_employee, field, hrm_delete,
                         hrm_get, hrm_json, hrm_post, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()


def main(page: Page):
    return page.locator("main")


def tab(page: Page, name: str):
    main(page).get_by_role("button", name=name, exact=True).click()


@pytest.fixture
def person(auth_page: Page):
    le = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Отчёты+ {stamp()}")
    yield {**emp, "le": le}
    for d in items(hrm_json(hrm_get(auth_page, "/doc-templates/generated", {"employee_id": emp["id"]}))):
        hrm_delete(auth_page, f"/doc-templates/generated/{d['id']}")
    delete_employee(auth_page, emp["id"])
    for t in items(hrm_json(hrm_get(auth_page, "/doc-templates/"))):
        if t["name"].startswith(QA_PREFIX):
            hrm_delete(auth_page, f"/doc-templates/{t['id']}")
    for j in hrm_json(hrm_get(auth_page, "/doc-templates/journals")):
        if j["name"].startswith(QA_PREFIX) and not j.get("is_system"):
            hrm_delete(auth_page, f"/doc-templates/journals/{j['id']}")
    for w in hrm_json(hrm_get(auth_page, "/org/workplaces")):
        if w["name"].startswith(QA_PREFIX):
            hrm_delete(auth_page, f"/org/workplaces/{w['id']}")
    clean_up(auth_page)


# Кейс: «Отчёт по журналам» — документ своего журнала, фильтр по юрлицу = /reports/contract-numbers
@pytest.mark.writes_data
def test_journal_report(auth_page: Page, person):
    journal = hrm_json(hrm_post(auth_page, "/doc-templates/journals", {
        "name": f"{QA_PREFIX} Журнал отчёт {stamp()}", "legal_entity_id": person["le"]["id"], "prefix": "QR",
        "separator": "/", "start_value": 1, "step": 1, "prefix_position": "start", "is_contract": False}))
    wp = hrm_json(hrm_post(auth_page, "/org/workplaces", {"name": f"{QA_PREFIX} Место отчёт {stamp()}"}))
    tpl = hrm_json(hrm_post(auth_page, "/doc-templates/", {
        "name": f"{QA_PREFIX} Шаблон отчёт {stamp()}", "doc_type": "referral_form", "template_kind": "html",
        "body_template": "<p>QA</p>", "variables": [], "numbering_enabled": True, "numbering_journal_id": journal["id"],
        "applicability": "by_workplace", "workplace_ids": [wp["id"]], "position_ids": [], "journal_ids": [], "print_copies": 1}))
    doc = hrm_json(hrm_post(auth_page, f"/doc-templates/{tpl['id']}/generate", {"employee_id": person["id"]}))
    report = hrm_json(hrm_get(auth_page, "/reports/contract-numbers", {"legal_entity_id": person["le"]["id"]}))
    assert [(r["doc_number"], r["journal_name"], r["template_name"]) for r in report["rows"]] == \
        [(doc["doc_number"], journal["name"], tpl["name"])], report
    open_hrm(auth_page, "/admin/reports")
    tab(auth_page, "Отчёт по журналам")
    for th in ("Номер документа", "Имя", "Фамилия", "ИДНП", "Юрлицо", "Шаблон", "Журнал", "Дата", "Статус"):
        expect(main(auth_page).locator("th").filter(has_text=th).first).to_be_visible()
    main(auth_page).get_by_role("button", name="Все юрлица", exact=True).click()
    with auth_page.expect_response(lambda r: "/reports/contract-numbers" in r.url and f"legal_entity_id={person['le']['id']}" in r.url):
        auth_page.get_by_role("option", name=person["le"]["name"], exact=True).click()
    rows = main(auth_page).locator("tbody tr")
    expect(rows).to_have_count(1)
    for text in (doc["doc_number"], person["telegram_first_name"], journal["name"], tpl["name"], "Сгенерирован"):
        expect(rows.first).to_contain_text(text)


# Кейс: Вкладка «Онбординг» в отчётах = /onboard/dashboard
def test_onboarding_report_tab(auth_page: Page):
    dash = hrm_json(hrm_get(auth_page, "/onboard/dashboard"))
    open_hrm(auth_page, "/admin/reports")
    tab(auth_page, "Онбординг")
    for value, label in ((dash["active_count"], "Активных онбордингов"), (dash["completed_this_month"], "Завершено в этом месяце"),
                         (dash["overdue_task_count"], "Просроченных задач")):
        expect(main(auth_page)).to_contain_text(f"{value}\n{label}", use_inner_text=True)
    if not dash["overdue_tasks"]:
        expect(main(auth_page)).to_contain_text("Нет просроченных задач")


# Кейс: Выгрузки отчётов «Обзор» и «Текучесть» — Excel и PDF скачиваются
@pytest.mark.parametrize("tab_name", ["Обзор", "Текучесть"])
def test_report_downloads(auth_page: Page, tab_name):
    open_hrm(auth_page, "/admin/reports")
    tab(auth_page, tab_name)
    with auth_page.expect_download(timeout=60_000) as xl:
        main(auth_page).get_by_role("button", name="Excel", exact=True).first.click()
    book = openpyxl.load_workbook(io.BytesIO(open(xl.value.path(), "rb").read()))  # временный файл без расширения
    assert xl.value.suggested_filename.endswith(".xlsx") and book.active.max_row >= 1, xl.value.suggested_filename
    with auth_page.expect_download(timeout=60_000) as pdf:
        main(auth_page).get_by_role("button", name="PDF", exact=True).first.click()
    assert pdf.value.suggested_filename.endswith(".pdf") and open(pdf.value.path(), "rb").read(5) == b"%PDF-", \
        pdf.value.suggested_filename


# Кейс: Личные настройки уведомлений — «День рождения · В приложении» выключить и включить обратно = /notifications/preferences
@pytest.mark.writes_data
def test_notification_preferences(auth_page: Page):
    open_hrm(auth_page, "/admin/notifications")
    tab(auth_page, "Настройки")
    expect(main(auth_page)).to_contain_text("Настройте, какие уведомления вы хотите получать и по каким каналам")
    row = main(auth_page).locator("tbody tr").filter(has_text="День рождения")
    in_app = row.locator("td").nth(1).locator("button")  # переключатель без aria: «вкл» = класс bg-brand-500
    was_on = "bg-brand-500" in (in_app.get_attribute("class") or "")
    try:
        with auth_page.expect_response(lambda r: "/notifications/preferences" in r.url and r.request.method == "PUT") as put:
            in_app.click()
        body = put.value.request.post_data_json
        assert put.value.ok and (body["event_type"], body["channel"], body["is_enabled"]) == ("birthday", "in_app", not was_on), body
        prefs = hrm_json(hrm_get(auth_page, "/notifications/preferences"))
        assert any(p["event_type"] == "birthday" and p["channel"] == "in_app" and p["is_enabled"] is (not was_on) for p in prefs), prefs
    finally:
        with auth_page.expect_response(lambda r: "/notifications/preferences" in r.url and r.request.method == "PUT") as back:
            in_app.click()
        assert back.value.request.post_data_json["is_enabled"] is was_on


# Кейс: «Налоговые льготы» — личная льгота с даты и ребёнок-иждивенец сохраняются = API, «Всего льгот», запись в «Изменения раздела»
@pytest.mark.writes_data
def test_tax_exemptions(auth_page: Page, person):
    rel = next(r for r in items(hrm_json(hrm_get(auth_page, "/family/relations"))) if r.get("code") == "child"
               or r.get("name_ru") == "Ребёнок")
    child = f"{QA_PREFIX} Ребёнок {stamp()}"
    hrm_json(hrm_post(auth_page, f"/employees/{person['id']}/family", {"relation_id": rel["id"], "full_name": child,
                                                                       "birth_date": f"{TODAY.year - 8}-03-10"}))
    since = TODAY.replace(day=1)
    open_hrm(auth_page, "/admin/employees")
    main(auth_page).get_by_role("button").filter(has_text=person["telegram_first_name"]).first.click()
    main(auth_page).get_by_role("button", name="Семья", exact=True).last.click()
    box = main(auth_page).locator("div").filter(has_text="Налоговые льготы").filter(
        has=auth_page.get_by_text("Личная льгота сотрудника", exact=True)).last
    expect(box).to_contain_text("Всего льгот: 0")
    expect(box).to_contain_text("Cererea privind acordarea scutirilor")
    box.get_by_role("button", name="Не заявлена", exact=True).click()
    auth_page.get_by_role("option", name="Личная льгота", exact=True).click()
    date_field(box, "Льготы с даты").fill(str(since))
    member = box.locator("div").filter(has_text=child).filter(has=auth_page.get_by_role("button", name="Без льготы")).last
    member.get_by_role("button", name="Без льготы", exact=True).click()
    auth_page.get_by_role("option", name="Иждивенец", exact=True).click()
    field(box, "Примечание").fill(f"{QA_PREFIX} заявление от {TODAY:%d.%m.%Y}")
    with auth_page.expect_response(lambda r: f"/employees/{person['id']}/tax-exemptions" in r.url and r.request.method == "PUT") as put:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert put.value.ok, put.value.text()
    got = hrm_json(hrm_get(auth_page, f"/employees/{person['id']}/tax-exemptions"))
    assert (got["personal"], got["granted_from"]) == ("personal", str(since)), got
    assert [(m["full_name"], m["tax_exemption"]) for m in got["members"]] == [(child, "dependent")], got["members"]
    assert got["summary"]["dependents_count"] == 1, got["summary"]
    expect(box).to_contain_text(re.compile(r"Всего льгот: [12]"))
    expect(main(auth_page)).to_contain_text("Изменения раздела")
