"""Блок 8. Продукты: app.bord.one/admin/products.

Снято 22.10.2026:
- список: GET /api/products/?q=&archived=false|true → {items: […]}; таблица «Название, Код,
  Вид, Где используется», под названием — описание; порядок по «Порядку»
  (меньше — выше); пусто по поиску — «Ничего не нашлось. Попробуйте код…»;
- «Добавить продукт» → окно «Новый продукт» (helpers/modal.py): Название,
  Код, Вид (Продаётся = sold / Внутренний = internal), Сайт, Порядок,
  Описание → POST /api/products/ → 201, URL ?id=<id>, окно карточки
  «О продукте» (Код, Порядок в списках, Сайт, Описание);
- код: фронт приводит к нижнему регистру и проверяет сам, без запроса
  («Укажите код продукта» / «Код — латиница, цифры, «_» и «-», до 50
  символов…»); сервер повторяет те же проверки (400); занятый код, в том числе
  архивным продуктом, → 409 «Код «…» уже занят другим продуктом (в том числе
  в архиве)»;
- «Изменить» → PATCH /api/products/<id>; «В архив» → окно «Убрать «…» в архив?
  …а код останется за ним.» → POST …/<id>/archive; «Вернуть из архива» →
  POST …/<id>/unarchive.

Удаления продуктов в API нет — свои «QA Autotest …» тесты убирают в архив;
код у каждого продукта свой (из времени), архивные коды прошлых прогонов не мешают.
"""
import re
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_get, api_post
from helpers.modal import close_modal, modal
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

API = "/api/products/"
NAME_PREFIX = "QA Autotest"
CODE_RULE = "Код — латиница, цифры, «_» и «-», до 50 символов, начинается с буквы или цифры"


def unique() -> str:
    return str(int(time.time() * 1000))


def create_product(page: Page, **fields) -> dict:
    stamp = unique()
    data = {"name": f"{NAME_PREFIX} {stamp}", "code": f"qa-{stamp}", "kind": "sold", **fields}
    resp = api_post(page, API, data)
    assert resp.status == 201, resp.text()
    return resp.json()


def archive_qa_products(page: Page):
    resp = api_get(page, f"{API}?q={NAME_PREFIX}&archived=false")
    for product in resp.json()["items"] if resp.ok else []:
        if product["name"].startswith(NAME_PREFIX):
            api_post(page, f"{API}{product['id']}/archive", {})


@pytest.fixture
def products(auth_page: Page):
    auth_page.goto("/admin/products")
    expect(auth_page.locator("main").get_by_role("button", name="Добавить продукт").first).to_be_visible()
    settle(auth_page)
    yield auth_page
    archive_qa_products(auth_page)


def rows(page: Page):
    return page.locator("main table tbody tr")


def find(page: Page, text: str):
    with page.expect_response(lambda r: r.url.split("?")[0].endswith(API) and "q=" in r.url):
        page.locator("main").get_by_placeholder("Название или код").fill(text)


def open_new(page: Page):
    page.locator("main").get_by_role("button", name="Добавить продукт").first.click()
    return modal(page, "Новый продукт")


def name_input(form):
    return form.get_by_placeholder("ECOM", exact=True)


def code_input(form):
    return form.get_by_placeholder("ecom", exact=True)


def fact(card, term: str):
    return card.locator("dt", has_text=re.compile(rf"^{term}$")).locator("xpath=following-sibling::dd[1]")


