"""Сквозная проверка: каждый раздел платформы открывается без ошибок сервера.

Ловит поломку, которой не видно по одному экрану. 21.10.2026 одно сохранение
раскладки на стартовой (ecom-100148) уронило в 500 сразу пять API организации:
карта, чек-листы задач и реестра доступов, «Компания», «Модули и оплата».
Тест на каждый раздел: ни один запрос /api/* не ответил 5xx и на экране нет
«Ошибка 500» / «Не удалось загрузить».
"""
import pytest
from playwright.sync_api import Page, expect

from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

ROUTES = [
    "/admin",
    "/admin/tasks",
    "/admin/security",
    "/admin/security/incidents",
    "/admin/security/incidents/vault",
    "/admin/domains",
    "/admin/finance",
    "/admin/finance/invoices",
    "/admin/finance/debtors",
    "/admin/counterparties",
    "/admin/products",
    "/admin/settings/company",
    "/admin/users",
    "/admin/access",
    "/admin/roles",
    "/admin/subscription",
    "/admin/settings/custom-fields",
    "/admin/developers",
    "/admin/profile",
]


# Кейс: Раздел открывается без ошибок сервера
@pytest.mark.smoke
@pytest.mark.parametrize("route", ROUTES)
def test_section_opens_without_server_errors(auth_page: Page, route):
    failed = []
    auth_page.on(
        "response",
        lambda r: failed.append(f"{r.request.method} {r.status} {r.url.split('.one', 1)[-1]}")
        if "/api/" in r.url and r.status >= 500 else None,
    )

    auth_page.goto(route)
    settle(auth_page)

    main = auth_page.locator("main")
    expect(main).to_be_visible()
    assert not failed, f"{route}: API ответили 5xx: {sorted(set(failed))}"
    expect(main.get_by_text("Ошибка 500")).to_have_count(0)
    expect(main.get_by_text("Не удалось загрузить")).to_have_count(0)
