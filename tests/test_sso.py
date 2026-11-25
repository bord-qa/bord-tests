"""Блок 17. SSO: системы *.bord.one открываются из сессии app.bord.one без пароля.

Снято 22.10.2026: после входа в app.bord.one ставится кука bord_sso на
.bord.one, по ней каждая система получает свой вход. Проверяем все адреса из
helpers/modules.py на домене bord.one: нет формы пароля, не ушли на /login, в
интерфейсе видна организация «BORD QA».
- lms.bord.one и rms.bord.one выключены намеренно (29.10.2026 домены убраны из DNS,
  плитки на стартовой пока остались) — не проверяем;
- app.ecom.md, app.facturi.md, app.targeting.md — другие домены, кука до них
  не доходит, там своя форма входа (так ли задумано — вопрос открыт, не тестируем).
"""
import re

import pytest
from playwright.sync_api import Browser, Page, expect

from helpers.api import api_get
from helpers.modules import TILE_HREF
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

DISABLED = ("lms.bord.one", "rms.bord.one")
HOST_MODULE = {"dms.bord.one": "dms", "crm.bord.one": "crm", "hrm.bord.one": "hrm", "wms.bord.one": "wms",
               "mms.bord.one": "mms", "hds.bord.one": "helpdesk", "fms.bord.one": "finance", "ads.bord.one": "marketing"}
SYSTEMS = sorted({url for url in TILE_HREF.values()
                  if url.startswith("https://") and ".bord.one/" in url and url.split("/")[2] not in DISABLED})


def ids(url: str) -> str:
    return url.removeprefix("https://").removesuffix("/admin")


# Кейс: Система открывается без повторного пароля
@pytest.mark.smoke
@pytest.mark.parametrize("url", SYSTEMS, ids=[ids(u) for u in SYSTEMS])
def test_system_opens_without_password(auth_page: Page, url):
    auth_page.goto(url)
    settle(auth_page, timeout=15_000)

    host = url.split("/")[2]
    assert auth_page.url.startswith(f"https://{host}/"), f"увело на {auth_page.url}"
    # модуль выключен в BORD QA (25.11 — DMS): система пишет «… не подключён» — это не про вход; пропускаем,
    # только если модуль и правда выключен по /api/subscription
    if auth_page.get_by_text(re.compile("модуль .* не подключён")).count():
        key = HOST_MODULE.get(host)
        hub = auth_page.context.new_page()  # токен BORD лежит в localStorage app.bord.one, не системы
        hub.goto("https://app.bord.one/admin")
        settle(hub)
        modules = {m["key"]: m["enabled"] for m in api_get(hub, "/api/subscription").json()["modules"]}
        hub.close()
        assert key and modules.get(key) is False, f"{host}: «не подключён», но модуль {key} включён: {modules.get(key)}"
        pytest.skip(f"{host}: модуль {key} выключен в BORD QA")
    assert "/login" not in auth_page.url, f"{url}: попросили войти — {auth_page.url}"
    expect(auth_page.locator("input[type=password]")).to_have_count(0)
    # организация: видимый текст «BORD QA» или (с ~20.11 в CRM/DMS) значок с title «Организация: BORD QA · <тариф>»,
    # который на узком экране спрятан
    org = auth_page.locator("text=BORD QA >> visible=true").or_(auth_page.locator('[title^="Организация: BORD QA"]'))
    expect(org.first).to_be_attached(timeout=15_000)


# Кейс: Гость не получает данных
def test_guest_gets_no_data(browser: Browser, browser_context_args):
    """Без сессии BORD система данных не показывает — просит войти."""
    guest = browser.new_context(**{k: v for k, v in browser_context_args.items() if k != "storage_state"}).new_page()
    try:
        guest.goto("https://crm.bord.one/admin")
        settle(guest, timeout=15_000)

        expect(guest.get_by_text("BORD QA")).to_have_count(0)
        on_login = "/login" in guest.url or guest.locator("input[type=password]").count() > 0 \
            or guest.get_by_role("button", name="Войти через BORD SSO").count() > 0
        assert on_login, f"гость не увидел входа: {guest.url}"
    finally:
        guest.context.close()