# Кейс: Новый продукт: карточка и строка в списке
@pytest.mark.writes_data
def test_product_create_shows_card_and_row(products: Page):
    stamp = unique()
    name = f"{NAME_PREFIX} {stamp}"
    form = open_new(products)
    name_input(form).fill(name)
    code_input(form).fill(f"QA-Auto_{stamp}")
    form.get_by_role("button", name="Продаётся").click()
    products.get_by_role("option", name="Внутренний").click()
    form.get_by_placeholder("app.ecom.md").fill("qa.example.com")
    form.get_by_role("spinbutton").fill("7")
    form.locator("textarea").fill("описание автотеста")

    with products.expect_response(lambda r: r.url.endswith(API) and r.request.method == "POST") as created:
        form.get_by_role("button", name="Сохранить").click()

    assert created.value.status == 201, created.value.text()
    product = created.value.json()
    code = f"qa-auto_{stamp}"
    assert created.value.request.post_data_json == {
        "name": name, "code": code, "kind": "internal",
        "website": "qa.example.com", "sort_order": 7, "description": "описание автотеста",
    }, "код уходит в нижнем регистре, остальное — как введено"
    expect(products).to_have_url(re.compile(rf"\?id={product['id']}$"))
    card = modal(products, name)
    expect(card).to_contain_text("Внутренний")
    expect(fact(card, "Код")).to_have_text(code)
    expect(fact(card, "Порядок в списках")).to_have_text("7")
    expect(fact(card, "Сайт")).to_have_text("qa.example.com")
    # описание — отдельный блок под реквизитами, без dt/dd
    expect(card.get_by_text("описание автотеста", exact=True)).to_be_visible()
    expect(card).to_contain_text(f"№ {product['id']}")

    row = rows(products).filter(has_text=name)
    expect(row).to_have_count(1)
    expect(row.locator("td").nth(0)).to_contain_text("описание автотеста")
    expect(row.locator("td").nth(1)).to_have_text(code)
    expect(row.locator("td").nth(2)).to_have_text("Внутренний")


# Кейс: Код проверяется до отправки
@pytest.mark.parametrize(
    ("code", "message"),
    [("", "Укажите код продукта"), ("код с пробелом", CODE_RULE), ("-abc", CODE_RULE)],
    ids=["пусто", "кириллица и пробел", "с дефиса"],
)
def test_code_checked_before_request(products: Page, code, message):
    posts = []
    products.on("request", lambda r: posts.append(r.url) if r.method == "POST" and r.url.endswith(API) else None)
    form = open_new(products)
    name_input(form).fill(f"{NAME_PREFIX} {unique()}")
    code_input(form).fill(code)

    form.get_by_role("button", name="Сохранить").click()

    expect(form.get_by_text(message, exact=True)).to_be_visible()
    assert posts == [], "форма отправила продукт с неверным кодом"


# Кейс: API не принимает неверный код
@pytest.mark.parametrize("code", ["код с пробелом", "-abc", "a" * 51, ""], ids=["кириллица", "с дефиса", "51 символ", "пусто"])
def test_api_rejects_bad_code(products: Page, code):
    resp = api_post(products, API, {"name": f"{NAME_PREFIX} {unique()}", "code": code, "kind": "sold"})

    assert resp.status == 400, f"код {code!r} принят: {resp.status}"
    assert resp.json()["detail"] == CODE_RULE


# Кейс: Повторный код не принимается
@pytest.mark.writes_data
@pytest.mark.parametrize("archived", [False, True], ids=["у действующего", "у архивного"])
def test_duplicate_code_blocked(products: Page, archived):
    existing = create_product(products)
    if archived:
        assert api_post(products, f"{API}{existing['id']}/archive", {}).ok
    form = open_new(products)
    name_input(form).fill(f"{NAME_PREFIX} дубль {unique()}")
    code_input(form).fill(existing["code"])

    with products.expect_response(lambda r: r.url.endswith(API) and r.request.method == "POST") as created:
        form.get_by_role("button", name="Сохранить").click()

    assert created.value.status == 409
    expect(form.get_by_text(f"Код «{existing['code']}» уже занят другим продуктом (в том числе в архиве)")).to_be_visible()
    for flag in ("false", "true"):
        same = api_get(products, f"{API}?q={existing['code']}&archived={flag}").json()["items"]
        assert [p["id"] for p in same if p["code"] == existing["code"]] in ([], [existing["id"]])


# Кейс: Порядок сортировки поднимает продукт выше
@pytest.mark.writes_data
def test_sort_order_lifts_product(products: Page):
    stamp = unique()
    low = create_product(products, name=f"{NAME_PREFIX} {stamp} B", code=f"qa-{stamp}-b", sort_order=9)
    high = create_product(products, name=f"{NAME_PREFIX} {stamp} A", code=f"qa-{stamp}-a", sort_order=1)

    find(products, stamp)

    expect(rows(products)).to_have_count(2)
    expect(rows(products).locator("td:first-child")).to_contain_text([high["name"], low["name"]])


