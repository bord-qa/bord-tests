"""Блок 81. Утренняя сводка — /admin/settings/digest.

Снято 25.11.2026 в видимом MCP-браузере:
- GET /api/admin/digest {settings{enabled, all_members, days, hour, timezone, channels, blocks}, summary, can_manage,
  me{enabled, scope, receives}, blocks[{key, module, module_label, title, description}], people…};
- экран: «Сводка компании выключена», «Присылать мне», «Включить сводку для компании», дни Пн…Вс, час, каналы
  (Колокольчик / Пуш / Telegram / Письмо / Группа компании), «Блоки» (модуль + название = blocks), «Люди» —
  участники с «Получает / По умолчанию / Не получает» и «Свои числа / Вся компания»;
- в шапке «Предпросмотр» (GET /admin/digest/preview) и «Отправить тест» (POST /admin/digest/test).
Сводку компании НЕ включаем (уйдёт всем участникам), «Отправить тест» не жмём; «Присылать мне» (PUT /admin/digest/me)
переключаем у себя и возвращаем.
"""
import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_get, api_put
from helpers.ui import screen_button
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

API = "/api/admin/digest"


@pytest.fixture
def digest(auth_page: Page) -> tuple[Page, dict]:
    with auth_page.expect_response(lambda r: r.url.endswith(API) and r.request.method == "GET") as resp:
        auth_page.goto("/admin/settings/digest")
    assert resp.value.ok, resp.value.status
    expect(auth_page.locator("main")).to_contain_text("Присылать мне", timeout=20_000)
    settle(auth_page)
    return auth_page, resp.value.json()


# Кейс: Состояние сводки, блоки и люди = API
@pytest.mark.smoke
def test_settings_blocks_people_match_api(digest):
    page, data = digest
    main = page.locator("main")
    expect(main).to_contain_text("Сводка компании включена" if data["settings"]["enabled"] else "Сводка компании выключена")
    for block in data["blocks"]:
        line = main.locator("label, div").filter(has_text=block["title"]).filter(has_text=block["module_label"]).last
        expect(line, block["key"]).to_be_visible()
    # «Люди» — участники организации под теми же именами, что в фильтре журнала действий
    for person in api_get(page, "/api/admin/audit-journal/filters").json()["people"]:
        expect(main.get_by_text(person["name"], exact=True).first, person["name"]).to_be_visible()


# Кейс: «Присылать мне» — пока сводка компании выключена, галочка неактивна; включена — сохраняется сразу
# (PUT /admin/digest/me), API отдаёт новое значение, возвращаем как было
@pytest.mark.writes_data
def test_me_toggle(digest):
    page, data = digest
    was = data["me"]["enabled"]
    toggle = page.get_by_label("Присылать мне")
    if not data["settings"]["enabled"]:
        expect(toggle).to_be_disabled()
        expect(page.locator("main")).to_contain_text("Сводка компании выключена")
        return
    try:
        expect(toggle).to_be_checked(checked=was)
        with page.expect_response(lambda r: r.url.endswith(f"{API}/me") and r.request.method == "PUT") as resp:
            toggle.click()
        assert resp.value.ok, resp.value.text()
        assert resp.value.request.post_data_json.get("enabled") is (not was), resp.value.request.post_data_json
        assert api_get(page, API).json()["me"]["enabled"] is (not was)
        expect(toggle).to_be_checked(checked=not was)
    finally:
        if api_get(page, API).json()["me"]["enabled"] is not was:
            api_put(page, f"{API}/me", {"enabled": was})


# Кейс: «Предпросмотр» — GET /preview отвечает и показывает сводку (ничего не отправляет)
def test_preview(digest):
    page, _ = digest
    with page.expect_response(lambda r: "/admin/digest/preview" in r.url) as resp:
        screen_button(page, "Предпросмотр", exact=True).click()
    assert resp.value.ok, resp.value.text()[:200]
    preview = page.locator("div.fixed.inset-0:visible, [role=dialog]").last
    expect(preview).to_be_visible()
    expect(preview).not_to_be_empty()
