"""Блок 16. Биллинг и модули: app.bord.one/admin/subscription, /admin/modules (только чтение).

Снято 09.11.2026 (экран «Модули и оплата» заменён двумя):
- эталон — GET /api/subscription: modules[{key, name, description, niche,
  enabled, base, price | null, status, paid_until}], company_ready, trial_*;
  price = текущий тариф {plan_name, per_unit, unit_bani, units, amount_bani,
  on_request, plans[…]}; у модулей-надстроек (base: PMS → HDS, CLM → DMS,
  инциденты → безопасность) и бесплатных цены нет;
- «Биллинг» (/admin/subscription): сводка «В месяц, без НДС», баланс, карта;
  «Подписки» — строка на каждый модуль с ценой: название, «· <тариф>»,
  статус («Пробный период»), «N лей / мес», у тарифа за человека
  «(N чел. × M лей)»; кнопки «Оплатить картой», «Сменить тариф» (если тарифов
  больше одного), «Отменить»; ниже «История» (Счета / Оплаты / Баланс);
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


def lei(bani: int) -> str:
    """6 000 лей: тысячи через пробел (на странице — неразрывный)."""
    return r"\s".join(f"{bani // 100:,}".split(","))


def priced(data: dict) -> list[dict]:
    return [m for m in data["modules"] if m["enabled"] and m["price"] and m["price"]["amount_bani"]]


@pytest.fixture
def billing(auth_page: Page) -> tuple[Page, dict]:
    with auth_page.expect_response(lambda r: r.url.endswith(API)) as resp:
        auth_page.goto("/admin/subscription")
    assert resp.value.ok, resp.value.status
    expect(auth_page.locator("main").get_by_text("Подписки", exact=True)).to_be_visible(timeout=20_000)
    settle(auth_page)
    return auth_page, resp.value.json()


def subscription(page: Page, module: dict):
    """Строка подписки: <li> с названием модуля."""
    return page.locator("main li").filter(has=page.get_by_text(module["name"], exact=True))


# Кейс: Подписки совпадают с API
@pytest.mark.smoke
def test_subscriptions_match_api(billing):
    page, data = billing
    modules = priced(data)
    assert modules, "в BORD QA нет модулей с ценой — проверять нечего"

    expect(page.locator("main li").filter(has_text="Оплатить картой")).to_have_count(len(modules))
    for module in modules:
        price = module["price"]
        row = subscription(page, module)
        expect(row, module["key"]).to_have_count(1)
        expect(row).to_contain_text(f"· {price['plan_name']}")
        expect(row).to_contain_text(re.compile(lei(price["amount_bani"]) + r"\s+лей / мес"))
        if price["per_unit"] and price["units"]:
            # «пользователь» пишется «чел.», остальные единицы — как в API («3 рекламный аккаунт × …»)
            unit = "чел." if price["unit_name"] == "пользователь" else price["unit_name"]
            expect(row).to_contain_text(re.compile(
                rf"\({price['units']} {re.escape(unit)} ×\s+{lei(price['unit_bani'])}\s+лей\)"))


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

    for module in (m for m in data["modules"] if m["enabled"] and not (m["price"] and m["price"]["amount_bani"])):
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
