"""Блок 40 (часть 2). Facturi — справочники разметки: категории и их правила, бизнес-линии,
валюты, сальдо на начало.

Снято 02.11.2026:
- «Категории» (/admin/categories) = GET /api/categories/?active_only=false: таблица
  «Название / Доход/Расход / Активность / Вид деятельности / Тип», в строке «Изменить»;
  вкладки «Все / Доходы / Расходы / Без типа / Архивные». «Новая категория» → окно
  (Название *, Доход/Расход *, Родительская, Тип (ПиУ), Вид деятельности, «Активна»).
  Удаления нет — только снять «Активна» (уходит в «Архивные»);
- правила статьи — вкладки «По тексту строки» (#line-category-rules), «По назначению
  платежа», «По контрагенту»: форма в строке (направление, сравнение, образец, статья,
  «Добавить правило»), в строке правила «Сменить статью» / «Удалить правило».
  «Применить правила» не жмём — перекладывает статьи по всем документам организации;
- «Бизнес-линии» (/admin/business-lines): «Новая бизнес-линия» (Название *, Тип центра
  «Центр прибыли / Центр затрат», Родительская линия, Порядок, «Активна»), в строке
  «Изменить»; у центра затрат вместо родителя «Метод распределения» (По выручке /
  Вручную (%) / Поровну); «Правила направлений»: контрагент → направление, «Удалить
  правило». Удаления линий нет, а выключенные остаются в списке — тесты оживляют
  выключенные «QA Autotest Линия», новая появляется только в тесте формы;
- «Валюты» (/admin/currencies): «Код / Название / Символ / Курс / Базовая / Статус»,
  «Новая валюта» (Валюта из списка ISO, Код *, Символ, Название *, Курс обмена, «Базовая
  валюта» — не трогаем, «Активна»). Код — три латинские буквы, уникален; выключенная
  валюта пропадает с экрана, а «Новая валюта» с тем же кодом возвращает её — на этом
  держится уборка (тестовые коды Q??). «Курсы валют» = GET /currencies/<код>/rates;
  «Обновить курсы» (НБМ) не жмём;
- «Сальдо на начало» (/admin/opening-balances): без даты начальных остатков у
  организации API отвечает 422 — фикстура ставит дату и возвращает null. «Новое» →
  «Новое сальдо» (Контрагент *, Сумма * (+ / -), Валюта, Дата *, Примечание), клик по
  строке открывает карточку с «Редактировать» (форма прямо в карточке) / «Удалить». Первое сальдо заводит в плане
  счетов системные «Accounts Receivable» / «Opening Balance Equity».
"""
import random
import re
import string

import pytest
from playwright.sync_api import Page, expect

from helpers.facturi import (QA_PREFIX, QA_ORG, field, fx_delete, fx_get, fx_json, fx_post, fx_put, listbox,
                             modal, open_fx, pick, stamp, tick)

pytestmark = pytest.mark.requires_auth

CAT_TYPE = {"income": "Доход", "expense": "Расход"}
CF_ACTIVITY = {1: "Операционная деятельность", 2: "Инвестиционная деятельность", 3: "Финансовая деятельность"}


def head(page: Page):
    return page.locator("header.sticky")


def row(page: Page, text: str):
    return page.locator("main tbody tr").filter(has_text=text)


def maybe_confirm(page: Page):
    dlg = page.locator("[data-bord-dialogs]").get_by_role("dialog")
    try:
        dlg.wait_for(timeout=2_000)
    except Exception:  # noqa: BLE001 — окна нет
        return None
    text = dlg.inner_text()
    yes = dlg.get_by_role("button", name=re.compile("^(Да|Удалить)$"))
    (yes if yes.count() else dlg.get_by_role("button").last).click()
    return text


# ───────────────────────── категории ─────────────────────────

@pytest.fixture
def categories(fx_page: Page):
    """Свои статьи: удаления нет — после теста is_active=false."""
    ids: list[int] = []

    def create(**data) -> dict:
        cat = fx_json(fx_post(fx_page, "/categories/", {"name": f"{QA_PREFIX} Статья {stamp()}", "type": "expense", **data}))
        ids.append(cat["id"])
        return cat

    create.ids = ids
    yield create
    for cat_id in ids:
        fx_put(fx_page, f"/categories/{cat_id}", {"is_active": False})


