"""Блок 80. Интеграции — /admin/integrations и /admin/integrations/ecom.

Снято 25.11.2026 в видимом MCP-браузере:
- «Все интеграции BORD — здесь, по группам…»: группы = GET /api/admin/shell/integrations {groups[{key, title,
  about, items[{key, title, about, app, settings_url, connections[], state, state_words}]}]}; у каждой интеграции
  подпись состояния («не подключено» / «подключено» / «выключена» / «не включено» / «модуль не подключён»), подключения
  списком, ссылка «Настроить»; плагин CRM выключен — «Включить плагин» (crm …/plugins), модуль не подключён —
  «Подключить модуль» (/admin/billing);
- ECOM: GET /api/admin/ecom-link {connected, status, sites}; не подключено — «Подключить ECOM одним
  разрешением», кнопка «Подключить ECOM», что работает от связи (CRM, Facturi, WMS, Targeting).
Блок только читает: «Подключить ECOM» не нажимаем (уводит во вход ECOM).
"""
import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_get
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth


@pytest.fixture
def hub(auth_page: Page) -> tuple[Page, dict]:
    with auth_page.expect_response(lambda r: r.url.endswith("/api/admin/shell/integrations")) as resp:
        auth_page.goto("/admin/integrations")
    assert resp.value.ok, resp.value.status
    expect(auth_page.locator("main")).to_contain_text("Все интеграции BORD", timeout=20_000)
    settle(auth_page)
    return auth_page, resp.value.json()


def card(page: Page, title: str):
    """Карточка интеграции: div > (шапка: логотип, div(название, описание), состояние) + (ссылки / подсказка)."""
    return page.locator("main section div").get_by_text(title, exact=True).first.locator("xpath=ancestor::div[3]")


# Кейс: Группы и интеграции = API: заголовки групп, подпись состояния у каждой интеграции
@pytest.mark.smoke
def test_groups_and_states_match_api(hub):
    page, data = hub
    assert data["groups"], data
    main = page.locator("main")
    for group in data["groups"]:
        expect(main.get_by_text(group["title"], exact=True)).to_be_visible()
        for item in group["items"]:
            box = card(page, item["title"])
            expect(box, item["key"]).to_contain_text(item["state_words"])
            for conn in item["connections"]:
                name = conn.get("title") or conn.get("name")
                if name:
                    expect(box, f"{item['key']}: {name}").to_contain_text(name)


# Кейс: «Настроить» ведёт в настройки своей системы (settings_url)
def test_settings_links(hub):
    page, data = hub
    for group in data["groups"]:
        for item in group["items"]:
            if item.get("settings_url"):
                box = card(page, item["title"])
                # не готово к настройке: плагин CRM выключен → «Включить плагин», модуль не подключён → «Подключить модуль»
                if box.get_by_role("link", name="Включить плагин").count():
                    expect(box).to_contain_text("Плагин CRM не включён")
                    expect(box.get_by_role("link", name="Включить плагин")).to_have_attribute(
                        "href", "https://crm.bord.one/admin/plugins")
                    continue
                if box.get_by_role("link", name="Подключить модуль").count():
                    expect(box).to_contain_text("Модуль не подключён компании")
                    expect(box.get_by_role("link", name="Подключить модуль")).to_have_attribute("href", "/admin/billing")
                    continue
                hrefs = [a.get_attribute("href") for a in box.get_by_role("link", name="Настроить").all()]
                want = item["settings_url"]  # свои экраны хаба — относительной ссылкой
                assert want in hrefs or want.removeprefix("https://app.bord.one") in hrefs,                     f"{item['key']}: {want} нет среди {hrefs}"


# Кейс: ECOM не подключён — «Подключить ECOM» и список того, что даст связь
def test_ecom_link_screen(auth_page: Page):
    with auth_page.expect_response(lambda r: r.url.endswith("/api/admin/ecom-link")) as resp:
        auth_page.goto("/admin/integrations/ecom")
    link = resp.value.json()
    main = auth_page.locator("main")
    if link["connected"]:
        pytest.skip(f"ECOM подключён ({link['status']}) — экран подключения не показывается")
    expect(main).to_contain_text("Подключить ECOM одним разрешением", timeout=20_000)
    expect(main.get_by_role("button", name="Подключить ECOM")).to_be_enabled()
    for system in ("CRM", "Facturi", "WMS", "Targeting"):
        expect(main).to_contain_text(system)
