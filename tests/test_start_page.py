"""Блок 2. Стартовая страница app.bord.one/admin — карта функций компании.

Эталон — API: /api/org-map/ (функции-«слоты» и раскладка систем `placement`)
и /api/modules/ (какие системы подключены). Экран сверяется с ними, а не
с захардкоженным списком: раскладка зависит от шаблона и перестановок
Владельца. Секция функции на странице — section[data-slot=<ключ слота>].

Тесты только читают. Перенос системы и смену шаблона не проверяем, пока не
починят ecom-100148: один перенос «Домены» → «Финансы» уронил /api/org-map/
в 500 для всей организации без возможности отката.
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_get
from helpers.modules import TILE_HREF
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

ORG_MAP_BUG = "ecom-100148: /api/org-map/ отвечает 500 в BORD QA после переноса системы"


def tile_names(section):
    return section.locator("a p.font-semibold").all_inner_texts()


@pytest.fixture
def start(auth_page: Page):
    auth_page.goto("/admin")
    expect(auth_page).to_have_title(re.compile("Стартовая страница"))
    settle(auth_page)
    return auth_page


@pytest.fixture
def org_map(start: Page) -> dict:
    resp = api_get(start, "/api/org-map/")
    if resp.status == 500:
        pytest.xfail(ORG_MAP_BUG)
    assert resp.ok, f"/api/org-map/ → {resp.status}"
    return resp.json()


@pytest.fixture
def modules(start: Page) -> dict:
    resp = api_get(start, "/api/modules/")
    assert resp.ok, f"/api/modules/ → {resp.status}"
    return {m["module_key"]: m for m in resp.json() if m["is_enabled"]}


# Кейс: API карты организации доступно
@pytest.mark.smoke
def test_org_map_api_available(start: Page):
    """Сам источник карты отвечает. Красный = карта у организации сломана."""
    resp = api_get(start, "/api/org-map/")

    assert resp.status == 200, f"/api/org-map/ → {resp.status} ({ORG_MAP_BUG})"
    expect(start.get_by_text("Не удалось загрузить раскладку по функциям")).to_have_count(0)


# Кейс: Ячейки карты совпадают с API
def test_map_slots_match_api(start: Page, org_map):
    for slot in org_map["slots"]:
        section = start.locator(f"section[data-slot='{slot['key']}']")
        expect(section, f"нет секции функции «{slot['name']}»").to_be_visible()
        expect(section).to_contain_text(slot["name"], ignore_case=True)


# Кейс: Системы стоят на карте как в API
def test_systems_placed_as_in_api(start: Page, org_map, modules):
    for slot in org_map["slots"]:
        expected = sorted(
            modules[key]["name"]
            for key, where in org_map["placement"].items()
            if where == slot["key"] and key in modules
        )
        section = start.locator(f"section[data-slot='{slot['key']}']")
        assert sorted(tile_names(section)) == expected, f"функция «{slot['name']}»"
        if not expected:
            expect(section.get_by_text("Нет систем")).to_be_visible()


# Кейс: У каждого подключённого модуля есть плитка со ссылкой
def test_every_enabled_module_has_tile_with_link(start: Page, org_map, modules):
    for key, module in modules.items():
        if org_map["placement"].get(key) == org_map["unused"]:
            continue
        tile = start.locator("main a").filter(has=start.locator(f"p.font-semibold[title='{module['name']}']"))
        expect(tile, f"нет плитки «{module['name']}»").to_have_count(1)
        href = TILE_HREF.get(key)
        assert href, f"новая система {key!r}: допиши её адрес в TILE_HREF"
        expect(tile).to_have_attribute("href", href)
        if href.startswith("http"):
            expect(tile).to_have_attribute("target", "_blank")


# Кейс: Внутренняя плитка открывает раздел
def test_internal_tile_opens_section(start: Page, org_map):
    start.locator("section[data-slot] a[href='/admin/tasks']").click()

    expect(start).to_have_url(re.compile(r"/admin/tasks$"))
    expect(start).to_have_title(re.compile("Задачи"))


# Кейс: «Настроить» предлагает все шаблоны
def test_configure_offers_all_presets(start: Page, org_map):
    """«Настроить» показывает все шаблоны вертикали. Шаблон не выбираем —
    раскладка общая на организацию и сохраняется сразу."""
    expected = [p["name"] for p in org_map["presets"]]
    current = next(p["name"] for p in org_map["presets"] if p["key"] == org_map["preset"])

    start.get_by_role("button", name="Настроить").click()
    preset = start.locator("main button[aria-haspopup='listbox']").first
    expect(preset).to_have_text(current)
    preset.click()

    expect(start.get_by_role("option")).to_have_text(expected)
    start.keyboard.press("Escape")
    start.get_by_role("button", name="Готово").click()
    expect(start.get_by_role("button", name="Настроить")).to_be_visible()
