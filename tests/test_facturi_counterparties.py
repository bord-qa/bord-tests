"""Блок 39. Facturi — контрагенты: app.facturi.md/admin/counterparties.

Снято 02.11.2026:
- список — GET /api/counterparties/?with_balance=true (по умолчанию только активные),
  таблица «Название / Тип / ИНН / Баланс», в шапке счётчик, поиск «Поиск по
  названию...» (→ &search=, в адресе ?q=), «Все / Клиенты / Поставщики» (→ &type=,
  ?type=), «С картой» (→ &with_card=true, ?card=1), ссылка «Дубли», кнопка «Новый»;
  пусто — «Нет контрагентов»;
- «Новый» — окно «Новый контрагент» (div.fixed, не role=dialog, Esc не закрывает):
  Название *, Тип * (Клиент / Поставщик / Оба), Email, Телефон, ИНН, Адрес, Язык
  документов, Заметки; «Создать» неактивна без названия → POST /api/counterparties/;
- карточка — ?id=<id> на том же экране: «К списку», тип, «Слить с…» (ссылка на
  «Дубли»), «Редактировать» (то же окно, «Сохранить» → PUT), реквизиты, плитки
  «Нам должны / Мы должны» — клик открывает «… — из чего сложилось» =
  GET …/{id}/balance/breakdown?kind=receivable|payable;
- «Дубли» (/admin/counterparties/duplicates) = GET …/duplicates: «Совпал фискальный
  код» (группы по ИНН, радио «останется», «Слить» — сразу, без подтверждения) и
  «Совпало имя» (только предложение). Слияние ничего не удаляет: дубль неактивен с
  merged_into; на его карточке «Карточка слита в #<id>» и «Расслить» (окно «Вернуть
  карточку из слияния?» → «Да»). «Слить все по ИДНО» не жмём — сольёт и чужие дубли.

Удаления у контрагентов нет (ни в API, ни на экране) — тесты прячут свои карточки
через PUT is_active=false, «QA Autotest …» копятся неактивными. API принимает пустое
название и любой email (форма пустое название не пускает) — как в CRM, не заводим.
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.facturi import (FACTURI, QA_PREFIX, confirm, field, fx_get, fx_json, fx_post, fx_put,
                             modal, open_fx, pick, stamp, unique_idno)

pytestmark = pytest.mark.requires_auth

LIST = "/admin/counterparties"
TYPE_LABEL = {"customer": "Клиент", "supplier": "Поставщик", "both": "Оба"}


def money(amount: float) -> re.Pattern:
    """«1 200,00» с любым пробелом между тысячами."""
    whole, frac = f"{amount:,.2f}".split(".")
    return re.compile(r"\s".join(re.escape(p) for p in whole.split(",")) + f",{frac}")


@pytest.fixture
def made(fx_page: Page):
    """Свои контрагенты теста: после теста — is_active=false (удаления нет)."""
    ids: list[int] = []

    def create(**data) -> dict:
        body = {"name": f"{QA_PREFIX} Контрагент {stamp()}", "type": "customer", **data}
        cp = fx_json(fx_post(fx_page, "/counterparties/", body))
        ids.append(cp["id"])
        return cp

    create.ids = ids
    yield create
    # «Дубли» показывают и неактивные карточки — свои пары с одним ИНН сливаем,
    # иначе каждая висела бы там группой после прогона
    dups = fx_get(fx_page, "/counterparties/duplicates")
    if dups.ok:
        for group in dups.json()["idno"]:
            members = [m["id"] for m in group["members"]]
            if len(members) > 1 and set(members) <= set(ids):
                fx_post(fx_page, "/counterparties/merge", {"survivor_id": members[0], "duplicate_ids": members[1:]})
    for cp_id in ids:
        fx_put(fx_page, f"/counterparties/{cp_id}", {"is_active": False})


def head(page: Page):
    return page.locator("header.sticky")


def rows(page: Page):
    return page.locator("main tbody tr")


def row(page: Page, name: str):
    return rows(page).filter(has=page.get_by_role("cell", name=name, exact=True))


def open_list(page: Page, query: str = "") -> list[dict]:
    with page.expect_response(lambda r: "/api/counterparties/?" in r.url and r.request.method == "GET") as resp:
        open_fx(page, LIST + query)
    return resp.value.json()


def card(page: Page, cp_id: int) -> dict:
    open_fx(page, f"{LIST}?id={cp_id}")
    expect(page.locator("main").get_by_text("К списку")).to_be_visible()
    return fx_json(fx_get(page, f"/counterparties/{cp_id}"))


# Кейс: Список контрагентов совпадает с API
@pytest.mark.smoke
def test_list_matches_api(fx_page: Page, made):
    made(type="supplier", tax_id=unique_idno())
    api = open_list(fx_page)

    expect(rows(fx_page)).to_have_count(len(api))
    # заголовок шапки — «Контрагенты» и счётчик в одном span
    expect(head(fx_page).get_by_title("Контрагенты", exact=True)).to_have_text(f"Контрагенты{len(api)}")
    for i, cp in enumerate(api):
        cells = rows(fx_page).nth(i).locator("td")
        expect(cells.nth(0)).to_have_text(cp["name"])
        expect(cells.nth(1)).to_have_text(TYPE_LABEL[cp["type"]])
        expect(cells.nth(2)).to_have_text(cp["tax_id"] or "—")
        bal = cp.get("balance") or {}
        if bal.get("receivable"):
            expect(cells.nth(3)).to_contain_text(money(bal["receivable"]))
        elif not bal.get("payable") and not bal.get("current_balance"):
            expect(cells.nth(3)).to_have_text("—")


# Кейс: Без названия контрагента не создать, «Отмена» закрывает без сохранения
def test_form_requires_name(fx_page: Page):
    open_list(fx_page)
    head(fx_page).get_by_role("button", name="Новый", exact=True).click()
    form = modal(fx_page, "Новый контрагент")
    create = form.get_by_role("button", name="Создать", exact=True)

    expect(create).to_be_disabled()
    field(form, "Название").fill("   ")
    expect(create).to_be_disabled()
    field(form, "Название").fill("x")
    expect(create).to_be_enabled()

    posts = []
    fx_page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "/api/counterparties" in r.url else None)
    fx_page.keyboard.press("Escape")
    expect(form).to_be_visible()  # Esc окно не закрывает — только «Отмена»
    form.get_by_role("button", name="Отмена", exact=True).click()
    expect(form).to_have_count(0)
    assert not posts, posts


# Кейс: Новый контрагент через форму — все поля
@pytest.mark.writes_data
def test_create_via_form(fx_page: Page, made):
    name = f"{QA_PREFIX} Поставщик {stamp()}"
    idno = unique_idno()
    open_list(fx_page)
    head(fx_page).get_by_role("button", name="Новый", exact=True).click()
    form = modal(fx_page, "Новый контрагент")
    field(form, "Название").fill(name)
    pick(fx_page, form, "Тип", "Поставщик")
    field(form, "Email").fill("qa-autotest@example.com")
    field(form, "Телефон").fill("+37360000001")
    field(form, "ИНН").fill(idno)
    field(form, "Адрес").fill("mun. Chișinău, str. QA 1")
    pick(fx_page, form, "Язык документов", "Română")
    field(form, "Заметки").fill("создан автотестом")

    with fx_page.expect_response(lambda r: r.url.endswith("/api/counterparties/") and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    cp = resp.value.json()
    made.ids.append(cp["id"])

    assert (cp["name"], cp["type"], cp["tax_id"], cp["email"], cp["phone"], cp["address"], cp["language"], cp["notes"]) == (
        name, "supplier", idno, "qa-autotest@example.com", "+37360000001", "mun. Chișinău, str. QA 1", "ro",
        "создан автотестом")
    expect(form).to_have_count(0)
    new = row(fx_page, name)
    expect(new).to_contain_text("Поставщик")
    expect(new).to_contain_text(idno)


# Кейс: Карточка контрагента по ссылке совпадает с API
def test_card_matches_api(fx_page: Page, made):
    cp = made(tax_id=unique_idno(), email="qa-card@example.com", phone="+37360000002",
              address="mun. Bălți, str. QA 2", notes="заметка автотеста")
    api = card(fx_page, cp["id"])
    main = fx_page.locator("main")

    expect(main.get_by_role("heading", name=api["name"], exact=True)).to_be_visible()
    expect(main).to_contain_text(TYPE_LABEL[api["type"]])
    for text in (f"ИНН: {api['tax_id']}", f"Email: {api['email']}", f"Тел: {api['phone']}",
                 f"Адрес: {api['address']}", api["notes"]):
        expect(main).to_contain_text(text)
    expect(main.get_by_role("link", name="Слить с…")).to_have_attribute("href", f"{LIST}/duplicates")

    main.get_by_text("К списку").click()
    expect(fx_page).to_have_url(FACTURI + LIST)
    expect(row(fx_page, api["name"])).to_be_visible()


# Кейс: Открытие карточки кликом по строке
def test_card_by_row_click(fx_page: Page, made):
    cp = made()
    open_list(fx_page)
    row(fx_page, cp["name"]).click()

    expect(fx_page).to_have_url(f"{FACTURI}{LIST}?id={cp['id']}")
    expect(fx_page.locator("main").get_by_role("heading", name=cp["name"], exact=True)).to_be_visible()


# Кейс: Правка контрагента
@pytest.mark.writes_data
def test_edit(fx_page: Page, made):
    cp = made(phone="+37360000003")
    card(fx_page, cp["id"])
    fx_page.locator("main").get_by_role("button", name="Редактировать", exact=True).click()
    form = modal(fx_page, "Редактировать контрагента")
    expect(field(form, "Название")).to_have_value(cp["name"])
    expect(field(form, "Телефон")).to_have_value("+37360000003")

    new_name = cp["name"] + " (изм.)"
    field(form, "Название").fill(new_name)
    field(form, "Телефон").fill("+37360000033")
    pick(fx_page, form, "Тип", "Оба")
    with fx_page.expect_response(lambda r: f"/api/counterparties/{cp['id']}" in r.url and r.request.method == "PUT") as resp:
        form.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()

    expect(form).to_have_count(0)
    main = fx_page.locator("main")
    expect(main.get_by_role("heading", name=new_name, exact=True)).to_be_visible()
    expect(main).to_contain_text("Тел: +37360000033")
    expect(main).to_contain_text("Оба")
    api = fx_json(fx_get(fx_page, f"/counterparties/{cp['id']}"))
    assert (api["name"], api["phone"], api["type"]) == (new_name, "+37360000033", "both")


# Кейс: Поиск по названию
def test_search(fx_page: Page, made):
    cp = made()
    other = made(name=f"{QA_PREFIX} Другой {stamp()}")
    open_list(fx_page)

    with fx_page.expect_response(lambda r: "search=" in r.url and "/api/counterparties/" in r.url) as resp:
        head(fx_page).get_by_placeholder("Поиск по названию...").fill(cp["name"])
    api = resp.value.json()
    assert [c["id"] for c in api] == [cp["id"]], api

    expect(rows(fx_page)).to_have_count(1)
    expect(row(fx_page, cp["name"])).to_be_visible()
    expect(row(fx_page, other["name"])).to_have_count(0)
    expect(fx_page).to_have_url(re.compile(r"\?q="))


# Кейс: Поиск без результатов — «Нет контрагентов»
def test_search_nothing(fx_page: Page):
    open_list(fx_page)
    with fx_page.expect_response(lambda r: "search=" in r.url and "/api/counterparties/" in r.url) as resp:
        head(fx_page).get_by_placeholder("Поиск по названию...").fill("zzz-нет-такого-" + stamp())
    assert resp.value.json() == []
    expect(fx_page.locator("main").get_by_text("Нет контрагентов")).to_be_visible()


# Кейс: Фильтр «Клиенты / Поставщики / Все»
@pytest.mark.parametrize("button, api_type", [("Поставщики", "supplier"), ("Клиенты", "customer")])
def test_type_filter(fx_page: Page, made, button, api_type):
    supplier = made(type="supplier")
    customer = made(type="customer")
    open_list(fx_page)

    with fx_page.expect_response(lambda r: f"type={api_type}" in r.url) as resp:
        head(fx_page).get_by_role("button", name=button, exact=True).click()
    api = resp.value.json()
    assert api and all(c["type"] == api_type for c in api), [c["type"] for c in api]
    expect(fx_page).to_have_url(re.compile(f"type={api_type}"))
    expect(rows(fx_page)).to_have_count(len(api))
    mine, alien = (supplier, customer) if api_type == "supplier" else (customer, supplier)
    expect(row(fx_page, mine["name"])).to_be_visible()
    expect(row(fx_page, alien["name"])).to_have_count(0)

    with fx_page.expect_response(lambda r: "/api/counterparties/?" in r.url and "type=" not in r.url):
        head(fx_page).get_by_role("button", name="Все", exact=True).click()
    expect(row(fx_page, alien["name"])).to_be_visible()


# Кейс: «С картой» — только контрагенты с сохранённой картой
def test_with_card_filter(fx_page: Page):
    open_list(fx_page)
    with fx_page.expect_response(lambda r: "with_card=true" in r.url) as resp:
        head(fx_page).get_by_role("button", name="С картой", exact=True).click()
    api = resp.value.json()

    expect(fx_page).to_have_url(re.compile(r"card=1"))
    if api:
        expect(rows(fx_page)).to_have_count(len(api))
    else:
        expect(fx_page.locator("main").get_by_text("Нет контрагентов")).to_be_visible()


# Кейс: Неактивный контрагент не виден в списке и поиске
def test_inactive_hidden(fx_page: Page, made):
    cp = made()
    fx_json(fx_put(fx_page, f"/counterparties/{cp['id']}", {"is_active": False}))
    open_list(fx_page)

    expect(row(fx_page, cp["name"])).to_have_count(0)
    with fx_page.expect_response(lambda r: "search=" in r.url):
        head(fx_page).get_by_placeholder("Поиск по названию...").fill(cp["name"])
    expect(fx_page.locator("main").get_by_text("Нет контрагентов")).to_be_visible()


def dup_group(page: Page, key: str):
    """Группа «Совпал фискальный код» по ИНН на экране «Дубли»."""
    # самый глубокий блок, где есть и ИНН с «Слить», и радио карточек
    return page.locator("main div").filter(has=page.get_by_text(key, exact=True)).filter(
        has=page.get_by_role("button", name="Слить", exact=True)).filter(has=page.get_by_role("radio")).last


# Кейс: Дубль ИНН попадает в «Дубли» и сливается
@pytest.mark.writes_data
def test_duplicate_idno_merge_and_unmerge(fx_page: Page, made):
    idno = unique_idno()
    first = made(tax_id=idno)
    second = made(tax_id=idno, name=f"{QA_PREFIX} Дубль {stamp()}")

    with fx_page.expect_response(lambda r: "/api/counterparties/duplicates" in r.url) as resp:
        open_fx(fx_page, f"{LIST}/duplicates")
    groups = {g["key"]: g for g in resp.value.json()["idno"]}
    assert idno in groups, f"ИНН {idno} нет в дублях API"
    assert {m["id"] for m in groups[idno]["members"]} == {first["id"], second["id"]}

    group = dup_group(fx_page, idno)
    expect(group).to_contain_text("карточек 2")
    keep = group.get_by_role("radio", name=re.compile(re.escape(second["name"])))
    keep.check()
    with fx_page.expect_response(lambda r: "/api/counterparties/merge" in r.url) as merged:
        group.get_by_role("button", name="Слить", exact=True).click()
    assert merged.value.ok, merged.value.text()
    expect(fx_page.get_by_text(idno, exact=True)).to_have_count(0)

    loser = fx_json(fx_get(fx_page, f"/counterparties/{first['id']}"))
    assert (loser["is_active"], loser["merged_into_counterparty_id"]) == (False, second["id"]), loser

    # карточка слитого: плашка и «Расслить»
    card(fx_page, first["id"])
    main = fx_page.locator("main")
    expect(main).to_contain_text(f"Карточка слита в #{second['id']}")
    main.get_by_role("button", name="Расслить", exact=True).click()
    with fx_page.expect_response(lambda r: f"/api/counterparties/{first['id']}/unmerge" in r.url) as unmerged:
        text = confirm(fx_page, "Да")
    assert "Вернуть карточку из слияния?" in text
    assert unmerged.value.ok, unmerged.value.text()

    expect(main.get_by_text("Карточка слита в")).to_have_count(0)
    back = fx_json(fx_get(fx_page, f"/counterparties/{first['id']}"))
    assert (back["is_active"], back["merged_into_counterparty_id"]) == (True, None), back


# Кейс: «Расслить» → «Отмена» оставляет карточку слитой
def test_unmerge_cancel(fx_page: Page, made):
    idno = unique_idno()
    survivor = made(tax_id=idno)
    dup = made(tax_id=idno)
    fx_json(fx_post(fx_page, "/counterparties/merge", {"survivor_id": survivor["id"], "duplicate_ids": [dup["id"]]}))

    card(fx_page, dup["id"])
    fx_page.locator("main").get_by_role("button", name="Расслить", exact=True).click()
    confirm(fx_page, "Отмена")

    expect(fx_page.locator("main")).to_contain_text(f"Карточка слита в #{survivor['id']}")
    api = fx_json(fx_get(fx_page, f"/counterparties/{dup['id']}"))
    assert api["merged_into_counterparty_id"] == survivor["id"]


# Кейс: Одинаковое имя без ИНН — только предложение в «Совпало имя»
def test_same_name_suggestion(fx_page: Page, made):
    name = f"{QA_PREFIX} Тёзка {stamp()}"
    made(name=name)
    made(name=name)

    with fx_page.expect_response(lambda r: "/api/counterparties/duplicates" in r.url) as resp:
        open_fx(fx_page, f"{LIST}/duplicates")
    by_name = resp.value.json()["name"]
    assert any({m["name"] for m in g["members"]} == {name} for g in by_name), by_name

    section = fx_page.locator("main div").filter(
        has=fx_page.get_by_role("heading", name=re.compile(r"^Совпало имя"))).last
    expect(section.get_by_role("heading", name=re.compile(r"^Совпало имя"))).not_to_have_text("Совпало имя · 0")
    expect(section).to_contain_text(name)


# Кейс: «Нам должны» — расшифровка совпадает с API
def test_balance_breakdown(fx_page: Page):
    debtors = [c for c in fx_json(fx_get(fx_page, "/counterparties/", {"with_balance": "true"}))
               if (c.get("balance") or {}).get("receivable")]
    if not debtors:
        pytest.skip("в BORD QA нет контрагента с долгом перед нами")
    cp = debtors[0]
    card(fx_page, cp["id"])
    bd = fx_json(fx_get(fx_page, f"/counterparties/{cp['id']}/balance/breakdown", {"kind": "receivable"}))

    tile = fx_page.locator("main").get_by_role("button").filter(has_text="Нам должны")
    expect(tile).to_contain_text(money(bd["total_mdl"]))
    tile.click()
    panel = fx_page.locator("main").filter(has_text="Нам должны — из чего сложилось")
    expect(panel).to_contain_text("Нам должны — из чего сложилось")
    for cur in bd["currencies"]:
        for group in cur["groups"]:
            expect(panel).to_contain_text(group["label"])
            for r in group["rows"]:
                expect(panel).to_contain_text(r["number"])
    expect(panel).to_contain_text(re.compile(r"Итого, MDL\s*" + money(bd["total_mdl"]).pattern))