# Кейс: Поиск по названию и коду
@pytest.mark.writes_data
def test_search_by_name_and_code(products: Page):
    product = create_product(products)
    other = create_product(products)

    for query in (product["name"], product["code"], product["code"].upper()):
        find(products, query)
        expect(rows(products).filter(has_text=product["name"]), query).to_have_count(1)
        expect(rows(products).filter(has_text=other["name"]), query).to_have_count(0)

    find(products, f"нет-такого-{unique()}")
    expect(rows(products)).to_have_count(0)
    expect(products.locator("main").get_by_text("Ничего не нашлось. Попробуйте код или другое написание названия.")).to_be_visible()


# Кейс: Изменение продукта
@pytest.mark.writes_data
def test_edit_product(products: Page):
    product = create_product(products)
    new_name = f"{product['name']} изм."
    find(products, product["code"])
    rows(products).filter(has_text=product["name"]).click()
    modal(products, product["name"]).get_by_role("button", name="Изменить").click()

    form = modal(products, f"Изменить: {product['name']}")
    expect(code_input(form)).to_have_value(product["code"])
    name_input(form).fill(new_name)
    form.get_by_role("button", name="Продаётся").click()
    products.get_by_role("option", name="Внутренний").click()
    with products.expect_response(lambda r: r.url.endswith(f"{API}{product['id']}") and r.request.method == "PATCH") as patched:
        form.get_by_role("button", name="Сохранить").click()

    assert patched.value.ok, patched.value.text()
    expect(modal(products, new_name)).to_contain_text("Внутренний")
    saved = api_get(products, f"{API}{product['id']}").json()
    assert (saved["name"], saved["kind"], saved["code"]) == (new_name, "internal", product["code"])
    close_modal(products, new_name)
    expect(rows(products).filter(has_text=new_name).locator("td").nth(2)).to_have_text("Внутренний")


# Кейс: В архив и обратно
@pytest.mark.writes_data
def test_archive_and_restore(products: Page):
    product = create_product(products)
    main = products.locator("main")
    find(products, product["code"])
    rows(products).filter(has_text=product["name"]).click()

    modal(products, product["name"]).get_by_role("button", name="В архив").click()
    dialog = products.get_by_role("dialog", name=re.compile(rf"Убрать «{re.escape(product['name'])}» в архив\?"))
    expect(dialog).to_contain_text("код останется за ним")
    with products.expect_response(lambda r: r.url.endswith(f"{product['id']}/archive")) as archived:
        dialog.get_by_role("button", name="Да").click()

    assert archived.value.ok
    assert api_get(products, f"{API}{product['id']}").json()["archived"] is True
    close_modal(products, product["name"])
    expect(rows(products).filter(has_text=product["name"])).to_have_count(0)

    main.get_by_role("button", name="Архив", exact=True).click()
    row = rows(products).filter(has_text=product["name"])
    expect(row).to_have_count(1)
    row.click()
    with products.expect_response(lambda r: r.url.endswith(f"{product['id']}/unarchive")) as restored:
        modal(products, product["name"]).get_by_role("button", name="Вернуть из архива").click()

    assert restored.value.ok
    assert api_get(products, f"{API}{product['id']}").json()["archived"] is False
    close_modal(products, product["name"])
    main.get_by_role("button", name="Действующие").click()
    expect(rows(products).filter(has_text=product["name"])).to_have_count(1)


# Кейс: Отправку в архив можно отменить
@pytest.mark.writes_data
def test_archive_can_be_cancelled(products: Page):
    product = create_product(products)
    find(products, product["code"])
    rows(products).filter(has_text=product["name"]).click()

    modal(products, product["name"]).get_by_role("button", name="В архив").click()
    products.get_by_role("dialog", name=re.compile("в архив\\?")).get_by_role("button", name="Отмена").click()

    expect(modal(products, product["name"]).get_by_role("button", name="В архив")).to_be_visible()
    assert api_get(products, f"{API}{product['id']}").json()["archived"] is False
