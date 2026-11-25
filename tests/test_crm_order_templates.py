"""Блок 84. CRM — «Шаблоны заказа» (/admin/crm/order-templates, ядро с 12.11).

Снято 25.11.2026 в видимом MCP-браузере:
- в шапке «Excel», «Загрузить», «Новый шаблон»; слева список с «Поиск шаблона», справа — форма;
  пусто — «Шаблонов пока нет. Создайте шаблон или загрузите их из Excel.»;
- форма встроена в экран: «Название шаблона» (placeholder «Например, BELLA SARA 7016»), «Описание»
  («Необязательно»), таблица «№ · Товар · Артикул · Кол-во · Цена», поиск товара «Добавить товар: начните вводить
  название» (GET /wms-products/search?q= — товары каталога, подсказки li), «Строк: N · всего шт.: N», «Сохранить» →
  POST /order-templates {name, description, items[{product_name, quantity, unit_price, product_sku…}]};
- у сохранённого: «Копировать», «Этот шаблон в Excel», «Удалить шаблон» → «Удалить шаблон «…»? Сделки, куда он уже
  добавлен, не изменятся.» → «Удалить» → DELETE /order-templates/<id>.
Товары в BORD QA — из каталога ECOM-подключения (чужая ручная проверка): тест берёт первый найденный через API,
нет товаров — skip. Шаблон свой «QA Autotest …», удаляется через UI (и API в уборке).
Пустой шаблон (без товаров) интерфейс сохраняет — спорно, не тестируем (спросить юзера).
"""
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import CRM, QA_PREFIX, crm_delete, crm_get, crm_ready
from helpers.ui import screen_button

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/order-templates"


@pytest.fixture
def templates(auth_page: Page):
    auth_page.goto(CRM + PAGE)
    crm_ready(auth_page)
    yield auth_page
    for t in all_templates(auth_page):
        if t["name"].startswith(QA_PREFIX):
            crm_delete(auth_page, f"/order-templates/{t['id']}")


def all_templates(page: Page) -> list[dict]:
    data = crm_get(page, "/order-templates").json()
    return data if isinstance(data, list) else data.get("items", [])


def any_product(page: Page) -> dict:
    for q in ("Xiaomi", "Смартфон", "Samsung", "Чехол"):  # поиск по словам, «a» ничего не находит
        found = crm_get(page, "/wms-products/search", {"q": q, "limit": 10}).json()
        items = found if isinstance(found, list) else found.get("items", [])
        if items:
            return items[0]
    pytest.skip("в каталоге BORD QA нет товаров для шаблона")


# Кейс: Шаблон с товаром из поиска → POST /order-templates = форма; в списке; «Удалить шаблон» с подтверждением
@pytest.mark.writes_data
def test_template_create_and_delete(templates: Page):
    page = templates
    product = any_product(page)
    pname = product.get("name") or product.get("product_name")
    name = f"{QA_PREFIX} Шаблон {int(time.time() * 1000) % 10**8}"

    screen_button(page, "Новый шаблон", exact=True).click()
    page.get_by_placeholder("Добавить товар: начните вводить название").fill(pname)
    page.locator("li").filter(has_text=pname).first.click()
    expect(page.locator("main")).to_contain_text("Строк: 1")
    page.get_by_placeholder("Например, BELLA SARA 7016").fill(name)
    page.get_by_placeholder("Необязательно").fill("QA: автотест")
    with page.expect_response(lambda r: r.url.endswith("/order-templates") and r.request.method == "POST") as resp:
        page.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    sent = resp.value.request.post_data_json
    assert (sent["name"], sent["description"], len(sent["items"]), sent["items"][0]["product_name"],
            sent["items"][0]["quantity"]) == (name, "QA: автотест", 1, pname, 1), sent
    saved = next(t for t in all_templates(page) if t["name"] == name)
    expect(page.locator("main").get_by_text(name, exact=True).first).to_be_visible()

    page.locator('button[title="Удалить шаблон"], button[aria-label="Удалить шаблон"]').first.click()
    ask = page.locator("[data-bord-dialogs] div.fixed.inset-0:visible, div.fixed.inset-0:visible").last
    expect(ask).to_contain_text(f"Удалить шаблон «{name}»? Сделки, куда он уже добавлен, не изменятся.")
    with page.expect_response(lambda r: r.url.endswith(f"/order-templates/{saved['id']}")
                              and r.request.method == "DELETE") as gone:
        ask.get_by_role("button", name="Удалить", exact=True).click()
    assert gone.value.ok, gone.value.text()
    assert name not in [t["name"] for t in all_templates(page)]
    expect(page.locator("main").get_by_text(name, exact=True)).to_have_count(0)