def open_categories(page: Page):
    with page.expect_response(lambda r: "/api/categories/" in r.url and r.request.method == "GET"):
        open_fx(page, "/admin/categories")


def cat_row(page: Page, name: str):
    return page.locator("main table").first.locator("tbody tr").filter(has_text=name)


# Кейс: Новая категория через форму
@pytest.mark.writes_data
def test_category_create_via_form(fx_page: Page, categories):
    name = f"{QA_PREFIX} Доход {stamp()}"
    open_categories(fx_page)
    # с 15.11 «Категории» называются «Статьи»
    head(fx_page).get_by_role("button", name="Новая статья", exact=True).click()
    form = modal(fx_page, "Новая статья")
    create = form.get_by_role("button", name="Создать", exact=True)
    expect(create).to_be_disabled()

    field(form, "Название").fill(name)
    pick(fx_page, form, "Доход/Расход", "Доход")
    pick(fx_page, form, "Вид деятельности", "Финансовая деятельность")
    with fx_page.expect_response(lambda r: r.url.endswith("/api/categories/") and r.request.method == "POST") as resp:
        create.click()
    assert resp.value.status in (200, 201), resp.value.text()
    cat = resp.value.json()
    categories.ids.append(cat["id"])

    assert (cat["name"], cat["type"], cat["cf_activity"], cat["is_active"]) == (name, "income", 3, True)
    r = cat_row(fx_page, name)
    expect(r).to_contain_text("Доход")
    expect(r).to_contain_text("Финансовая деятельность")


# Кейс: Категории — вкладки «Доходы / Расходы»
def test_category_type_tabs(fx_page: Page, categories):
    income = categories(type="income")
    expense = categories(type="expense")
    open_categories(fx_page)
    main = fx_page.locator("main")

    main.get_by_role("button", name="Доходы", exact=True).click()
    expect(cat_row(fx_page, income["name"])).to_be_visible()
    expect(cat_row(fx_page, expense["name"])).to_have_count(0)
    main.get_by_role("button", name="Расходы", exact=True).click()
    expect(cat_row(fx_page, expense["name"])).to_be_visible()
    expect(cat_row(fx_page, income["name"])).to_have_count(0)


# Кейс: Категория в архив — снятой «Активна»
@pytest.mark.writes_data
def test_category_archive(fx_page: Page, categories):
    cat = categories()
    open_categories(fx_page)
    cat_row(fx_page, cat["name"]).get_by_role("button", name="Изменить").click()
    form = fx_page.locator("div.fixed.inset-0").last
    expect(field(form, "Название")).to_have_value(cat["name"])
    tick(form, "Активна", False)
    with fx_page.expect_response(lambda r: f"/api/categories/{cat['id']}" in r.url and r.request.method == "PUT") as resp:
        form.get_by_role("button", name=re.compile("^(Сохранить|Создать)$")).click()
    assert resp.value.ok, resp.value.text()

    api = next(c for c in fx_json(fx_get(fx_page, "/categories/", {"active_only": "false"})) if c["id"] == cat["id"])
    assert api["is_active"] is False
    main = fx_page.locator("main")
    main.get_by_role("button", name="Все", exact=True).click()
    expect(cat_row(fx_page, cat["name"])).to_have_count(0)
    main.get_by_role("button", name="Архивные", exact=True).click()
    expect(cat_row(fx_page, cat["name"])).to_be_visible()


# Кейс: Правило «текст строки → статья» — добавить и удалить
@pytest.mark.writes_data
def test_line_text_rule(fx_page: Page, categories):
    cat = categories(type="income")
    pattern = f"qa-autotest-{random.randint(10**5, 10**6)}"
    open_categories(fx_page)
    section = fx_page.locator("section#line-category-rules")
    expect(section).to_be_visible()

    # направление по умолчанию — «Исходящие накладные (доход)»
    section.get_by_placeholder("например: услуги программирования").fill(pattern)
    section.get_by_role("button", name="— Выберите —").click()
    fx_page.get_by_role("option").filter(has_text=cat["name"]).first.click()
    with fx_page.expect_response(lambda r: r.url.endswith("/api/line-category-rules/") and r.request.method == "POST") as resp:
        section.get_by_role("button", name="Добавить правило", exact=True).click()
    assert resp.value.ok, resp.value.text()
    rule = resp.value.json()

    try:
        assert (rule["pattern"], rule["category_id"], rule["direction"]) == (pattern, cat["id"], "income"), rule
        r = section.locator("tbody tr").filter(has_text=pattern)
        expect(r).to_contain_text(cat["name"])
        expect(r).to_contain_text("содержит")
        with fx_page.expect_response(lambda x: f"/api/line-category-rules/{rule['id']}" in x.url and x.request.method == "DELETE") as gone:
            r.get_by_role("button", name="Удалить правило").click()
            maybe_confirm(fx_page)
        assert gone.value.ok
        expect(r).to_have_count(0)
    finally:
        fx_delete(fx_page, f"/line-category-rules/{rule['id']}")


