"""Блок 6. Доступы: app.bord.one/admin/access — роли модулей.

Снято 22.10.2026:
- эталон — GET /api/admin/auth/module-roles: modules[{key, name, roles[{id,
  slug, name, level, is_template, permissions, members}], catalog}], levels
  (employee/manager/admin → Сотрудник/Руководитель/Администратор);
- «Роли модулей»: слева модули с числом ролей, справа карточки ролей
  выбранного модуля (имя, «Назначена людям» N, уровень, «N прав», у своей
  роли пометка «своя»); «Новая роль» → название, уровень, описание, галочки
  прав (label[title=<ключ права>]) → «Создать» → POST …/module-roles → 201;
  у своей роли «Удалить» → окно «Удалить роль «<имя>»?» (Отмена / Да) →
  DELETE …/module-roles/<id> → 204;
- «Сотрудники» (?tab=people): GET …/module-roles/matrix — человек × модуль,
  у владельца в каждой ячейке «Владелец — всё».

С 08.11.2026 «Пользователи», «Доступы» и «Роли» — вкладки «Команды»
(/admin/team: «Сотрудники» / «Доступ к модулям» ?tab=access / «Роли»
?tab=roles&sub=org|editor); старые адреса редиректят туда.

Пишем только роли «QA Autotest …» в модуле «Задачи» и удаляем их в любом исходе.
"""
import re
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_delete, api_get
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

MODULE_ROLES = "/api/admin/auth/module-roles"
NAME_PREFIX = "QA Autotest"
MODULE = "tasks"


def module_roles(page: Page) -> dict:
    resp = api_get(page, MODULE_ROLES)
    assert resp.ok, f"{MODULE_ROLES} → {resp.status}"
    return resp.json()


def module(page: Page, key: str) -> dict:
    return next(m for m in module_roles(page)["modules"] if m["key"] == key)


def delete_qa_roles(page: Page):
    resp = api_get(page, MODULE_ROLES)
    for mod in resp.json()["modules"] if resp.ok else []:
        for role in mod["roles"]:
            if role["name"].startswith(NAME_PREFIX):
                api_delete(page, f"{MODULE_ROLES}/{role['id']}")


@pytest.fixture
def access(auth_page: Page):
    auth_page.goto("/admin/access")
    expect(auth_page.locator("main nav")).to_be_visible()
    settle(auth_page)
    yield auth_page
    delete_qa_roles(auth_page)


def module_tab(page: Page, name: str):
    return page.locator("main nav").get_by_role("button", name=re.compile(rf"^{re.escape(name)} \d+$"))


def role_cards(page: Page):
    return page.locator("main button").filter(has=page.get_by_title("Назначена людям"))


def role_card(page: Page, name: str):
    # Название — сосед счётчика «Назначена людям»: плашка уровня ниже бывает
    # с тем же текстом («Сотрудник»), её не берём.
    title = page.locator(f"xpath=.//*[@title='Назначена людям']/preceding-sibling::*[normalize-space()='{name}']")
    return role_cards(page).filter(has=title)


# Кейс: Вкладки систем совпадают с подключёнными модулями
@pytest.mark.smoke
def test_module_tabs_match_api(access: Page):
    mods = module_roles(access)["modules"]

    expect(access.locator("main nav").get_by_role("button")).to_have_count(len(mods))
    for mod in mods:
        expect(module_tab(access, mod["name"]), mod["key"]).to_have_text(f"{mod['name']}{len(mod['roles'])}")


# Кейс: Карточки ролей системы совпадают с API
def test_role_cards_match_api(access: Page):
    data = module_roles(access)
    levels = {lvl["key"]: lvl["label"] for lvl in data["levels"]}

    for mod in data["modules"]:
        module_tab(access, mod["name"]).click()
        expect(access.locator("main").get_by_text(mod["name"], exact=True).last).to_be_visible()
        expect(role_cards(access), mod["key"]).to_have_count(len(mod["roles"]))
        for role in mod["roles"]:
            card = role_card(access, role["name"])
            expect(card, f"{mod['key']}/{role['slug']}").to_have_count(1)
            expect(card.get_by_title("Назначена людям")).to_have_text(str(role["members"]))
            expect(card).to_contain_text(levels[role["level"]])
            expect(card).to_contain_text(re.compile(rf"\b{len(role['permissions'])} прав"))


