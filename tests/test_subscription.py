"""Блок 16. Модули и оплата: app.bord.one/admin/subscription (только чтение).

Снято 22.10.2026:
- эталон — GET /api/subscription: modules[{key, name, description, niche,
  enabled, price{amount_bani, interval_months, currency} | null, status,
  paid_until}], company_ready, trial_*;
- карточка модуля: название, описание, «Подключён», цена «3 600 лей / мес.»
  (amount_bani / 100) или «Цена по запросу», «Открыть» (внешние системы — в
  новой вкладке), у платных «Оплатить картой» и «Счёт на оплату», «Отключить»;
- нишевые модули (RMS, LMS) — отдельной карточкой «Нишевые решения» со
  ссылкой на /admin/subscription/niche;
- пока у компании нет юр. названия и IDNO (company_ready=false) — плашка «Для
  оплаты нужны реквизиты компании…» со ссылкой «Заполнить реквизиты» на
  мастер /admin/onboarding.

Оплату, счёт и отключение тесты НЕ нажимают. Единственная запись — реквизиты
компании в тесте плашки, возвращаются в любом исходе.
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_get, api_put
from helpers.modules import TILE_HREF
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

API = "/api/subscription"
COMPANY = "/api/onboarding/company"
BANNER = "Для оплаты нужны реквизиты компании (юр. название и IDNO) — счёт выставляется на них."


def price_text(price: dict | None) -> re.Pattern:
    if price is None:
        return re.compile("Цена по запросу")
    assert price["interval_months"] == 1 and price["currency"] == "MDL", f"новый вид цены: {price}"
    whole = f"{price['amount_bani'] // 100:,}".split(",")
    return re.compile(r"\s".join(whole) + r"\s+лей / мес\.")


@pytest.fixture
def sub(auth_page: Page) -> tuple[Page, dict]:
    with auth_page.expect_response(lambda r: r.url.endswith(API)) as resp:
        auth_page.goto("/admin/subscription")
    assert resp.value.ok, resp.value.status
    expect(auth_page.locator("main").get_by_role("link", name="Перейти к работе")).to_be_visible()
    settle(auth_page)
    return auth_page, resp.value.json()


def card(page: Page, module: dict):
    """Карточка модуля: блок с его описанием и кнопкой «Открыть»."""
    return page.locator("main").get_by_text(module["description"], exact=True).locator(
        "xpath=ancestor::*[.//a[normalize-space()='Открыть']][1]")


@pytest.mark.smoke
def test_modules_match_api(sub):
    page, data = sub
    regular = [m for m in data["modules"] if not m["niche"] and m["enabled"]]

    expect(page.locator("main").get_by_role("link", name="Открыть", exact=True)).to_have_count(len(regular))
    for module in regular:
        box = card(page, module)
        expect(box, module["key"]).to_contain_text(module["name"])
        expect(box).to_contain_text("Подключён")
        expect(box).to_contain_text(price_text(module["price"]))


def test_open_links(sub):
    page, data = sub

    for module in (m for m in data["modules"] if not m["niche"] and m["enabled"]):
        link = card(page, module).get_by_role("link", name="Открыть", exact=True)
        href = TILE_HREF.get(module["key"])
        assert href, f"новая система {module['key']!r}: допиши её адрес в helpers/modules.py"
        expect(link, module["key"]).to_have_attribute("href", href)
        if href.startswith("http"):
            expect(link).to_have_attribute("target", "_blank")


def test_pay_buttons_only_for_priced_modules(sub):
    """Кнопки оплаты — только у модулей с ценой; «Отключить» — у каждого подключённого. Не нажимаем."""
    page, data = sub

    for module in (m for m in data["modules"] if not m["niche"] and m["enabled"]):
        box = card(page, module)
        priced = 1 if module["price"] else 0
        expect(box.get_by_role("button", name="Оплатить картой"), module["key"]).to_have_count(priced)
        expect(box.get_by_role("button", name="Счёт на оплату"), module["key"]).to_have_count(priced)
        expect(box.get_by_role("button", name="Отключить")).to_have_count(1)


def test_niche_modules_on_separate_page(sub):
    page, data = sub
    niche = [m for m in data["modules"] if m["niche"] and m["enabled"]]
    link = page.locator("main").get_by_role("link", name=re.compile("^Нишевые решения"))

    expect(link).to_have_attribute("href", "/admin/subscription/niche")
    expect(link).to_contain_text("Подключено: " + ", ".join(m["name"] for m in niche) + ".")
    link.click()

    expect(page).to_have_url(re.compile(r"/admin/subscription/niche$"))
    for module in niche:
        expect(page.locator("main"), module["key"]).to_contain_text(module["name"])


@pytest.mark.writes_data
def test_requisites_banner_follows_company(sub):
    page, data = sub
    before = api_get(page, "/api/onboarding").json()["company"]
    banner = page.locator("main").get_by_text(BANNER)
    if data["company_ready"]:
        pytest.skip("у BORD QA уже заполнены реквизиты — плашки нет")

    expect(banner).to_be_visible()
    expect(page.locator("main").get_by_role("link", name="Заполнить реквизиты")).to_have_attribute("href", "/admin/onboarding")
    try:
        assert api_put(page, COMPANY, {"name": before["name"], "legal_name": "QA Autotest SRL", "idno": "1003600012345"}).ok
        with page.expect_response(lambda r: r.url.endswith(API)) as resp:
            page.reload()
        assert resp.value.json()["company_ready"] is True
        expect(page.locator("main").get_by_role("link", name="Перейти к работе")).to_be_visible()
        expect(banner).to_have_count(0)
    finally:
        restored = api_put(page, COMPANY, {"name": before["name"], "legal_name": before.get("legal_name") or "", "idno": before.get("idno") or ""})
        assert restored.ok, restored.status