# ───────────────────────── бизнес-линии ─────────────────────────

@pytest.fixture
def lines(fx_page: Page):
    """Бизнес-линии теста. Удаления нет, а выключенные остаются в списке — поэтому
    берём выключенную «QA Autotest Линия» прошлых прогонов и оживляем её под новым
    именем; новая заводится, только если свободных нет. После теста — выключаем."""
    ids: list[int] = []

    def create(**data) -> dict:
        body = {"name": f"{QA_PREFIX} Линия {stamp()}", "is_active": True, "parent_id": None, "kind": "profit", **data}
        spare = [b for b in fx_json(fx_get(fx_page, "/business-lines/"))
                 if b["name"].startswith(QA_PREFIX) and not b["is_active"] and b["id"] not in ids]
        bl = fx_json(fx_put(fx_page, f"/business-lines/{spare[0]['id']}", body)) if spare \
            else fx_json(fx_post(fx_page, "/business-lines/", body))
        ids.append(bl["id"])
        return bl

    create.ids = ids
    yield create
    for bl_id in reversed(ids):  # сначала дочерние
        fx_put(fx_page, f"/business-lines/{bl_id}", {"is_active": False, "parent_id": None})


def open_lines(page: Page) -> list[dict]:
    with page.expect_response(lambda r: r.url.endswith("/api/business-lines/") and r.request.method == "GET") as resp:
        open_fx(page, "/admin/business-lines")
    return resp.value.json()


def new_line_form(page: Page):
    # с 15.11 «Бизнес-линии» называются «Направления»
    head(page).get_by_role("button", name="Новое направление", exact=True).click()
    form = modal(page, "Новое направление")
    expect(form.get_by_role("button", name="Создать", exact=True)).to_be_disabled()
    return form


