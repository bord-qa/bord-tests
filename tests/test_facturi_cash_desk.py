"""Блок 86. Facturi — «Касса» (/admin/cash-desk).

Снято 26.11.2026:
- вкладки экрана в шапке: «Мои кассы», «Заметки», «Сдать в кассу», «Наличные по кассам»,
  «Заказы без оплаты», «Дополнить заказы», «Транспорт», «Исправленные», «Кто ведёт кассы»,
  «Терминалы», «Карты: ждут решения», «Свои поля», «Транспорт: настройки» — каждая
  грузит свой GET /cash-desk/* (или /trip-costs/*, /custom-fields);
- касса = банковский счёт type=cash. Сотрудник видит в «Мои кассы» только кассы, где он
  отмечен в «Кто ведёт кассы» (выпадашка на сотрудника: «Нет доступа» / «Ведёт кассу» /
  «Только смотрит» = PUT /cash-desk/access/<id> {user_ids, viewer_ids});
- карточка кассы: «+ Поступление наличных» / «− Выдача наличных» — форма прямо на экране
  (Покупатель / Заказ, Вид операции = статьи нужного типа, Сумма, дата, Основание),
  «Записать» = POST /cash-desk/accounts/<id>/operations; «Пересчёт на сегодня»
  (В кассе, Примечание) = POST …/count, ответ несёт computed_amount и gap;
- «Исправить» в строке операции = POST …/operations/<id>/correct (с причиной): новая
  операция correction_of_id, старая скрывается; правка попадает в «Исправленные» до
  отметки проверки (POST /cash-desk/corrections/<id>/reviewed);
- кассу с операциями не удалить (409) — уборка удаляет операции (и исходную, скрытую
  исправлением), потом кассу.
Не трогаем: «Свести автоматически» (сводит карты всей организации), «Транспорт:
настройки» (тариф организации), «Добавить недостающие виды «как в 1С»» (заводит статьи).
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.facturi import QA_PREFIX, fx_delete, fx_get, fx_json, fx_post, fx_put, open_fx, stamp

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/cash-desk"
QA_USER_NAME = "maxim.turcan1"


def head(page: Page):
    return page.locator("header.sticky")


def tab(page: Page, name: str):
    head(page).get_by_role("button", name=name, exact=True).click()


def money(amount: float) -> re.Pattern:
    """«1 200,00 MDL» — разделитель тысяч неразрывный пробел."""
    whole, frac = f"{abs(amount):,.2f}".split(".")
    return re.compile(r"\s".join(re.escape(p) for p in whole.split(",")) + f",{frac}" + r"\sMDL")


def my_id(page: Page) -> int:
    return fx_json(fx_get(page, "/auth/me"))["user"]["id"]


class Cash:
    """Своя касса со своими статьями прихода и расхода; уборка — операции, касса, статьи."""

    def __init__(self, page: Page):
        self.page = page
        tag = stamp()
        self.acc = fx_json(fx_post(page, "/bank-accounts/", {"name": f"{QA_PREFIX} Касса {tag}",
                                                            "type": "cash", "currency": "MDL"}))
        self.income = fx_json(fx_post(page, "/categories/", {"name": f"{QA_PREFIX} Касса приход {tag}",
                                                            "type": "income", "pls_type": 1100}))
        self.expense = fx_json(fx_post(page, "/categories/", {"name": f"{QA_PREFIX} Касса расход {tag}",
                                                             "type": "expense", "pls_type": 2200}))
        self.op_ids: set[int] = set()

    @property
    def id(self) -> int:
        return self.acc["id"]

    def grant(self, manage=True):
        body = {"user_ids": [], "viewer_ids": []}
        body["user_ids" if manage else "viewer_ids"] = [my_id(self.page)]
        fx_json(fx_put(self.page, f"/cash-desk/access/{self.id}", body))

    def operations(self) -> list[dict]:
        data = fx_json(fx_get(self.page, f"/cash-desk/accounts/{self.id}/operations"))
        items = data["items"] if isinstance(data, dict) else data
        self.op_ids |= {o["id"] for o in items}
        return items

    def access(self) -> dict:
        return next(a for a in fx_json(fx_get(self.page, "/cash-desk/access"))["accounts"] if a["id"] == self.id)

    def cleanup(self):
        p = self.page
        try:
            self.operations()
        except AssertionError:
            pass
        for c in fx_json(fx_get(p, "/cash-desk/corrections", {"only_unreviewed": "false"}))["items"]:
            if c["account_id"] == self.id:
                self.op_ids |= {c["before"]["id"], c["after"]["id"]}
        for op_id in sorted(self.op_ids, reverse=True):
            fx_delete(p, f"/payments/{op_id}")
        if not fx_delete(p, f"/bank-accounts/{self.id}").ok:
            fx_put(p, f"/bank-accounts/{self.id}", {"is_active": False})
        for cat in (self.income, self.expense):
            fx_put(p, f"/categories/{cat['id']}", {"is_active": False})


@pytest.fixture
def cash(fx_page: Page):
    open_fx(fx_page, PAGE)
    c = Cash(fx_page)
    yield c
    c.cleanup()


def card(page: Page, cash: Cash):
    """Карточка своей кассы в «Мои кассы» (кнопка с названием и остатком)."""
    return page.locator("main").get_by_role("button").filter(has_text=cash.acc["name"])


def open_my_cash(page: Page, cash: Cash):
    open_fx(page, PAGE)
    tab(page, "Мои кассы")
    c = card(page, cash)
    expect(c).to_be_visible(timeout=15_000)
    c.click()
    expect(page.locator("main").get_by_role("button", name="+ Поступление наличных")).to_be_visible()


def write_operation(page: Page, kind: str, category: str, amount: float, reason: str):
    """«+ Поступление наличных» / «− Выдача наличных» → форма на экране → «Записать»."""
    main = page.locator("main")
    main.get_by_role("button", name=kind).click()
    main.get_by_role("button", name="Вид операции…").click()
    page.get_by_role("option", name=category, exact=True).click()
    main.get_by_placeholder("Сумма").fill(str(amount))
    main.get_by_placeholder("Основание / за что").fill(reason)
    with page.expect_response(lambda r: re.search(r"/api/cash-desk/accounts/\d+/operations$", r.url)
                              and r.request.method == "POST") as resp:
        main.get_by_role("button", name="Записать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    return resp.value.json()


def op_row(page: Page, text: str):
    return page.locator("main tbody tr").filter(has_text=text)


# Кейс: Каждая вкладка «Кассы» грузит свои данные без ошибок
TABS = {
    "Заметки": "/cash-desk/notes",
    "Сдать в кассу": "/cash-desk/handovers",
    "Наличные по кассам": "/cash-desk/summary",
    "Заказы без оплаты": "/cash-desk/orders/unpaid",
    "Дополнить заказы": "/trip-costs/pending",
    "Транспорт": "/trip-costs/report",
    "Исправленные": "/cash-desk/corrections",
    "Кто ведёт кассы": "/cash-desk/access",
    "Терминалы": "/cash-desk/terminals",
    "Карты: ждут решения": "/cash-desk/card-settlements",
    "Свои поля": "/custom-fields",
    "Транспорт: настройки": "/trip-costs/settings",
}


@pytest.mark.smoke
def test_tabs_load(fx_page: Page):
    open_fx(fx_page, PAGE)
    expect(fx_page).to_have_title("Касса — Facturi")
    for name, api in TABS.items():
        with fx_page.expect_response(lambda r: f"/api{api}" in r.url and r.request.method == "GET") as resp:
            tab(fx_page, name)
        assert resp.value.ok, f"{name}: {resp.value.status} {resp.value.text()[:200]}"
    expect(fx_page.locator("main")).not_to_contain_text(re.compile("Internal Server Error|Something went wrong"))


# Кейс: «Кто ведёт кассы» — «Ведёт кассу» открывает кассу в «Мои кассы», «Нет доступа» убирает
@pytest.mark.writes_data
def test_access_grants_my_cash(fx_page: Page, cash: Cash):
    me = my_id(fx_page)
    open_fx(fx_page, PAGE)
    tab(fx_page, "Кто ведёт кассы")
    block = fx_page.locator("main div.rounded-xl").filter(has_text=cash.acc["name"])
    member = block.locator("div.text-sm").filter(has=fx_page.get_by_text(QA_USER_NAME, exact=True))
    expect(member.get_by_role("button")).to_have_text("Нет доступа")

    def choose(option: str, user_ids: list[int]):
        with fx_page.expect_response(lambda r: r.url.endswith(f"/api/cash-desk/access/{cash.id}")
                                     and r.request.method == "PUT") as resp:
            member.get_by_role("button").click()
            fx_page.get_by_role("option", name=option, exact=True).click()
        assert resp.value.ok, resp.value.text()
        assert cash.access()["user_ids"] == user_ids

    choose("Ведёт кассу", [me])
    tab(fx_page, "Мои кассы")
    expect(card(fx_page, cash)).to_be_visible()
    mine = fx_json(fx_get(fx_page, "/cash-desk/accounts"))["items"]
    assert any(a["id"] == cash.id and a["level"] == "manage" for a in mine), mine

    tab(fx_page, "Кто ведёт кассы")
    choose("Нет доступа", [])
    tab(fx_page, "Мои кассы")
    expect(card(fx_page, cash)).to_have_count(0)


# Кейс: Поступление и выдача через форму — остаток, строки и «Наличные по кассам» = API
@pytest.mark.writes_data
def test_income_and_expense(fx_page: Page, cash: Cash):
    cash.grant()
    open_my_cash(fx_page, cash)

    inc = write_operation(fx_page, "+ Поступление наличных", cash.income["name"], 500, f"{QA_PREFIX} приход")
    out = write_operation(fx_page, "− Выдача наличных", cash.expense["name"], 200, f"{QA_PREFIX} выдача")
    cash.op_ids |= {inc["id"], out["id"]}
    assert (inc["type"], inc["amount"], inc["category_id"]) == ("income", 500, cash.income["id"]), inc
    assert (out["type"], out["amount"], out["category_id"]) == ("expense", 200, cash.expense["id"]), out

    expect(card(fx_page, cash)).to_contain_text(money(300))
    expect(op_row(fx_page, f"{QA_PREFIX} приход")).to_contain_text(money(500))
    expect(op_row(fx_page, f"{QA_PREFIX} выдача")).to_contain_text(money(200))
    assert {o["id"] for o in cash.operations()} == {inc["id"], out["id"]}

    tab(fx_page, "Наличные по кассам")
    line = fx_page.locator("main tbody tr").filter(has_text=cash.acc["name"])
    expect(line).to_contain_text(money(500))
    expect(line).to_contain_text(money(200))
    expect(line).to_contain_text(money(300))


# Кейс: Пересчёт кассы показывает расхождение с учётом
@pytest.mark.writes_data
def test_count_gap(fx_page: Page, cash: Cash):
    cash.grant()
    op = fx_json(fx_post(fx_page, f"/cash-desk/accounts/{cash.id}/operations",
                         {"type": "income", "amount": 500, "date": dt.date.today().isoformat(),
                          "description": f"{QA_PREFIX} до пересчёта", "category_id": cash.income["id"]}))
    cash.op_ids.add(op["id"])
    open_my_cash(fx_page, cash)

    main = fx_page.locator("main")
    main.get_by_placeholder("В кассе").fill("450")
    main.get_by_placeholder("Примечание").fill(f"{QA_PREFIX} пересчёт")
    with fx_page.expect_response(lambda r: r.url.endswith(f"/api/cash-desk/accounts/{cash.id}/count")
                                 and r.request.method == "POST") as resp:
        main.get_by_role("button", name="Записать пересчёт", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    count = resp.value.json()
    assert (count["amount"], count["computed_amount"], count["gap"]) == (450, 500, -50), count
    # кассиру расхождение не показывают — только «Пересчёт записан» (учётный остаток видит руководитель)
    dlg = fx_page.locator("[data-bord-dialogs]")
    expect(dlg).to_contain_text("Пересчёт записан")
    dlg.get_by_role("button", name="OK", exact=True).click()
    expect(dlg.get_by_role("button")).to_have_count(0)


# Кейс: «Исправить» операцию — правка с причиной в «Исправленных», «проверено» убирает её
@pytest.mark.writes_data
def test_correction_reviewed(fx_page: Page, cash: Cash):
    cash.grant()
    op = fx_json(fx_post(fx_page, f"/cash-desk/accounts/{cash.id}/operations",
                         {"type": "income", "amount": 500, "date": dt.date.today().isoformat(),
                          "description": f"{QA_PREFIX} с опечаткой", "category_id": cash.income["id"]}))
    cash.op_ids.add(op["id"])
    open_my_cash(fx_page, cash)

    row = op_row(fx_page, f"{QA_PREFIX} с опечаткой")
    row.get_by_role("button", name="Исправить").click()
    form = fx_page.locator("main div").filter(has_text=re.compile("^Исправление записи")).filter(
        has=fx_page.get_by_placeholder("Сумма")).last
    amount = form.get_by_placeholder("Сумма")
    expect(amount).to_have_value(re.compile(r"^500(\.0+)?$"))
    amount.fill("400")
    form.get_by_role("button", name="Исправить", exact=True).click()
    # причину спрашивает окно после «Исправить»
    dlg = fx_page.locator("[data-bord-dialogs]")
    dlg.locator("input, textarea").first.fill(f"{QA_PREFIX} опечатка в сумме")
    with fx_page.expect_response(lambda r: r.url.endswith(f"/operations/{op['id']}/correct")
                                 and r.request.method == "POST") as resp:
        dlg.get_by_role("button").last.click()
    assert resp.value.status == 201, resp.value.text()
    fixed = resp.value.json()
    cash.op_ids.add(fixed["id"])
    assert fixed["correction_of_id"] == op["id"], fixed

    expect(op_row(fx_page, f"{QA_PREFIX} с опечаткой")).to_contain_text(money(400))
    expect(card(fx_page, cash)).to_contain_text(money(400))

    tab(fx_page, "Исправленные")
    line = fx_page.locator("main tbody tr").filter(has_text=f"{QA_PREFIX} опечатка в сумме")
    expect(line).to_contain_text(cash.acc["name"])
    expect(line).to_contain_text(money(500))
    expect(line).to_contain_text(money(400))
    expect(line).to_contain_text(QA_USER_NAME)
    with fx_page.expect_response(lambda r: r.url.endswith(f"/cash-desk/corrections/{fixed['id']}/reviewed")
                                 and r.request.method == "POST") as resp:
        line.get_by_role("button").last.click()
    assert resp.value.ok, resp.value.text()
    expect(line).to_have_count(0)
    mine = [c for c in fx_json(fx_get(fx_page, "/cash-desk/corrections", {"only_unreviewed": "false"}))["items"]
            if c["id"] == fixed["id"]]
    assert mine and mine[0]["reviewed_at"], mine


@pytest.fixture
def notes(fx_page: Page):
    yield
    for n in fx_json(fx_get(fx_page, "/cash-desk/notes", {"show_done": "true"}))["items"]:
        if n["text"].startswith(QA_PREFIX):
            fx_delete(fx_page, f"/cash-desk/notes/{n['id']}")


# Кейс: Заметка кассы — добавить, отметить выполненной, выполненные прячутся
@pytest.mark.writes_data
def test_notes(fx_page: Page, notes):
    text = f"{QA_PREFIX} заметка {stamp()}"
    open_fx(fx_page, PAGE)
    tab(fx_page, "Заметки")
    main = fx_page.locator("main")
    with fx_page.expect_response(lambda r: r.url.endswith("/api/cash-desk/notes") and r.request.method == "POST") as resp:
        main.get_by_placeholder(re.compile("Что нужно сделать")).fill(text)
        main.get_by_role("button", name="Добавить", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    note = resp.value.json()
    assert (note["text"], note["done"]) == (text, False), note

    line = main.locator("li, tr, div.flex").filter(has_text=text).last
    expect(line).to_be_visible()
    show_done = main.locator("label").filter(has_text="Показывать выполненные").locator("input")
    show_done.set_checked(False)
    with fx_page.expect_response(lambda r: r.url.endswith(f"/cash-desk/notes/{note['id']}/toggle")) as resp:
        line.locator("input[type=checkbox]").first.click()
    assert resp.value.ok, resp.value.text()
    expect(main.get_by_text(text)).to_have_count(0)
    show_done.set_checked(True)
    expect(main.get_by_text(text)).to_be_visible()
    done = next(n for n in fx_json(fx_get(fx_page, "/cash-desk/notes", {"show_done": "true"}))["items"]
                if n["id"] == note["id"])
    assert done["done"] and done["done_by_name"] == QA_USER_NAME, done


# Кейс: «Заказы без оплаты» — итог и клиенты = API
def test_unpaid_orders_match_api(fx_page: Page):
    open_fx(fx_page, PAGE)
    with fx_page.expect_response(lambda r: "/api/cash-desk/orders/unpaid" in r.url) as resp:
        tab(fx_page, "Заказы без оплаты")
    data = resp.value.json()
    main = fx_page.locator("main")
    if not data["clients"]:
        expect(main.locator("tbody tr")).to_have_count(0)
        return
    expect(main).to_contain_text(f"Отгружено, но не оплачено: {data['orders']} заказов")
    expect(main).to_contain_text(money(data["total"]))
    for client in data["clients"]:
        line = main.locator("tbody tr").filter(has_text=client["client_name"]).first
        expect(line).to_contain_text(str(len(client["orders"])))
        expect(line).to_contain_text(money(client["remaining"]))