# Кейс: Таблица «люди × роли» совпадает с API
def test_people_matrix_matches_api(access: Page):
    resp = api_get(access, f"{MODULE_ROLES}/matrix")
    assert resp.ok
    matrix = resp.json()

    # с 08.11 «Доступы» — вкладки «Команды»: матрица живёт на «Доступ к модулям»
    access.locator("main").get_by_role("button", name="Доступ к модулям", exact=True).click()
    expect(access).to_have_url(re.compile(r"tab=access"))

    rows = access.locator("main table tbody tr")
    expect(rows).to_have_count(len(matrix["members"]))
    header = [t.strip() for t in access.locator("main table thead th").all_text_contents()]
    assert header == ["Сотрудник"] + [m["name"] for m in matrix["modules"]]
    for member in matrix["members"]:
        row = rows.filter(has_text=member["email"])
        expect(row, member["email"]).to_have_count(1)
        if member["is_owner"]:
            expect(row.locator("td").filter(has_text="Владелец — всё")).to_have_count(len(matrix["modules"]))


# Кейс: Своя роль системы: создать, изменить, удалить
@pytest.mark.writes_data
def test_module_role_create_edit_delete(access: Page):
    main = access.locator("main")
    mod = module(access, MODULE)
    name = f"{NAME_PREFIX} {int(time.time() * 1000)}"
    tab = module_tab(access, mod["name"])
    tab.click()

    main.get_by_role("button", name="Новая роль").click()
    main.get_by_placeholder("Название роли, например «Финансовый директор»").fill(name)
    main.get_by_placeholder("Что делает человек с этой ролью").fill("роль автотеста")
    main.locator("label[title='tasks.view'] input").check()
    with access.expect_response(lambda r: r.url.endswith(MODULE_ROLES) and r.request.method == "POST") as created:
        main.get_by_role("button", name="Создать").click()

    assert created.value.status == 201, created.value.text()
    sent = created.value.request.post_data_json
    assert sent["name"] == name and sent["permissions"] == ["tasks.view"]
    role = next(r for r in module(access, MODULE)["roles"] if r["name"] == name)
    expect(tab).to_have_text(f"{mod['name']}{len(mod['roles']) + 1}")
    card = role_card(access, name)
    expect(card).to_contain_text("своя")
    expect(card.get_by_title("Назначена людям")).to_have_text("0")

    main.locator("label[title='tasks.manage'] input").check()
    with access.expect_response(lambda r: f"{MODULE_ROLES}/{role['id']}" in r.url and r.request.method in ("PUT", "PATCH")) as edited:
        main.get_by_role("button", name="Сохранить").click()

    assert edited.value.ok, edited.value.text()
    saved = next(r for r in module(access, MODULE)["roles"] if r["id"] == role["id"])
    assert sorted(saved["permissions"]) == ["tasks.manage", "tasks.view"]

    main.get_by_role("button", name="Удалить", exact=True).click()
    dialog = access.get_by_role("dialog", name=f"Удалить роль «{name}»?")
    with access.expect_response(lambda r: f"{MODULE_ROLES}/{role['id']}" in r.url and r.request.method == "DELETE") as deleted:
        dialog.get_by_role("button", name="Да").click()

    assert deleted.value.status == 204
    expect(card).to_have_count(0)
    expect(tab).to_have_text(f"{mod['name']}{len(mod['roles'])}")
    assert all(r["id"] != role["id"] for r in module(access, MODULE)["roles"])


# Кейс: Шаблонную роль удалить нельзя
def test_template_role_cannot_be_deleted(access: Page):
    """У шаблонных ролей модуля кнопки «Удалить» нет — только «Вернуть исходную»."""
    mod = module(access, MODULE)
    template = next(r for r in mod["roles"] if r["is_template"])
    module_tab(access, mod["name"]).click()

    role_card(access, template["name"]).click()

    main = access.locator("main")
    expect(main.get_by_placeholder("Название роли, например «Финансовый директор»")).to_have_value(template["name"])
    expect(main.get_by_role("button", name="Вернуть исходную")).to_be_visible()
    expect(main.get_by_role("button", name="Удалить", exact=True)).to_have_count(0)