def submit_line(page: Page, form, lines) -> dict:
    with page.expect_response(lambda r: r.url.endswith("/api/business-lines/") and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    bl = resp.value.json()
    lines.ids.append(bl["id"])
    return bl


# Кейс: Новая бизнес-линия через форму — центр прибыли внутри другого
@pytest.mark.writes_data
def test_business_line_create_child(fx_page: Page, lines):
    parent = lines(kind="profit")
    name = f"{QA_PREFIX} Линия {stamp()}"
    open_lines(fx_page)
    form = new_line_form(fx_page)
    field(form, "Название").fill(name)
    pick(fx_page, form, "Родительская линия", re.compile(re.escape(parent["name"])))
    bl = submit_line(fx_page, form, lines)

    assert (bl["name"], bl["kind"], bl["parent_id"], bl["is_active"]) == (name, "profit", parent["id"], True), bl
    expect(row(fx_page, name)).to_contain_text(f"#{bl['id']}")


# Кейс: Центр затрат — без родителя, с методом распределения
def test_business_line_cost_center_form(fx_page: Page):
    open_lines(fx_page)
    form = new_line_form(fx_page)
    expect(listbox(form, "Родительская линия")).to_be_visible()
    form.get_by_role("button", name="Центр затрат", exact=True).click()

    expect(form.locator("label").filter(has_text="Родительская линия")).to_have_count(0)
    expect(form.locator("label").filter(has_text="Метод распределения")).to_be_visible()
    listbox(form, "Метод распределения").click()
    expect(fx_page.get_by_role("option")).to_have_text(["По выручке", "Вручную (%)", "Поровну"])
    fx_page.keyboard.press("Escape")
    form.get_by_role("button", name="Отмена", exact=True).click()
    expect(form).to_have_count(0)


# Кейс: Правка бизнес-линии — центр затрат «Поровну»
@pytest.mark.writes_data
def test_business_line_edit(fx_page: Page, lines):
    bl = lines()
    open_lines(fx_page)
    row(fx_page, bl["name"]).get_by_role("button", name="Изменить").click()
    form = fx_page.locator("div.fixed.inset-0").last
    expect(field(form, "Название")).to_have_value(bl["name"])
    new_name = bl["name"] + " (изм.)"
    field(form, "Название").fill(new_name)
    form.get_by_role("button", name="Центр затрат", exact=True).click()
    pick(fx_page, form, "Метод распределения", "Поровну")
    with fx_page.expect_response(lambda r: f"/api/business-lines/{bl['id']}" in r.url and r.request.method == "PUT") as resp:
        form.get_by_role("button", name=re.compile("^(Сохранить|Создать)$")).click()
    assert resp.value.ok, resp.value.text()

    expect(row(fx_page, new_name)).to_be_visible()
    api = next(b for b in fx_json(fx_get(fx_page, "/business-lines/")) if b["id"] == bl["id"])
    assert (api["name"], api["kind"]) == (new_name, "cost"), api
    assert api["allocation_method"], api


def rules_box(page: Page):
    """«Правила направлений»: подписи — не label, выпадашки по порядку (контрагент, направление)."""
    return page.locator("main div").filter(
        has=page.get_by_role("heading", name=re.compile("^Правила направлений"))).filter(
        has=page.get_by_role("button", name="Добавить правило")).last


# Кейс: Правило направления — контрагент → бизнес-линия
@pytest.mark.writes_data
def test_business_line_rule(fx_page: Page, lines):
    bl = lines()
    cp = fx_json(fx_post(fx_page, "/counterparties/", {"name": f"{QA_PREFIX} Контрагент {stamp()}", "type": "customer"}))
    try:
        open_lines(fx_page)
        box = rules_box(fx_page)
        add = box.get_by_role("button", name="Добавить правило", exact=True)
        expect(add).to_be_disabled()
        pickers = box.locator("button[aria-haspopup=listbox]")
        pickers.nth(0).click()
        fx_page.get_by_role("option").filter(has_text=cp["name"]).first.click()
        pickers.nth(1).click()
        fx_page.get_by_role("option").filter(has_text=bl["name"]).first.click()
        with fx_page.expect_response(lambda r: r.url.endswith("/api/business-line-rules/") and r.request.method == "POST") as resp:
            add.click()
        assert resp.value.ok, resp.value.text()
        rule = resp.value.json()
        assert (rule["counterparty_id"], rule["business_line_id"]) == (cp["id"], bl["id"])

        r = box.locator("tbody tr").filter(has_text=cp["name"])
        expect(r).to_contain_text(bl["name"])
        with fx_page.expect_response(lambda x: f"/api/business-line-rules/{rule['id']}" in x.url and x.request.method == "DELETE") as gone:
            r.get_by_role("button", name="Удалить правило").click()
            maybe_confirm(fx_page)
        assert gone.value.ok
        expect(r).to_have_count(0)
    finally:
        for rule in fx_json(fx_get(fx_page, "/business-line-rules/"))["items"]:
            if rule["counterparty_id"] == cp["id"]:
                fx_delete(fx_page, f"/business-line-rules/{rule['id']}")
        fx_put(fx_page, f"/counterparties/{cp['id']}", {"is_active": False})


# ───────────────────────── валюты ─────────────────────────

def qa_code() -> str:
    """Тестовый код валюты «Q??» — не пересекается с ISO-валютами BORD QA."""
    return "Q" + "".join(random.choices(string.ascii_uppercase, k=2))


@pytest.fixture
def currencies(fx_page: Page):
    """Тестовые валюты выключаются после теста (удаления нет)."""
    ids: list[int] = []
    yield ids
    for cur_id in ids:
        fx_put(fx_page, f"/currencies/{cur_id}", {"is_active": False})


def open_currencies(page: Page) -> list[dict]:
    with page.expect_response(lambda r: r.url.endswith("/api/currencies/") and r.request.method == "GET") as resp:
        open_fx(page, "/admin/currencies")
    return resp.value.json()


def cur_row(page: Page, code: str):
    return page.locator("main table").first.locator("tbody tr").filter(
        has=page.get_by_role("cell", name=code, exact=True))


# Кейс: Валюты — список совпадает с API
@pytest.mark.smoke
def test_currencies_match_api(fx_page: Page):
    api = open_currencies(fx_page)

    expect(fx_page.locator("main table").first.locator("tbody tr")).to_have_count(len(api))
    expect(head(fx_page)).to_contain_text(f"Валюты{len(api)}")
    for cur in api:
        r = cur_row(fx_page, cur["code"])
        expect(r).to_contain_text(cur["name"])
        if cur.get("symbol"):
            expect(r).to_contain_text(cur["symbol"])
    assert sum(c["is_base"] for c in api) == 1, "базовая валюта должна быть ровно одна"


# Кейс: Новая валюта через форму, выключение и возврат тем же кодом
@pytest.mark.writes_data
def test_currency_create_hide_and_revive(fx_page: Page, currencies):
    taken = {c["code"] for c in fx_json(fx_get(fx_page, "/currencies/", {"active_only": "false"}))}
    code = next(c for c in iter(qa_code, None) if c not in taken)
    name = f"{QA_PREFIX} валюта {stamp()}"

    def submit_form(title: str):
        head(fx_page).get_by_role("button", name="Новая валюта", exact=True).click()
        form = modal(fx_page, "Новая валюта")
        field(form, "Код").fill(code)
        field(form, "Название").fill(title)
        field(form, "Символ").fill("¤")
        field(form, "Курс обмена").fill("2.5")
        form.get_by_role("button", name="Создать", exact=True).click()
        expect(form).to_have_count(0)

    open_currencies(fx_page)
    submit_form(name)
    expect(cur_row(fx_page, code)).to_contain_text(name)
    cur = next(c for c in fx_json(fx_get(fx_page, "/currencies/")) if c["code"] == code)
    currencies.append(cur["id"])
    assert (cur["symbol"], cur["exchange_rate"], cur["is_base"]) == ("¤", 2.5, False), cur

    fx_json(fx_put(fx_page, f"/currencies/{cur['id']}", {"is_active": False}))
    open_currencies(fx_page)
    expect(cur_row(fx_page, code)).to_have_count(0)

    submit_form(name + " (снова)")
    expect(cur_row(fx_page, code)).to_contain_text(name + " (снова)")
    back = [c for c in fx_json(fx_get(fx_page, "/currencies/", {"active_only": "false"})) if c["code"] == code]
    assert [c["id"] for c in back] == [cur["id"]], f"вместо возврата завелась вторая {code}: {back}"
    assert back[0]["is_active"] is True


# Кейс: Валюта из списка ISO заполняет код, название и символ
def test_currency_iso_picker(fx_page: Page):
    open_currencies(fx_page)
    head(fx_page).get_by_role("button", name="Новая валюта", exact=True).click()
    form = modal(fx_page, "Новая валюта")
    listbox(form, "Валюта из списка ISO").click()
    fx_page.keyboard.type("GBP")
    fx_page.get_by_role("option").filter(has_text="GBP").first.click()

    expect(field(form, "Код")).to_have_value("GBP")
    expect(field(form, "Название")).not_to_have_value("")
    expect(field(form, "Символ")).to_have_value("£")
    form.get_by_role("button", name="Отмена", exact=True).click()
    expect(form).to_have_count(0)


# Кейс: Курсы валют — таблица совпадает с API
def test_currency_rates_match_api(fx_page: Page):
    with fx_page.expect_response(lambda r: "/api/currencies/EUR/rates" in r.url) as resp:
        open_fx(fx_page, "/admin/currencies")
    rates = resp.value.json()
    items = rates if isinstance(rates, list) else rates.get("items", rates.get("rates", []))

    panel = fx_page.locator("main").filter(has_text="Курсы валют")
    if not items:
        expect(panel.get_by_text("Нет данных. Нажмите «Обновить курсы».")).to_be_visible()
        return
    table = fx_page.locator("main table").last
    expect(table.locator("tbody tr")).to_have_count(len(items))


# ───────────────────────── сальдо на начало ─────────────────────────

@pytest.fixture
def opening_date(fx_page: Page):
    """Дата начальных остатков организации — без неё сальдо не создаётся (422)."""
    org = fx_json(fx_get(fx_page, "/organizations/current"))
    assert org["id"] == QA_ORG
    # с 08.11 форма сальдо берёт дату только отсюда — держим известную на время теста
    if org["opening_date"] != "2026-01-01":
        fx_json(fx_put(fx_page, f"/organizations/{QA_ORG}", {"opening_date": "2026-01-01"}))
    yield "2026-01-01"
    fx_put(fx_page, f"/organizations/{QA_ORG}", {"opening_date": org["opening_date"]})


@pytest.fixture
def ob_counterparty(fx_page: Page):
    cp = fx_json(fx_post(fx_page, "/counterparties/", {"name": f"{QA_PREFIX} Сальдо {stamp()}", "type": "customer"}))
    yield cp
    for ob in fx_json(fx_get(fx_page, "/opening-balances/", {"counterparty_id": cp["id"]}))["items"]:
        fx_delete(fx_page, f"/opening-balances/{ob['id']}")
    fx_put(fx_page, f"/counterparties/{cp['id']}", {"is_active": False})


# Кейс: Сальдо без даты начальных остатков — понятная ошибка
def test_opening_balance_needs_org_date(fx_page: Page, ob_counterparty):
    org = fx_json(fx_get(fx_page, "/organizations/current"))
    if org["opening_date"]:
        pytest.skip("у BORD QA уже задана дата начальных остатков")
    resp = fx_post(fx_page, "/opening-balances/", {"counterparty_id": ob_counterparty["id"], "amount": 10,
                                                    "as_of_date": "2026-01-01"})
    assert resp.status == 422, resp.status
    assert "дата открытия остатков" in resp.text()


# Кейс: Сальдо на начало — создать, открыть, изменить, удалить
@pytest.mark.writes_data
def test_opening_balance_crud(fx_page: Page, opening_date, ob_counterparty):
    with fx_page.expect_response(lambda r: "/api/opening-balances/" in r.url and r.request.method == "GET"):
        open_fx(fx_page, "/admin/opening-balances")
    fx_page.locator("main").get_by_role("button", name="Новое", exact=True).click()
    form = modal(fx_page, "Новое сальдо")
    create = form.get_by_role("button", name="Создать", exact=True)
    expect(create).to_be_disabled()

    pick(fx_page, form, "Контрагент", re.compile(re.escape(ob_counterparty["name"])))
    field(form, "Сумма", exact=False).fill("1234.5")
    # с 08.11 дата сальдо = дата открытия остатков организации, поле только для чтения
    expect(field(form, "Дата")).to_have_value("2026-01-01")
    expect(field(form, "Дата")).not_to_be_editable()
    field(form, "Примечание").fill("сальдо автотеста")
    with fx_page.expect_response(lambda r: r.url.endswith("/api/opening-balances/") and r.request.method == "POST") as resp:
        create.click()
    assert resp.value.ok, resp.value.text()
    ob = resp.value.json()
    assert (ob["counterparty_id"], ob["amount"], ob["as_of_date"]) == (ob_counterparty["id"], 1234.5, "2026-01-01"), ob

    main = fx_page.locator("main")
    main.get_by_text(ob_counterparty["name"]).first.click()
    expect(main).to_contain_text("Нам должны")
    expect(main).to_contain_text(re.compile(r"1\s234,50"))
    expect(main).to_contain_text("Дата: 01.01.2026")
    expect(main).to_contain_text("сальдо автотеста")

    # правка — форма прямо в карточке, не окно
    main.get_by_role("button", name="Редактировать", exact=True).click()
    field(main, "Сумма (+ нам должны, - мы должны)").fill("-200")
    with fx_page.expect_response(lambda r: f"/api/opening-balances/{ob['id']}" in r.url and r.request.method == "PUT") as upd:
        main.get_by_role("button", name="Сохранить", exact=True).click()
    assert upd.value.ok, upd.value.text()
    expect(main).to_contain_text("Мы должны")
    assert fx_json(fx_get(fx_page, f"/opening-balances/{ob['id']}"))["amount"] == -200

    with fx_page.expect_response(lambda r: f"/api/opening-balances/{ob['id']}" in r.url and r.request.method == "DELETE") as gone:
        main.get_by_role("button", name="Удалить", exact=True).click()
        maybe_confirm(fx_page)
    assert gone.value.ok
    expect(main.get_by_text(ob_counterparty["name"])).to_have_count(0)
