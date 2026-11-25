"""Блок 77. HRM — «Настройки структуры» оргструктуры и «Управление категориями» ТМЦ (появились после 03.11.2026).

Снято 25.11.2026 в видимом MCP-браузере:
- «Оргструктура» → «Настройки» в шапке → окно «Настройки структуры», вкладки «Типы подразделений /
  Категории должностей / Бизнес-блоки / Компании / Финансы» (первые две покрыты в блоке 51 через «Настройки»);
- «Бизнес-блоки» = GET /org/swimlanes: строка «<название> · <тип> · N отд.»; «Добавить блок»: «Название»,
  цвет, тип (кнопка «Основной» = lane_type core), «Добавить» → POST /org/swimlanes (201); то же название
  ещё раз → 409 «Бизнес-блок «…» уже есть» — плашка в окне; у строки две иконки без подписи (правка,
  удаление) — вторая → «Удалить бизнес-блок?» «На блоке нет подразделений.» → «Удалить» → DELETE;
- «Компании» = GET /org/settings: «Формат холдинга», «Сотрудники общие для всех юрлиц», «Показывать
  администратора и главного бухгалтера», «Показывать владельцев и доли» — каждое сразу PUT /org/settings
  {<ключ>: bool}; ниже «Кто кому принадлежит» по юрлицам;
- «Финансы»: связь с facturi; без подключения (finance_connected=false) — «Финансы не подключены» и
  «Подключить», права hrm.view_finance / hrm.manage_finance. «Подключить» не жмём — связывает организацию
  с Facturi;
- «ТМЦ» → «Управление категориями» = GET /tmc/categories: «Новая категория» + «Добавить» → POST;
  у строки «Архивировать» (PUT is_archived, метка «В архиве», кнопка становится «Восстановить из архива»),
  «Переименовать» (поле на месте, Enter → PUT name), «Удалить» → «Удалить категорию» «Удалить категорию
  «…»? Позиции в ней станут без категории.» → «Удалить» → DELETE.
Свои записи — с префиксом QA_PREFIX, удаляются в любом исходе; настройки — возвращаем.
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import QA_PREFIX, header_button, hrm_delete, hrm_get, hrm_json, hrm_put, items, open_hrm, stamp

pytestmark = pytest.mark.requires_auth


def window(page: Page, text: str):
    return page.locator("div.fixed.inset-0:visible").filter(has=page.get_by_text(text, exact=True)).last


def structure(page: Page, tab: str):
    open_hrm(page, "/admin/orgchart")
    header_button(page, "Настройки").click()
    box = window(page, "Настройки структуры")
    box.get_by_role("button", name=tab, exact=True).click()
    return box


@pytest.fixture
def own_lanes(auth_page: Page):
    yield
    for lane in items(hrm_json(hrm_get(auth_page, "/org/swimlanes"))):
        if lane["name"].startswith(QA_PREFIX):
            hrm_delete(auth_page, f"/org/swimlanes/{lane['id']}")


@pytest.fixture
def own_categories(auth_page: Page):
    yield
    for cat in items(hrm_json(hrm_get(auth_page, "/tmc/categories"))):
        if cat["name"].startswith(QA_PREFIX):
            hrm_delete(auth_page, f"/tmc/categories/{cat['id']}")


# ───────────────────────── бизнес-блоки ─────────────────────────

# Кейс: «Бизнес-блоки» = API; добавить блок → строка «Основной · 0 отд.»; то же название — 409 и плашка;
# удалить через «Удалить бизнес-блок?»
@pytest.mark.writes_data
def test_business_block_lifecycle(auth_page: Page, own_lanes):
    box = structure(auth_page, "Бизнес-блоки")
    for lane in items(hrm_json(hrm_get(auth_page, "/org/swimlanes"))):
        expect(box).to_contain_text(lane["name"])
    name = f"{QA_PREFIX} Блок {stamp()}"
    box.get_by_placeholder("Название").fill(name)
    with auth_page.expect_response(lambda r: r.url.endswith("/org/swimlanes") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Добавить", exact=True).click()
    assert resp.value.status == 201, resp.value.text()[:200]
    lane_id = resp.value.json()["id"]
    assert resp.value.request.post_data_json["lane_type"] == "core"
    row = box.locator("div").filter(has=auth_page.get_by_text(name, exact=True)).filter(
        has=auth_page.locator("button")).last
    expect(row).to_contain_text(re.compile(r"Основной · 0 отд\."))
    api = next(x for x in items(hrm_json(hrm_get(auth_page, "/org/swimlanes"))) if x["id"] == lane_id)
    assert api["name"] == name, api

    box.get_by_placeholder("Название").fill(name)
    with auth_page.expect_response(lambda r: r.url.endswith("/org/swimlanes") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Добавить", exact=True).click()
    assert resp.value.status == 409, f"второй блок с тем же названием: {resp.value.status}"
    expect(box.get_by_role("alert")).to_contain_text(f"Бизнес-блок «{name}» уже есть")
    assert sum(x["name"] == name for x in items(hrm_json(hrm_get(auth_page, "/org/swimlanes")))) == 1

    row.locator("button").nth(1).click()  # иконки без подписи: правка, удаление
    confirm = window(auth_page, "Удалить бизнес-блок?")
    expect(confirm).to_contain_text(f"«{name}»")
    expect(confirm).to_contain_text("На блоке нет подразделений.")
    with auth_page.expect_response(lambda r: f"/org/swimlanes/{lane_id}" in r.url and r.request.method == "DELETE") as resp:
        confirm.get_by_role("button", name="Удалить", exact=True).click()
    assert resp.value.ok, resp.value.text()[:200]
    expect(box.get_by_text(name, exact=True)).to_have_count(0)
    assert lane_id not in {x["id"] for x in items(hrm_json(hrm_get(auth_page, "/org/swimlanes")))}


# ───────────────────────── компании и финансы ─────────────────────────

COMPANY = {"Формат холдинга": "holding_mode", "Сотрудники общие для всех юрлиц": "shared_staff",
           "Показывать администратора и главного бухгалтера": "show_entity_officials",
           "Показывать владельцев и доли (с диаграммой)": "show_entity_owners"}


# Кейс: «Компании» — галочки = /org/settings; «Сотрудники общие для всех юрлиц» сохраняется сразу → вернуть;
# «Финансы» без подключения — «Финансы не подключены» и «Подключить»
@pytest.mark.writes_data
def test_company_and_finance_settings(auth_page: Page):
    before = hrm_json(hrm_get(auth_page, "/org/settings"))
    box = structure(auth_page, "Компании")
    for label, key in COMPANY.items():
        expect(box.get_by_label(label), label).to_be_checked(checked=before[key])
    expect(box).to_contain_text("Кто кому принадлежит")

    cb = box.get_by_label("Сотрудники общие для всех юрлиц")
    try:
        with auth_page.expect_response(lambda r: "/org/settings" in r.url and r.request.method == "PUT") as resp:
            cb.click()
        assert resp.value.ok and resp.value.request.post_data_json == {"shared_staff": not before["shared_staff"]}
        expect(cb).to_be_checked(checked=not before["shared_staff"])
        assert hrm_json(hrm_get(auth_page, "/org/settings"))["shared_staff"] is (not before["shared_staff"])
    finally:
        hrm_put(auth_page, "/org/settings", {"shared_staff": before["shared_staff"]})

    box.get_by_role("button", name="Финансы", exact=True).click()
    expect(box).to_contain_text("Связь с финансами facturi")
    expect(box).to_contain_text("hrm.view_finance")
    if not before["finance_connected"]:
        expect(box.get_by_text("Финансы не подключены")).to_be_visible()
        expect(box.get_by_role("button", name="Подключить")).to_be_enabled()


# ───────────────────────── категории ТМЦ ─────────────────────────

# Кейс: «Управление категориями» = API; добавить → переименовать → в архив (метка, «Восстановить из архива»)
# → удалить через «Удалить категорию»
@pytest.mark.writes_data
def test_tmc_category_lifecycle(auth_page: Page, own_categories):
    open_hrm(auth_page, "/admin/tmc")
    header_button(auth_page, "Управление категориями").click()
    box = window(auth_page, "Управление категориями")
    for cat in items(hrm_json(hrm_get(auth_page, "/tmc/categories"))):
        expect(box).to_contain_text(cat["name"])
    expect(box).to_contain_text("При удалении категории её позиции станут «Без категории».")

    name, renamed = f"{QA_PREFIX} Кат {stamp()}", f"{QA_PREFIX} Кат2 {stamp()}"
    box.get_by_placeholder("Название категории").fill(name)
    with auth_page.expect_response(lambda r: r.url.endswith("/tmc/categories") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Добавить").click()
    assert resp.value.ok, resp.value.text()[:200]
    cat_id = resp.value.json()["id"]

    def row(text):
        return box.locator("div").filter(has=auth_page.get_by_text(text, exact=True)).filter(
            has=auth_page.get_by_title("Удалить")).last

    row(name).get_by_title("Переименовать").click()
    edit = box.locator("input:visible").last
    expect(edit).to_have_value(name)
    edit.fill(renamed)
    with auth_page.expect_response(lambda r: f"/tmc/categories/{cat_id}" in r.url and r.request.method == "PUT") as resp:
        edit.press("Enter")
    assert resp.value.request.post_data_json == {"name": renamed}
    expect(row(renamed)).to_be_visible()

    with auth_page.expect_response(lambda r: f"/tmc/categories/{cat_id}" in r.url and r.request.method == "PUT") as resp:
        row(renamed).get_by_title("Архивировать").click()
    assert resp.value.request.post_data_json == {"is_archived": True}
    expect(row(renamed)).to_contain_text("В архиве")
    expect(row(renamed).get_by_title("Восстановить из архива")).to_be_visible()
    api = next(c for c in items(hrm_json(hrm_get(auth_page, "/tmc/categories"))) if c["id"] == cat_id)
    assert api["name"] == renamed and api["is_archived"], api

    row(renamed).get_by_title("Удалить").click()
    confirm = window(auth_page, "Удалить категорию")
    expect(confirm).to_contain_text(f"Удалить категорию «{renamed}»? Позиции в ней станут без категории.")
    with auth_page.expect_response(lambda r: f"/tmc/categories/{cat_id}" in r.url and r.request.method == "DELETE") as resp:
        confirm.get_by_role("button", name="Удалить", exact=True).click()
    assert resp.value.ok, resp.value.text()[:200]
    expect(box.get_by_text(renamed, exact=True)).to_have_count(0)
    assert cat_id not in {c["id"] for c in items(hrm_json(hrm_get(auth_page, "/tmc/categories")))}
