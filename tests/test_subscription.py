"""Блок 16. Биллинг и модули: app.bord.one/admin/subscription, /admin/modules (только чтение).

Снято 09.11.2026 (экран «Модули и оплата» заменён двумя):
- эталон — GET /api/subscription: modules[{key, name, description, niche,
  enabled, base, price | null, status, paid_until}], company_ready, trial_*;
  price = текущий тариф {plan_name, per_unit, unit_bani, units, amount_bani,
  on_request, plans[…]}; у модулей-надстроек (base: PMS → HDS, CLM → DMS,
  инциденты → безопасность) и бесплатных цены нет;
- «Биллинг» (/admin/subscription): сводка «В месяц, без НДС», баланс, карта;
  «Подписки» — строка на каждый модуль с ценой (с 26.11 — колонки с подписями
  «Тариф / Статус / Пользователи / Цена / Ближайшее списание», подписи видны
  только на узком экране): тариф, статус («Пробный период»), «N чел.»,
  «N лей / мес» и под ней у тарифа за человека «N × M лей»; кнопки «Оплатить картой», «Сменить тариф» (если тарифов
  больше одного), «Отменить»; ниже «История» (Счета / Оплаты / Баланс);
  с 10.11 цена за единицу без числа единиц (Маркетинг: units/amount_bani = null)
  пишется «3 600 лей за рекламный аккаунт»;
  с 12.11 «Биллинг» грузит GET /api/subscription/billing (overview, after_trial
  [{key, name, price}], invoices…), а не /api/subscription — эталоном модулей
  остаётся /api/subscription (там есть enabled), страница ждёт ответа billing;
- «Включение модулей» (/admin/modules): карточка на каждый модуль — название,
  описание, «Включён»; выключить можно самому, включает платформенный админ.

Оплату, смену тарифа, отмену и выключение модулей тесты НЕ нажимают.
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_get
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

API = "/api/subscription"
BILLING_API = "/api/subscription/billing"


def lei(bani: int) -> str:
    """6 000 лей: тысячи через пробел (на странице — неразрывный)."""
    return r"\s".join(f"{bani // 100:,}".split(","))


def has_price(module: dict) -> bool:
    """Платный модуль: есть сумма в месяц или цена за единицу (у «Маркетинга» с 10.11
    единиц нет — amount_bani=null, а на странице «3 600 лей за рекламный аккаунт»)."""
    price = module["price"]
    return bool(price and (price["amount_bani"] or (price["per_unit"] and price["unit_bani"])))


def priced(data: dict) -> list[dict]:
    return [m for m in data["modules"] if m["enabled"] and has_price(m)]


@pytest.fixture
def billing(auth_page: Page) -> tuple[Page, dict]:
    with auth_page.expect_response(lambda r: r.url.endswith(BILLING_API)) as resp:
        auth_page.goto("/admin/subscription")
    assert resp.value.ok, resp.value.status
    expect(auth_page.locator("main").get_by_text("Подписки", exact=True)).to_be_visible(timeout=20_000)
    settle(auth_page)
    return auth_page, api_get(auth_page, API).json()


def subscription(page: Page, module: dict):
    """Строка подписки: <li> с названием модуля."""
    return page.locator("main li").filter(has=page.get_by_text(module["name"], exact=True))


def cell(row, label: str):
    """Значение колонки строки подписки по её подписи («Тариф», «Цена»…)."""
    column = row.locator(":scope > div").filter(has=row.page.locator("span", has_text=re.compile(rf"^{label}$")))
    return column.locator(":scope > div")


# Кейс: Подписки совпадают с API
@pytest.mark.smoke
def test_subscriptions_match_api(billing):
    page, data = billing
    modules = priced(data)
    assert modules, "в BORD QA нет модулей с ценой — проверять нечего"

    # строки «Подписок» рисуются из /billing → after_trial; каждая должна быть модулем из /api/subscription
    billed = api_get(page, BILLING_API).json()["after_trial"]
    expect(page.locator("main li").filter(has_text="Оплатить картой")).to_have_count(len(billed))
    orphan = sorted({b["key"] for b in billed} - {m["key"] for m in modules})
    for module in modules:
        price = module["price"]
        row = subscription(page, module)
        expect(row, module["key"]).to_have_count(1)
        expect(cell(row, "Тариф"), module["key"]).to_have_text(price["plan_name"])
        cost = cell(row, "Цена")
        if not price["amount_bani"]:
            # цена за единицу без числа единиц: «3 600 лей за рекламный аккаунт»
            expect(cost).to_contain_text(re.compile(
                rf"{lei(price['unit_bani'])}\s+лей за {re.escape(price['unit_name'])}"))
            continue
        expect(cost).to_contain_text(re.compile(lei(price["amount_bani"]) + r"\s+лей / мес"))
        if price["per_unit"] and price["units"]:
            # «пользователь» пишется «чел.», остальные единицы — как в API
            unit = "чел." if price["unit_name"] == "пользователь" else price["unit_name"]
            expect(cell(row, "Пользователи"), module["key"]).to_have_text(f"{price['units']} {unit}")
            expect(cost).to_contain_text(re.compile(rf"{price['units']} ×\s+{lei(price['unit_bani'])}\s+лей"))
    if orphan == ["cms"]:
        pytest.xfail("bord-669: подписка CMS есть в биллинге, модуля CMS нет в «Модулях» и /api/subscription")
    assert not orphan, f"подписки без модуля в /api/subscription: {orphan}"


# Кейс: Кнопки подписки: оплата и отмена у всех, смена тарифа — если есть из чего выбрать
def test_subscription_buttons(billing):
    page, data = billing

    for module in priced(data):
        row = subscription(page, module)
        plans = module["price"].get("plans") or []
        expect(row.get_by_role("button", name="Оплатить картой"), module["key"]).to_have_count(1)
        expect(row.get_by_role("button", name="Отменить"), module["key"]).to_have_count(1)
        expect(row.get_by_role("button", name="Сменить тариф"), module["key"]).to_have_count(1 if len(plans) > 1 else 0)


# Кейс: Модули без цены в подписках не показываются
def test_free_modules_not_in_subscriptions(billing):
    page, data = billing

    for module in (m for m in data["modules"] if m["enabled"] and not has_price(m)):
        expect(subscription(page, module), module["key"]).to_have_count(0)


# Кейс: «Включение модулей» показывает все модули организации
def test_modules_page_matches_api(auth_page: Page):
    auth_page.goto("/admin/modules")
    main = auth_page.locator("main")
    expect(main.get_by_text("Включён", exact=True).first).to_be_visible(timeout=20_000)
    settle(auth_page)
    data = api_get(auth_page, API).json()
    enabled = [m for m in data["modules"] if m["enabled"]]

    expect(main.get_by_text("Включён", exact=True)).to_have_count(len(enabled))
    for module in enabled:
        card = main.get_by_text(module["description"], exact=True).locator(
            "xpath=ancestor::*[.//*[normalize-space()='Включён']][1]")
        expect(card, module["key"]).to_contain_text(module["name"])
        expect(card).to_contain_text("Включён")
