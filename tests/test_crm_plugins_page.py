"""Блок 85. CRM — страница «Плагины» (/admin/plugins) и «Чат-бот» (/admin/crm/chat-bot) — только чтение.

Снято 25.11.2026 в видимом MCP-браузере:
- «Плагины»: карточка на каждый плагин GET /plugins/ {plugin_key, name, description, is_enabled}: название, описание,
  метка «Включён» / «Отключён», у части — «Настроить»;
- «Чат-бот» (плагин chat_bot): «Бот: <имя>», «Добавить бота»; три замка — «Главный рубильник» (= settings.is_enabled,
  «Включен» / «Выключен»), «Режим» (mode: suggest — подсказка оператору), каналы; пока рубильник выключен —
  «Клиентам ничего не уходит»; вкладки «Запуск», «Ответы», «База знаний», «Проверка», «Диалоги», «Ошибки бота»,
  «Комментарии», «Результат». GET /chat-bot/settings.
Ничего не переключаем: плагины включают и другие (тест меню их учитывает), бот при включении отвечает живым клиентам.
"""
import pytest
from playwright.sync_api import Page, expect

from helpers.crm import CRM, crm_get, crm_ready

pytestmark = pytest.mark.requires_auth


# Кейс: Карточки «Плагинов» = /plugins/: название, описание, «Включён» / «Отключён»
@pytest.mark.smoke
def test_plugins_match_api(auth_page: Page):
    auth_page.goto(CRM + "/admin/plugins")
    crm_ready(auth_page)
    plugins = crm_get(auth_page, "/plugins/").json()
    assert plugins, "нет плагинов"
    main = auth_page.locator("main")
    expect(main.get_by_text(plugins[0]["name"], exact=True).first).to_be_visible(timeout=15_000)
    for p in plugins:
        title = main.get_by_text(p["name"], exact=True).first
        expect(title, p["plugin_key"]).to_be_visible()
        card = title.locator("xpath=ancestor::div[.//*[normalize-space()='Включён' or normalize-space()='Отключён']][1]")
        expect(card, p["plugin_key"]).to_contain_text("Включён" if p["is_enabled"] else "Отключён")
        if p.get("description"):
            expect(card, p["plugin_key"]).to_contain_text(p["description"][:40])


# Кейс: «Чат-бот» — главный рубильник и режим = /chat-bot/settings; выключенный бот «Клиентам ничего не уходит»
def test_chat_bot_settings_shown(auth_page: Page):
    auth_page.goto(CRM + "/admin/crm/chat-bot")
    crm_ready(auth_page)
    plugins = {p["plugin_key"]: p["is_enabled"] for p in crm_get(auth_page, "/plugins/").json()}
    if not plugins.get("chat_bot"):
        pytest.skip("плагин «Чат-бот» в BORD QA выключен")
    settings = crm_get(auth_page, "/chat-bot/settings").json()
    main = auth_page.locator("main")
    expect(main).to_contain_text("Бот:", timeout=15_000)
    expect(main).to_contain_text(settings["name"])
    switch = main.locator("div").filter(has_text="Главный рубильник").filter(
        has=auth_page.get_by_text("Включен", exact=True).or_(auth_page.get_by_text("Выключен", exact=True))).last
    expect(switch).to_contain_text("Включен" if settings["is_enabled"] else "Выключен")
    if not settings["is_enabled"]:
        expect(main).to_contain_text("Клиентам ничего не уходит")
    for tab in ("Запуск", "Ответы", "База знаний", "Проверка", "Диалоги"):
        expect(main.get_by_role("button", name=tab).first, tab).to_be_visible()
