"""Блок 40 (часть 1). Facturi — справочники: банковские счета, план счетов, юрлица, организация.

Снято 02.11.2026:
- «Банк. счета» (/admin/bank-accounts) = GET /api/bank-accounts/ (только активные):
  таблица «Название / Тип / Валюта / Остаток / Сверка с банком / Организация /
  Интеграция / Статус», фильтры «Все типы / Банк / Касса / Онлайн», итоги по валютам.
  «Новый счёт» → окно «Новый банковский счёт» (Название *, Тип, Валюта, Организация,
  банк, BIC, IBAN, SWIFT, «Показывать в документах», начальный остаток и дата,
  «Активен», порядок, «Пополняется со счёта», «Зарплатный счёт», интеграция);
  в строке «Изменить» → «Редактировать счёт». Фильтры типа шлют ?type=, а API его
  не знает и отдаёт все счета (bord-28, xfail). Удаления на экране нет (в API DELETE
  есть — им и убираем). Снятый «Активен» прячет счёт отовсюду, показать неактивные
  негде («Сверить с банком» и интеграции не трогаем);
- «План счетов» (/admin/accounts): «Все / Актив / Пассив / Капитал / Доход / Расход»,
  «Показать неактивные», в строке «Изменить» / «Удалить». DELETE не стирает, а
  выключает — код остаётся занятым (409 «Account with code … already exists»),
  поэтому коды в тестах уникальные;
- «Юрлица» (/admin/legal-entities): «Добавить юрлицо» → «Новое юрлицо» («Сохранить»
  неактивна без названия), в строке «Изменить» / «Удалить» (удаляет насовсем);
- «Организация»: «Редактировать организацию» (реквизиты) и блок «Документы»
  (подписант, должность, язык, адреса писем) — тесты меняют контактные реквизиты и
  возвращают как было. Налоговый режим, «Книги закрыты по», дату начальных остатков,
  статьи по умолчанию, «Создать организацию» и Telegram-бот не трогаем.
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.facturi import (QA_PREFIX, QA_ORG, field, fx_delete, fx_get, fx_json, fx_post, fx_put, modal,
                             open_fx, pick, stamp, tick)

pytestmark = pytest.mark.requires_auth

TYPE_LABEL = {"bank": "Банк", "cash": "Касса", "online": "Онлайн"}
ACC_TYPE = {"asset": "Актив", "liability": "Пассив", "equity": "Капитал", "revenue": "Доход", "expense": "Расход"}


def money(amount: float) -> str:
    """«1 234,50» с неразрывным пробелом, как рисует Intl, — для regex."""
    whole, frac = f"{amount:,.2f}".split(".")
    return r"\s".join(re.escape(p) for p in whole.split(",")) + f",{frac}"


def head(page: Page):
    return page.locator("header.sticky")


def row(page: Page, text: str):
    return page.locator("main tbody tr").filter(has_text=text)


def maybe_confirm(page: Page):
    """Удаление спрашивает подтверждение окном [data-bord-dialogs] — если спросило, «Да»."""
    dlg = page.locator("[data-bord-dialogs]").get_by_role("dialog")
    try:
        dlg.wait_for(timeout=2_000)
    except Exception:  # noqa: BLE001 — окна нет, удалилось сразу
        return None
    text = dlg.inner_text()
    buttons = dlg.get_by_role("button")
    yes = dlg.get_by_role("button", name=re.compile("^(Да|Удалить)$"))
    (yes if yes.count() else buttons.last).click()
    return text


# ───────────────────────── банковские счета ─────────────────────────

@pytest.fixture
def bank_accounts(fx_page: Page):
    """Свои банковские счета: после теста DELETE (на экране удаления нет)."""
    ids: list[int] = []

    def create(**data) -> dict:
        body = {"name": f"{QA_PREFIX} Счёт {stamp()}", "type": "bank", "currency": "MDL", **data}
        acc = fx_json(fx_post(fx_page, "/bank-accounts/", body))
        ids.append(acc["id"])
        return acc

    create.ids = ids
    yield create
    for acc_id in ids:
        fx_delete(fx_page, f"/bank-accounts/{acc_id}")


def open_bank_accounts(page: Page) -> list[dict]:
    with page.expect_response(lambda r: r.url.endswith("/api/bank-accounts/") and r.request.method == "GET") as resp:
        open_fx(page, "/admin/bank-accounts")
    return resp.value.json()


# Кейс: Банковские счета — список совпадает с API
@pytest.mark.smoke
def test_bank_accounts_match_api(fx_page: Page, bank_accounts):
    bank_accounts(type="cash", currency="EUR")
    api = open_bank_accounts(fx_page)

    expect(fx_page.locator("main tbody tr")).to_have_count(len(api))
    for acc in api:
        r = row(fx_page, acc["name"])
        expect(r).to_have_count(1)
        cells = r.locator("td")
        expect(cells.nth(1)).to_have_text(TYPE_LABEL[acc["type"]])
        expect(cells.nth(2)).to_have_text(acc["currency"])
        expect(cells.nth(3)).to_contain_text(acc["currency"])
    expect(head(fx_page)).to_contain_text(f"Банк. счета{len(api)}")


# Кейс: Новый банковский счёт через форму
@pytest.mark.writes_data
def test_bank_account_create_via_form(fx_page: Page, bank_accounts):
    name = f"{QA_PREFIX} Касса {stamp()}"
    open_bank_accounts(fx_page)
    head(fx_page).get_by_role("button", name="Новый счёт", exact=True).click()
    form = modal(fx_page, "Новый банковский счёт")
    create = form.get_by_role("button", name="Создать", exact=True)
    expect(create).to_be_disabled()

    field(form, "Название").fill(name)
    pick(fx_page, form, "Тип", "Касса")
    pick(fx_page, form, "Валюта", re.compile("^EUR"))
    field(form, "Название банка").fill("QA Bank")
    field(form, "Код банка (BIC)").fill("QABKMD22")
    field(form, "Номер счёта (IBAN)").fill("MD00QA0000000000000000EU")
    field(form, "Начальный остаток").fill("150.5")
    field(form, "Дата остатка").fill("2026-10-01")
    tick(form, "Показывать в документах", False)
    with fx_page.expect_response(lambda r: r.url.endswith("/api/bank-accounts/") and r.request.method == "POST") as resp:
        create.click()
    assert resp.value.status == 201, resp.value.text()
    acc = resp.value.json()
    bank_accounts.ids.append(acc["id"])

    assert (acc["name"], acc["type"], acc["currency"], acc["bank_name"], acc["bank_code"], acc["account_number"],
            acc["initial_balance"], acc["balance_date"], acc["show_on_documents"], acc["is_active"]) == (
        name, "cash", "EUR", "QA Bank", "QABKMD22", "MD00QA0000000000000000EU", 150.5, "2026-10-01", False, True)
    expect(form).to_have_count(0)
    r = row(fx_page, name)
    expect(r.locator("td").nth(1)).to_have_text("Касса")
    expect(r.locator("td").nth(3)).to_contain_text(re.compile(money(150.5) + r"\s*EUR"))


# Кейс: Правка банковского счёта
@pytest.mark.writes_data
def test_bank_account_edit(fx_page: Page, bank_accounts):
    acc = bank_accounts(bank_name="QA Bank")
    open_bank_accounts(fx_page)
    row(fx_page, acc["name"]).get_by_role("button", name="Изменить").click()
    form = modal(fx_page, "Редактировать счёт")
    expect(field(form, "Название")).to_have_value(acc["name"])

    new_name = acc["name"] + " (изм.)"
    field(form, "Название").fill(new_name)
    pick(fx_page, form, "Тип", "Онлайн")
    with fx_page.expect_response(lambda r: f"/api/bank-accounts/{acc['id']}" in r.url and r.request.method == "PUT") as resp:
        form.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()

    expect(row(fx_page, new_name).locator("td").nth(1)).to_have_text("Онлайн")
    api = fx_json(fx_get(fx_page, f"/bank-accounts/{acc['id']}"))
    assert (api["name"], api["type"]) == (new_name, "online")


# Кейс: Фильтр типа счёта «Касса»
def test_bank_account_type_filter(fx_page: Page, bank_accounts):
    cash = bank_accounts(type="cash")
    bank = bank_accounts(type="bank")
    open_bank_accounts(fx_page)

    fx_page.locator("main").get_by_role("button", name="Касса", exact=True).click()
    expect(row(fx_page, cash["name"])).to_be_visible()
    expect(row(fx_page, bank["name"])).to_have_count(0)
    for r in fx_page.locator("main tbody tr").all():
        expect(r.locator("td").nth(1)).to_have_text("Касса")

    fx_page.locator("main").get_by_role("button", name="Все типы", exact=True).click()
    expect(row(fx_page, bank["name"])).to_be_visible()


# Кейс: Снятый «Активен» прячет банковский счёт
@pytest.mark.writes_data
def test_bank_account_deactivate(fx_page: Page, bank_accounts):
    acc = bank_accounts()
    open_bank_accounts(fx_page)
    row(fx_page, acc["name"]).get_by_role("button", name="Изменить").click()
    form = modal(fx_page, "Редактировать счёт")
    tick(form, "Активен", False)
    with fx_page.expect_response(lambda r: f"/api/bank-accounts/{acc['id']}" in r.url and r.request.method == "PUT"):
        form.get_by_role("button", name="Сохранить", exact=True).click()

    expect(row(fx_page, acc["name"])).to_have_count(0)
    assert fx_json(fx_get(fx_page, f"/bank-accounts/{acc['id']}"))["is_active"] is False
    assert acc["id"] in [a["id"] for a in fx_json(fx_get(fx_page, "/bank-accounts/", {"active_only": "false"}))]


# ───────────────────────── план счетов ─────────────────────────

def account_code() -> str:
    import time
    return "98" + str(time.time_ns() // 1000)[-6:]


@pytest.fixture
def ledger(fx_page: Page):
    """Свои счета учёта: DELETE после теста (он только выключает — коды уникальные)."""
    ids: list[int] = []

    def create(**data) -> dict:
        body = {"code": account_code(), "name": f"{QA_PREFIX} Счёт учёта {stamp()}", "type": "expense", **data}
        acc = fx_json(fx_post(fx_page, "/accounts/", body))
        ids.append(acc["id"])
        return acc

    create.ids = ids
    yield create
    for acc_id in ids:
        fx_delete(fx_page, f"/accounts/{acc_id}")


def open_ledger(page: Page) -> list[dict]:
    with page.expect_response(lambda r: "/api/accounts/" in r.url and r.request.method == "GET") as resp:
        open_fx(page, "/admin/accounts")
    return resp.value.json()


# Кейс: План счетов — новый счёт через форму
@pytest.mark.writes_data
def test_ledger_create_via_form(fx_page: Page, ledger):
    parent = ledger(type="asset")
    code, name = account_code(), f"{QA_PREFIX} Субсчёт {stamp()}"
    open_ledger(fx_page)
    head(fx_page).get_by_role("button", name="Новый счёт", exact=True).click()
    form = modal(fx_page, "Новый счёт")
    create = form.get_by_role("button", name="Создать", exact=True)
    expect(create).to_be_disabled()

    field(form, "Код").fill(code)
    field(form, "Название").fill(name)
    pick(fx_page, form, "Тип", "Актив")
    pick(fx_page, form, "Родительский счёт", re.compile(re.escape(parent["name"])))
    with fx_page.expect_response(lambda r: r.url.endswith("/api/accounts/") and r.request.method == "POST") as resp:
        create.click()
    assert resp.value.status == 201, resp.value.text()
    acc = resp.value.json()
    ledger.ids.append(acc["id"])

    assert (acc["code"], acc["name"], acc["type"], acc["parent_id"]) == (code, name, "asset", parent["id"])
    r = row(fx_page, name)
    expect(r).to_contain_text(code)
    expect(r).to_contain_text("Актив")


# Кейс: План счетов — фильтр по типу
def test_ledger_type_tabs(fx_page: Page, ledger):
    income = ledger(type="revenue")
    expense = ledger(type="expense")
    open_ledger(fx_page)

    fx_page.locator("main").get_by_role("button", name="Доход", exact=True).click()
    expect(row(fx_page, income["name"])).to_be_visible()
    expect(row(fx_page, expense["name"])).to_have_count(0)
    fx_page.locator("main").get_by_role("button", name="Все", exact=True).click()
    expect(row(fx_page, expense["name"])).to_be_visible()


# Кейс: План счетов — правка
@pytest.mark.writes_data
def test_ledger_edit(fx_page: Page, ledger):
    acc = ledger()
    open_ledger(fx_page)
    row(fx_page, acc["name"]).get_by_role("button", name="Изменить").click()
    form = fx_page.locator("div.fixed.inset-0").last
    new_name = acc["name"] + " (изм.)"
    field(form, "Название").fill(new_name)
    with fx_page.expect_response(lambda r: f"/api/accounts/{acc['id']}" in r.url and r.request.method == "PUT") as resp:
        form.get_by_role("button", name=re.compile("^(Сохранить|Создать)$")).click()
    assert resp.value.ok, resp.value.text()

    expect(row(fx_page, new_name)).to_be_visible()
    assert fx_json(fx_get(fx_page, f"/accounts/{acc['id']}"))["name"] == new_name


# Кейс: План счетов — «Удалить» выключает, код остаётся занятым
@pytest.mark.writes_data
def test_ledger_delete_deactivates(fx_page: Page, ledger):
    acc = ledger()
    open_ledger(fx_page)
    with fx_page.expect_response(lambda r: f"/api/accounts/{acc['id']}" in r.url and r.request.method == "DELETE") as resp:
        row(fx_page, acc["name"]).get_by_role("button", name="Удалить").click()
        maybe_confirm(fx_page)
    assert resp.value.ok, resp.value.status

    expect(row(fx_page, acc["name"])).to_have_count(0)
    fx_page.locator("main label").filter(has_text="Показать неактивные").locator("input").check()
    expect(row(fx_page, acc["name"])).to_be_visible()
    again = fx_post(fx_page, "/accounts/", {"code": acc["code"], "name": "dup", "type": "expense"})
    assert again.status == 409, again.status


# ───────────────────────── юрлица ─────────────────────────

@pytest.fixture
def entities(fx_page: Page):
    ids: list[int] = []

    def create(**data) -> dict:
        le = fx_json(fx_post(fx_page, "/legal-entities/", {"name": f"{QA_PREFIX} Юрлицо {stamp()}", **data}))
        ids.append(le["id"])
        return le

    create.ids = ids
    yield create
    for le_id in ids:
        fx_delete(fx_page, f"/legal-entities/{le_id}")


def open_entities(page: Page) -> list[dict]:
    with page.expect_response(lambda r: r.url.endswith("/api/legal-entities/") and r.request.method == "GET") as resp:
        open_fx(page, "/admin/legal-entities")
    return resp.value.json()


# Кейс: Новое юрлицо через форму
@pytest.mark.writes_data
def test_legal_entity_create_via_form(fx_page: Page, entities):
    name = f"{QA_PREFIX} ИП {stamp()}"
    open_entities(fx_page)
    head(fx_page).get_by_role("button", name="Добавить юрлицо", exact=True).click()
    form = modal(fx_page, "Новое юрлицо")
    save = form.get_by_role("button", name="Сохранить", exact=True)
    expect(save).to_be_disabled()

    values = {"Название": name, "Юридическое название": "QA Autotest SRL", "IDNO / ИНН": "1003600000001",
              "Свой ряд номеров": "QA-", "Адрес": "mun. Chișinău, str. QA 3", "Телефон": "+37360000004",
              "Почта": "qa-le@example.com", "Сайт": "https://qa.example.com", "Подписант": "QA Autotest",
              "Должность подписанта": "Директор"}
    for label, value in values.items():
        field(form, label).fill(value)
    with fx_page.expect_response(lambda r: r.url.endswith("/api/legal-entities/") and r.request.method == "POST") as resp:
        save.click()
    assert resp.value.ok, resp.value.text()
    le = resp.value.json()
    entities.ids.append(le["id"])

    assert (le["name"], le["legal_name"], le["vat"], le["number_prefix"], le["email"], le["signatory_position"]) == (
        name, "QA Autotest SRL", "1003600000001", "QA-", "qa-le@example.com", "Директор"), le
    r = row(fx_page, name)
    expect(r).to_contain_text("QA Autotest SRL")
    expect(r).to_contain_text("QA-")
    expect(r).to_contain_text("Активно")


# Кейс: Юрлица — список совпадает с API
def test_legal_entities_match_api(fx_page: Page, entities):
    entities(legal_name="QA Autotest Список SRL")
    api = open_entities(fx_page)

    expect(fx_page.locator("main tbody tr")).to_have_count(len(api))
    for le in api:
        r = row(fx_page, le["name"])
        expect(r).to_contain_text(le["legal_name"] or "—")
        expect(r).to_contain_text(le["number_prefix"] or "общий")


# Кейс: Правка юрлица
@pytest.mark.writes_data
def test_legal_entity_edit(fx_page: Page, entities):
    le = entities(legal_name="QA Autotest Правка SRL")
    open_entities(fx_page)
    row(fx_page, le["name"]).get_by_role("button", name="Изменить").click()
    form = fx_page.locator("div.fixed.inset-0").last
    expect(field(form, "Название")).to_have_value(le["name"])
    field(form, "Свой ряд номеров").fill("QE-")
    with fx_page.expect_response(lambda r: f"/api/legal-entities/{le['id']}" in r.url and r.request.method == "PUT") as resp:
        form.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()

    expect(row(fx_page, le["name"])).to_contain_text("QE-")
    api = next(x for x in fx_json(fx_get(fx_page, "/legal-entities/")) if x["id"] == le["id"])
    assert api["number_prefix"] == "QE-"


# Кейс: Удаление юрлица
@pytest.mark.writes_data
def test_legal_entity_delete(fx_page: Page, entities):
    le = entities()
    open_entities(fx_page)
    with fx_page.expect_response(lambda r: f"/api/legal-entities/{le['id']}" in r.url and r.request.method == "DELETE") as resp:
        row(fx_page, le["name"]).get_by_role("button", name="Удалить").click()
        maybe_confirm(fx_page)
    assert resp.value.ok, resp.value.status

    expect(row(fx_page, le["name"])).to_have_count(0)
    assert le["id"] not in [x["id"] for x in fx_json(fx_get(fx_page, "/legal-entities/"))]
    entities.ids.remove(le["id"])


# ───────────────────────── организация ─────────────────────────

REQS = {"legal_name": "QA Autotest BORD QA SRL", "vat": "1003600099999", "city": "Chișinău",
        "address": "str. QA 42", "phone": "+37360000042", "email": "qa-org@example.com", "web": "https://qa.example.com"}
REQ_LABEL = {"legal_name": "Юридическое название", "vat": "ИНН / VAT", "city": "Город", "address": "Адрес",
             "phone": "Телефон", "email": "Email", "web": "Сайт"}


@pytest.fixture
def org_restore(fx_page: Page):
    """Реквизиты организации — после теста как были."""
    before = fx_json(fx_get(fx_page, "/organizations/current"))
    assert before["id"] == QA_ORG, f"реквизиты меняем только в BORD QA, а это {before['name']}"
    yield before
    keys = list(REQS) + ["signatory_name", "signatory_position", "email_from", "email_reply_to"]
    fx_put(fx_page, f"/organizations/{QA_ORG}", {k: before[k] for k in keys})


# Кейс: Реквизиты организации — правка и возврат
@pytest.mark.writes_data
def test_org_requisites_edit(fx_page: Page, org_restore):
    open_fx(fx_page, "/admin/organizations")
    fx_page.locator("main").get_by_role("button", name="Редактировать организацию", exact=True).click()
    form = modal(fx_page, "Редактировать организацию")
    expect(field(form, "Название")).to_have_value(org_restore["name"])

    for key, value in REQS.items():
        field(form, REQ_LABEL[key]).fill(value)
    with fx_page.expect_response(lambda r: f"/api/organizations/{QA_ORG}" in r.url and r.request.method == "PUT") as resp:
        form.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()

    api = fx_json(fx_get(fx_page, "/organizations/current"))
    assert {k: api[k] for k in REQS} == REQS
    assert api["name"] == org_restore["name"] and api["tax_regime"] == org_restore["tax_regime"]


# Кейс: Блок «Документы» организации — подписант и должность
@pytest.mark.writes_data
def test_org_documents_signatory(fx_page: Page, org_restore):
    open_fx(fx_page, "/admin/organizations")
    docs = fx_page.locator("main div").filter(has=fx_page.get_by_text("Кто подписывает", exact=True)).filter(
        has=fx_page.get_by_role("button", name="Сохранить")).last
    field(docs, "Кто подписывает").fill("QA Autotest Подписант")
    field(docs, "Должность").fill("Администратор")
    with fx_page.expect_response(lambda r: f"/api/organizations/{QA_ORG}" in r.url and r.request.method == "PUT") as resp:
        docs.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()

    api = fx_json(fx_get(fx_page, "/organizations/current"))
    assert (api["signatory_name"], api["signatory_position"]) == ("QA Autotest Подписант", "Администратор")
    fx_page.reload()
    expect(field(fx_page.locator("main"), "Кто подписывает")).to_have_value("QA Autotest Подписант")
