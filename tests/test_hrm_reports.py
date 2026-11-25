"""Блок 63. HRM — отчёты (/admin/reports), уведомления (/admin/notifications), журнал действий (/admin/audit).

Снято 03.11.2026 в видимом MCP-браузере:
- «Отчёты»: вкладки «Обзор / Дни рождения / Онбординг / Текучесть / Отчёт по журналам / Конструктор
  отчётов»; «Обзор» — «Штат сотрудников» (Excel, PDF): «По отделам», «По типу занятости», «По юрлицам»,
  «По должностям» = /reports/headcount; «Дни рождения» — месяцы «Все, Янв … Дек», «Дни рождения — <Месяц>»,
  колонки «Сотрудник, IDNP, Должность, Отдел, Дата рождения, Возраст» = /reports/birthdays?month=;
  «Текучесть» — год, «Штат на начало / Принято / Уволено / Текучесть / Нетто», «Принято (N)» со списком
  = /reports/turnover; «Конструктор отчётов» — колонки «ФИО, Департамент, Должность, Статус занятости»
  (значение — сырой ключ «active»), «Найдено: N»;
- «Уведомления»: вкладки «Уведомления / Правила / Настройки», фильтр «Все / Непрочитанные / Прочитанные»,
  «Прочитать все» (не жмём — вся лента учётки); у уведомления «Прочитать» и «Удалить» (PUT …/read,
  DELETE — в архив); действия тестов (нарушения, заявки) сами пишут уведомления тест-учётке, тест
  читает и убирает своё; «Правила» → «Создать правило» → «Новое правило»: «Название *», «Событие»
  (День рождения / Дата приёма / Годовщина / Окончание паспорта / Дата увольнения), «Каналы» (В
  приложении / Email), «Тип получателя» (HR-отдел / По роли / Конкретный пользователь / …), «Время»,
  «Шаблон сообщения», «Обязательное», «Активное» — тест заводит правило только неактивным, только «В
  приложении» и только себе (активное слало бы живым людям) и удаляет его;
- «Журнал действий»: фильтры-списки «Все пользователи / Все разделы / Все действия», «с / по», колонки
  «Когда, Кто, Действие, Объект, Раздел, Подробности» = /audit/journal; «Назад / Дальше».
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (screen_tab, ME, QA_PREFIX, clean_up, create_employee, create_legal_entity, delete_employee, dropdown,
                         field, hrm_delete, hrm_get, hrm_json, hrm_post, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()
MONTH_NAMES = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
               "Ноябрь", "Декабрь"]
MONTHS = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]


def main(page: Page):
    return page.locator("main")


def tab(page: Page, name: str):
    screen_tab(page, name).click()


def modal(page: Page, heading: str):
    return page.locator("div.fixed.inset-0:visible").filter(
        has=page.get_by_role("heading", name=heading, exact=True)).last


@pytest.fixture
def person(auth_page: Page):
    """Свой сотрудник на своём юрлице, принят сегодня, родился в этом месяце 1990 года."""
    le = create_legal_entity(auth_page)
    born = TODAY.replace(year=1990, day=min(TODAY.day, 28))
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Отчёт {stamp()}", birth_date=str(born))
    yield {**emp, "le": le, "born": born}
    delete_employee(auth_page, emp["id"])
    clean_up(auth_page)


def section(page: Page, title: str):
    """Блок отчёта «По …» — заголовок и строки под ним."""
    return main(page).locator("div").filter(has=page.get_by_text(title, exact=True)).last


# ───────────────────────── отчёты ─────────────────────────

# Кейс: «Обзор» — штат по отделам, типу занятости, юрлицам и должностям = /reports/headcount; свой сотрудник — на своём юрлице
@pytest.mark.writes_data
def test_headcount_overview(auth_page: Page, person):
    report = hrm_json(hrm_get(auth_page, "/reports/headcount"))
    mine = [x for x in report["by_legal_entity"] if x["legal_entity_id"] == person["le"]["id"]]
    assert [x["count"] for x in mine] == [1], report["by_legal_entity"]
    open_hrm(auth_page, "/admin/reports")
    expect(main(auth_page)).to_contain_text("Штат сотрудников")
    for name in ("Excel", "PDF"):
        expect(main(auth_page).get_by_role("button", name=name, exact=True).first).to_be_visible()
    by_le = section(auth_page, "По юрлицам")
    for row in report["by_legal_entity"]:
        expect(by_le).to_contain_text(row["legal_entity"])
    expect(by_le.locator("div").filter(has_text=person["le"]["name"]).last.locator("xpath=..")).to_contain_text("1")
    by_dep = section(auth_page, "По отделам")
    for row in report["by_department"]:
        expect(by_dep).to_contain_text(row["department"])
        expect(by_dep).to_contain_text(str(row["count"]))


# Кейс: «Дни рождения» — месяц по умолчанию текущий, свой сотрудник с датой и возрастом = /reports/birthdays
@pytest.mark.writes_data
def test_birthdays_report(auth_page: Page, person):
    report = hrm_json(hrm_get(auth_page, "/reports/birthdays", {"month": TODAY.month}))
    mine = [x for x in report["items"] if x.get("id", x.get("employee_id")) == person["id"]]
    assert len(mine) == 1, report
    open_hrm(auth_page, "/admin/reports")
    tab(auth_page, "Дни рождения")
    for m in ["Все"] + MONTHS:
        expect(main(auth_page).get_by_role("button", name=m, exact=True)).to_be_visible()
    expect(main(auth_page)).to_contain_text(f"Дни рождения — {MONTH_NAMES[TODAY.month - 1]} ({report['total']})")
    rows = main(auth_page).locator("tbody tr")
    expect(rows).to_have_count(report["total"])
    row = rows.filter(has_text=person["telegram_first_name"])
    expect(row).to_contain_text(person["born"].strftime("%d.%m.%Y"))
    age = TODAY.year - 1990 - ((TODAY.month, TODAY.day) < (person["born"].month, person["born"].day))
    expect(row).to_contain_text(str(age))
    other = MONTHS[(TODAY.month % 12)]  # следующий месяц — своего сотрудника там нет
    main(auth_page).get_by_role("button", name=other, exact=True).click()
    expect(rows.filter(has_text=person["telegram_first_name"])).to_have_count(0)


# Кейс: «Текучесть» — свой сотрудник, принятый сегодня, в «Принято (N)», плитки = /reports/turnover
@pytest.mark.writes_data
def test_turnover_report(auth_page: Page, person):
    report = hrm_json(hrm_get(auth_page, "/reports/turnover", {"year": TODAY.year}))
    assert person["id"] in [h["id"] for h in report["hired"]], report["hired"]
    open_hrm(auth_page, "/admin/reports")
    tab(auth_page, "Текучесть")
    for label, value in (("Штат на начало", report["headcount_start"]), ("Принято", report["total_hires"]),
                         ("Уволено", report["total_dismissals"])):
        expect(main(auth_page)).to_contain_text(f"{value}\n{label}", use_inner_text=True)
    expect(main(auth_page)).to_contain_text(f"Принято ({len(report['hired'])})")
    row = main(auth_page).locator("tr").filter(has_text=person["telegram_first_name"])
    expect(row).to_contain_text(TODAY.strftime("%d.%m.%Y"))


# Кейс: «Конструктор отчётов» — «Найдено: N» = активные сотрудники, свой сотрудник в таблице
@pytest.mark.writes_data
def test_report_builder(auth_page: Page, person):
    active = items(hrm_json(hrm_get(auth_page, "/employees", {"status_filter": "active", "limit": 1000})))
    open_hrm(auth_page, "/admin/reports")
    tab(auth_page, "Конструктор отчётов")
    expect(main(auth_page)).to_contain_text("Колонки (4)")
    expect(main(auth_page)).to_contain_text(f"Найдено: {len(active)}")
    expect(main(auth_page).locator("tbody tr")).to_have_count(len(active))
    expect(main(auth_page).locator("tbody tr").filter(has_text=person["telegram_first_name"])).to_have_count(1)


# ───────────────────────── уведомления ─────────────────────────

# Кейс: Своё действие пишет уведомление — «Прочитать» уменьшает счётчик, «Удалить» убирает из ленты
@pytest.mark.writes_data
def test_notification_read_and_delete(auth_page: Page, person):
    code = "qa_notify_" + re.sub(r"\D", "", stamp())[-9:]
    hrm_json(hrm_post(auth_page, "/discipline/violations/", {
        "employee_id": person["id"], "violation_type": code, "severity": "critical",
        "description": f"{QA_PREFIX} для уведомления", "incident_date": str(TODAY)}))
    note = next(n for n in hrm_json(hrm_get(auth_page, "/notifications/", {"limit": 20})) if code in n["body"])
    assert (note["title"], note["event_type"], note["is_read"]) == ("Зафиксировано нарушение", "violation_created", False), note
    unread = hrm_json(hrm_get(auth_page, "/notifications/count"))["unread"]

    open_hrm(auth_page, "/admin/notifications")
    card = main(auth_page).locator("div").filter(has_text=f"Тип: {code}").filter(
        has=auth_page.get_by_role("button", name="Удалить")).last
    expect(card).to_contain_text("Зафиксировано нарушение")
    with auth_page.expect_response(lambda r: f"/notifications/{note['id']}/read" in r.url) as rd:
        card.get_by_role("button", name="Прочитать").click()
    assert rd.value.ok, rd.value.text()
    assert hrm_json(hrm_get(auth_page, "/notifications/count"))["unread"] == unread - 1
    expect(card.get_by_role("button", name="Прочитать")).to_have_count(0)
    with auth_page.expect_response(lambda r: f"/notifications/{note['id']}" in r.url and r.request.method == "DELETE") as gone:
        card.get_by_role("button", name="Удалить").click()
        confirm = auth_page.locator("div.fixed.inset-0:visible").get_by_role("button", name=re.compile("^(Удалить|Да)$"))
        if confirm.count():
            confirm.last.click()
    assert gone.value.ok, gone.value.text()
    expect(main(auth_page).get_by_text(f"Тип: {code}")).to_have_count(0)
    assert code not in " ".join(n["body"] for n in hrm_json(hrm_get(auth_page, "/notifications/", {"limit": 50})))


# Кейс: Правило уведомлений — неактивное, только «В приложении», получатель — сама тест-учётка; форма = API → удалить
@pytest.mark.writes_data
def test_notification_rule(auth_page: Page):
    name = f"{QA_PREFIX} Правило {stamp()}"
    open_hrm(auth_page, "/admin/notifications")
    tab(auth_page, "Правила")
    expect(main(auth_page)).to_contain_text("Правила определяют, кому и когда отправляются уведомления")
    main(auth_page).get_by_role("button", name="Создать правило").click()
    box = modal(auth_page, "Новое правило")
    rule_id = None
    try:
        field(box, "Название *").fill(name)
        dropdown(auth_page, box, "Событие", "Дата увольнения", starts=True)
        expect(box.get_by_role("checkbox", name="В приложении")).to_be_checked()
        expect(box.get_by_role("checkbox", name="Email")).not_to_be_checked()  # писем не шлём
        dropdown(auth_page, box, "Тип получателя", "Конкретный пользователь")
        field(box, "Значение получателя").click()  # вариант — логин и под ним email
        auth_page.get_by_role("option").filter(has_text="maxim.turcan1@gmail.com").click()
        auth_page.keyboard.press("Escape")  # мультивыбор: список остаётся открытым и перекрывает галочки
        expect(box).to_be_visible()
        box.get_by_role("checkbox", name="Активное").uncheck()  # активное слало бы уведомления по живым сотрудникам
        with auth_page.expect_response(lambda r: r.url.endswith("/notifications/rules") and r.request.method == "POST") as resp:
            box.get_by_role("button", name="Создать", exact=True).click()
        assert resp.value.ok, resp.value.text()
        rule_id = resp.value.json()["id"]
        rule = next(r for r in hrm_json(hrm_get(auth_page, "/notifications/rules")) if r["id"] == rule_id)
        assert (rule["name"], rule["event_type"], rule["recipient_type"], str(rule["recipient_value"]), rule["is_active"]) == \
            (name, "termination_date", "specific_user", str(ME), False), rule
        assert "email" not in str(rule.get("channel") or rule.get("channels")), rule
        row = main(auth_page).locator("tbody tr").filter(has_text=name)
        expect(row).to_contain_text("Дата увольнения")
        with auth_page.expect_response(lambda r: f"/notifications/rules/{rule_id}" in r.url and r.request.method == "DELETE") as gone:
            row.get_by_role("button", name="Удалить").click()
            confirm = auth_page.locator("div.fixed.inset-0:visible").get_by_role("button", name=re.compile("^(Удалить|Да)$"))
            if confirm.count():
                confirm.last.click()
        assert gone.value.ok, gone.value.text()
        rule_id = None
        expect(row).to_have_count(0)
    finally:
        if rule_id:
            hrm_delete(auth_page, f"/notifications/rules/{rule_id}")


# ───────────────────────── журнал действий ─────────────────────────

# Кейс: «Журнал действий» видит действие теста — «Сотрудник создан: <имя>», фильтры раздела и действия = /audit/journal
@pytest.mark.writes_data
def test_audit_journal(auth_page: Page, person):
    api = items(hrm_json(hrm_get(auth_page, "/audit/journal", {"section": "employees", "action": "created", "limit": 20})))
    entry = next(e for e in api if e["entity_id"] == person["id"])
    assert entry["summary"].startswith(f"Сотрудник создан: {person['telegram_first_name']}"), entry
    open_hrm(auth_page, "/admin/audit")
    for th in ("Когда", "Кто", "Действие", "Объект", "Раздел", "Подробности"):
        expect(main(auth_page).locator("th").filter(has_text=th).first).to_be_visible()
    def select_with(option: str):
        return main(auth_page).locator("select").filter(has=auth_page.locator("option", has_text=option)).first

    with auth_page.expect_response(lambda r: "/audit/journal" in r.url and "section=employees" in r.url):
        select_with("Сотрудники").select_option(label="Сотрудники")
    with auth_page.expect_response(lambda r: "/audit/journal" in r.url and "action=created" in r.url):
        select_with("Создание").select_option(label="Создание")
    row = main(auth_page).locator("tbody tr").filter(has_text=f"Сотрудник создан: {person['telegram_first_name']}")
    expect(row).to_have_count(1)
    expect(row).to_contain_text(entry["performer_name"])
    expect(row).to_contain_text("Создание")
    expect(row).to_contain_text("Сотрудники")
    actions = set(main(auth_page).locator("tbody tr td:nth-child(4)").all_inner_texts())
    assert actions == {"Создание"}, actions
