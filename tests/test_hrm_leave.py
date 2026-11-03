"""Блок 55. HRM — отсутствия (/admin/leave): политики, заявки, согласование.

Снято 02.11.2026 (в видимом MCP-браузере и по справке help.bord.one/hrm/otsutstviya):
- вкладки «Заявки, Таймлайн, Балансы, Политики, Цепочки согласования»; на «Заявках» —
  «Новая заявка», статусы «Все (N) / Ожидает / Одобрено / Отклонено / Отменено», таблица
  «Сотрудник, Тип отсутствия, Период, Дней, Статус, Подал, Кто рассмотрел» = GET /leave/requests;
- «Политики» = GET /leave/policies; «Новая политика» → окно: «Название *», «Тип», «Метод
  начисления», «Дней в год», «Цвет», «Обозначение в табеле», «Категория для табеля», галочки
  «Оплачивается», «Требует согласования», «В использовании» → POST; в строке «Редактировать /
  Удалить» («Удалить политику? … не используется ни в одной заявке» → «Удалить безвозвратно»);
  использованную политику удалить нельзя;
- «Новая заявка» → «Новая заявка на отсутствие»: «Сотрудник», «Тип отсутствия *», «Начало *»,
  «Конец *», «Причина» → «Создать заявку» (POST /leave/requests?employee_id=<id>); без согласования — сразу одобрена, с согласованием —
  «Ожидает» → карточка «Заявка на отсутствие» («Ожидает рассмотрения»): «Подтвердить» /
  «Отклонить» (окно «Причина отклонения:» → «OK») / «Удалить заявку»; «Подтвердить» → «Одобрить заявку?» → «Да»;
- одно отсутствие на день: пересекающаяся заявка не создаётся, система называет мешающую.
Не жмём «Балансы → Обновить» (инициализирует балансы всей организации).
Свои политики, сотрудник и заявки (даты в следующем году) удаляются после теста.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (QA_PREFIX, clean_up, create_employee, create_legal_entity, date_field, delete_employee,
                         dropdown, field, hrm_delete, hrm_get, hrm_json, hrm_post, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth

YEAR = dt.date.today().year + 1  # заявки — в следующем году, мимо дашборда и текущего табеля
TABS = ["Заявки", "Таймлайн", "Балансы", "Политики", "Цепочки согласования"]


def main(page: Page):
    return page.locator("main")


def tab(page: Page, name: str):
    main(page).get_by_role("button", name=name, exact=True).first.click()


def row(page: Page, text: str):
    return main(page).locator("tr").filter(has_text=text).first


def make_policy(page: Page, approval: bool) -> dict:
    return hrm_json(hrm_post(page, "/leave/policies", {
        "name": f"{QA_PREFIX} Отпуск {'с согласованием' if approval else 'без согласования'} {stamp()}",
        "policy_type": "accumulative", "accrual_method": "annual", "accrual_amount": 20, "requires_approval": approval,
        "is_paid": True, "timesheet_code": "QA", "color": "#22c55e"}))


@pytest.fixture
def staff(auth_page: Page):
    le = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Отпускник {stamp()}")
    data = {"le": le, "emp": emp, "policies": []}
    yield data
    for req in items(hrm_json(hrm_get(auth_page, "/leave/requests", {"limit": 500}))):
        if req.get("employee_id") == emp["id"]:
            hrm_delete(auth_page, f"/leave/requests/{req['id']}")
    delete_employee(auth_page, emp["id"])
    for pol in items(hrm_json(hrm_get(auth_page, "/leave/policies"))):
        if pol["name"].startswith(QA_PREFIX):
            hrm_delete(auth_page, f"/leave/policies/{pol['id']}")
    clean_up(auth_page)


def new_request(page: Page, emp: dict, policy: dict, start: dt.date, end: dt.date, reason: str = ""):
    tab(page, "Заявки")
    main(page).get_by_role("button", name="Новая заявка").click()
    box = page.locator("div.fixed:visible").filter(has_text="Новая заявка на отсутствие").last
    dropdown(page, box, "Сотрудник", emp["telegram_first_name"])
    dropdown(page, box, "Тип отсутствия *", policy["name"])
    date_field(box, "Начало *").fill(str(start))
    date_field(box, "Конец *").fill(str(end))
    if reason:
        field(box, "Причина").fill(reason)
    return box


# Кейс: Вкладки и таблица заявок = API
def test_tabs_and_requests(auth_page: Page):
    with auth_page.expect_response(lambda r: "/api/hrm/leave/requests" in r.url and r.request.method == "GET") as resp:
        open_hrm(auth_page, "/admin/leave")
    reqs = items(resp.value.json())
    for name in TABS:
        expect(main(auth_page).get_by_role("button", name=name, exact=True).first).to_be_visible()
    expect(main(auth_page).get_by_role("button", name=f"Все ({len(reqs)})")).to_be_visible()
    if not reqs:
        expect(main(auth_page)).to_contain_text("Нет заявок")
    with auth_page.expect_response(lambda r: "/api/hrm/leave/policies" in r.url) as pol:
        tab(auth_page, "Политики")
    for p in items(pol.value.json()):
        expect(row(auth_page, p["name"])).to_be_visible()


# Кейс: Политика — создать через форму и удалить
@pytest.mark.writes_data
def test_policy_create_and_delete(auth_page: Page, staff):
    name = f"{QA_PREFIX} Политика {stamp()}"
    open_hrm(auth_page, "/admin/leave")
    tab(auth_page, "Политики")
    main(auth_page).get_by_role("button", name="Новая политика").click()
    box = auth_page.locator("div.fixed:visible").filter(has_text="Новая политика").last
    field(box, "Название *").fill(name)
    expect(field(box, "Тип")).to_contain_text("Накопительный (ежегодный)")
    field(box, "Дней в год").fill("20")
    field(box, "Обозначение в табеле").fill("QA")
    box.get_by_label("Требует согласования").check()
    with auth_page.expect_response(lambda r: r.url.endswith("/api/hrm/leave/policies") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    pol = resp.value.json()
    assert (pol["name"], pol["accrual_amount"], pol["timesheet_code"], pol["requires_approval"]) == (name, 20, "QA", True), pol
    line = row(auth_page, name)
    expect(line).to_contain_text("Накопительный")
    expect(line).to_contain_text("20")
    line.get_by_role("button", name="Удалить").click()
    dlg = auth_page.locator("div.fixed:visible").filter(has_text="Удалить политику?").last
    expect(dlg).to_contain_text(f"Политика «{name}» не используется ни в одной заявке")
    with auth_page.expect_response(lambda r: f"/leave/policies/{pol['id']}" in r.url and r.request.method == "DELETE") as gone:
        dlg.get_by_role("button", name="Удалить безвозвратно").click()
    assert gone.value.ok, gone.value.text()
    expect(row(auth_page, name)).to_have_count(0)


# Кейс: Заявка без согласования сразу одобрена; пересекающаяся — не создаётся
@pytest.mark.writes_data
def test_request_auto_approved_and_overlap(auth_page: Page, staff):
    policy = make_policy(auth_page, approval=False)
    emp = staff["emp"]
    start, end = dt.date(YEAR, 3, 2), dt.date(YEAR, 3, 4)
    open_hrm(auth_page, "/admin/leave")
    box = new_request(auth_page, emp, policy, start, end, f"{QA_PREFIX} отпуск")
    with auth_page.expect_response(lambda r: r.url.split("?")[0].endswith("/api/hrm/leave/requests") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать заявку", exact=True).click()
    assert resp.value.ok, resp.value.text()
    req = resp.value.json()
    assert (req["status"], req["start_date"], req["end_date"]) == ("approved", str(start), str(end)), req
    line = row(auth_page, emp["telegram_first_name"])
    expect(line).to_contain_text(policy["name"])
    expect(line).to_contain_text("Одобрено")
    expect(line).to_contain_text("3")  # дней

    # тот же сотрудник, день внутри периода — отказ с названием мешающего отсутствия
    box = new_request(auth_page, emp, policy, dt.date(YEAR, 3, 3), dt.date(YEAR, 3, 3))
    with auth_page.expect_response(lambda r: r.url.split("?")[0].endswith("/api/hrm/leave/requests") and r.request.method == "POST") as again:
        box.get_by_role("button", name="Создать заявку", exact=True).click()
    assert again.value.status in (400, 409, 422), again.value.status
    detail = again.value.json().get("detail")
    message = detail if isinstance(detail, str) else str(detail)
    assert policy["name"] in message or f"{YEAR}-03" in message or "03." in message, message  # называет мешающее отсутствие
    expect(auth_page.get_by_text(message[:40]).first).to_be_visible()
    assert len([r for r in items(hrm_json(hrm_get(auth_page, "/leave/requests", {"limit": 500}))) if r.get("employee_id") == emp["id"]]) == 1


# Кейс: Заявка с согласованием — «Отклонить» с причиной и «Подтвердить»
@pytest.mark.writes_data
def test_request_reject_and_approve(auth_page: Page, staff):
    policy = make_policy(auth_page, approval=True)
    emp = staff["emp"]
    open_hrm(auth_page, "/admin/leave")
    results = {}
    for decision, start in (("Отклонить", dt.date(YEAR, 4, 6)), ("Подтвердить", dt.date(YEAR, 4, 13))):
        open_hrm(auth_page, "/admin/leave")  # карточка прошлой заявки по Escape не закрывается
        box = new_request(auth_page, emp, policy, start, start + dt.timedelta(days=1))
        with auth_page.expect_response(lambda r: r.url.split("?")[0].endswith("/api/hrm/leave/requests") and r.request.method == "POST") as resp:
            box.get_by_role("button", name="Создать заявку", exact=True).click()
        assert resp.value.ok, resp.value.text()
        req = resp.value.json()
        assert req["status"] == "pending", req
        line = main(auth_page).locator("tr").filter(has_text=emp["telegram_first_name"]).filter(has_text="Ожидает").first
        expect(line).to_be_visible()
        line.click()
        card = auth_page.locator("div.fixed:visible").filter(has_text="Заявка на отсутствие").last
        expect(card).to_contain_text("Ожидает рассмотрения")
        expect(card).to_contain_text(f"{start:%d.%m.%Y} – {start + dt.timedelta(days=1):%d.%m.%Y}")
        action = "reject" if decision == "Отклонить" else "approve"
        with auth_page.expect_response(lambda r: f"/leave/requests/{req['id']}/{action}" in r.url) as dec:
            card.get_by_role("button", name=decision, exact=True).click()
            if decision == "Подтвердить":  # окно «Одобрить заявку?» → «Да»
                auth_page.locator("div.fixed:visible").filter(has_text="Одобрить заявку?").last.get_by_role(
                    "button", name="Да", exact=True).click()
            if decision == "Отклонить":  # окно «Причина отклонения:» → «OK»
                prompt = auth_page.locator("div.fixed:visible").filter(has_text="Причина отклонения:").last
                prompt.locator("input, textarea").first.fill(f"{QA_PREFIX} не согласовано")
                prompt.get_by_role("button", name="OK", exact=True).click()
        assert dec.value.ok, dec.value.text()
        results[decision] = hrm_json(hrm_get(auth_page, f"/leave/requests/{req['id']}"))
    assert results["Отклонить"]["status"] == "rejected", results["Отклонить"]
    assert f"{QA_PREFIX} не согласовано" in str(results["Отклонить"]), results["Отклонить"]
    assert results["Подтвердить"]["status"] == "approved", results["Подтвердить"]
    open_hrm(auth_page, "/admin/leave")
    mine = main(auth_page).locator("tr").filter(has_text=emp["telegram_first_name"])
    expect(mine.filter(has_text="Отклонено")).to_have_count(1)
    expect(mine.filter(has_text="Одобрено")).to_have_count(1)
