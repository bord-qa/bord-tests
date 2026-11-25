"""Блок 6. Роли и права: app.bord.one/admin/roles.

Снято 22.10.2026:
- эталон — GET /api/admin/auth/role-permissions: roles{slug: {label,
  permissions, source, is_custom}}, all_permissions (14 прав платформы),
  permission_labels, grantable / never_grantable;
- «Матрица»: карточка роли «N из 14 прав», таблица право × роль, в ячейке
  svg lucide-check (есть) или lucide-minus (нет), ключ права — title подписи;
- «Редактор ролей» (?tab=editor): «Создать роль» → Название, Описание, цвет,
  галочки прав (never_grantable — disabled с пометкой «никогда») →
  POST /api/admin/auth/org-roles → 201; карточка «своя», кнопки «Изменить»
  и «Удалить роль» → окно «Удалить роль «<имя>»? Действие необратимо.»
  (Отмена / Да). Своя роль сразу доступна при приглашении на «Пользователях».

Пишем только роли «QA Autotest …» и удаляем их в любом исходе: список с id —
GET /api/admin/auth/org-roles.
"""
import re
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_delete, api_get, api_post
from helpers.wait import settle
from helpers.ui import screen_button

pytestmark = pytest.mark.requires_auth

PERMS = "/api/admin/auth/role-permissions"
ORG_ROLES = "/api/admin/auth/org-roles"
NAME_PREFIX = "QA Autotest"


def role_name() -> str:
    return f"{NAME_PREFIX} {int(time.time() * 1000)}"


def delete_qa_roles(page: Page):
    resp = api_get(page, ORG_ROLES)
    for role in resp.json()["roles"].values() if resp.ok else []:
        if role.get("id") and role["name"].startswith(NAME_PREFIX):
            api_delete(page, f"{ORG_ROLES}/{role['id']}")


def perms(page: Page) -> dict:
    resp = api_get(page, PERMS)
    assert resp.ok, f"{PERMS} → {resp.status}"
    return resp.json()


@pytest.fixture
def roles_page(auth_page: Page):
    auth_page.goto("/admin/roles")
    expect(auth_page.locator("main table")).to_be_visible()
    settle(auth_page)
    yield auth_page
    delete_qa_roles(auth_page)


@pytest.fixture
def editor(roles_page: Page):
    screen_button(roles_page, "Редактор ролей").click()
    # с 08.11 роли — вкладка «Команды»: /admin/team?tab=roles&sub=editor
    expect(roles_page).to_have_url(re.compile(r"(tab|sub)=editor"))
    expect(screen_button(roles_page, "Создать роль")).to_be_visible()
    return roles_page


def role_card(page: Page, name: str):
    """Карточка роли в редакторе: ближайший к названию блок с кнопками."""
    return page.locator("main").get_by_text(name, exact=True).locator("xpath=ancestor::div[.//button][1]")


def create_in_form(page: Page, name: str, labels: list[str]) -> dict:
    main = page.locator("main")
    screen_button(page, "Создать роль").click()
    main.get_by_role("textbox", name="Название").fill(name)
    main.get_by_role("textbox", name="Описание").fill("роль автотеста")
    for label in labels:
        main.get_by_role("checkbox", name=label).check()
    with page.expect_response(lambda r: r.url.endswith(ORG_ROLES) and r.request.method == "POST") as created:
        screen_button(page, "Сохранить").click()
    assert created.value.status == 201, created.value.text()
    return created.value.json()


# Кейс: Карточки ролей совпадают с API
@pytest.mark.smoke
def test_role_cards_match_api(roles_page: Page):
    data = perms(roles_page)
    total = len(data["all_permissions"])

    for role in data["roles"].values():
        card = roles_page.locator("main button").filter(has=roles_page.get_by_text(role["label"], exact=True))
        expect(card, f"нет карточки «{role['label']}»").to_have_count(1)
        expect(card).to_contain_text(f"{len(role['permissions'])} из {total} прав")


# Кейс: Таблица прав совпадает с API
def test_matrix_matches_api(roles_page: Page):
    data = perms(roles_page)
    # textContent, а не innerText: заголовки выводятся капсом через CSS
    header = [t.strip() for t in roles_page.locator("main table thead th").all_text_contents()]
    assert header[0] == "Право"
    by_label = {r["label"]: r for r in data["roles"].values()}
    assert sorted(header[1:]) == sorted(by_label), "столбцы матрицы ≠ роли в API"

    for perm in data["all_permissions"]:
        row = roles_page.locator("main table tbody tr").filter(has=roles_page.locator(f"[title='{perm}']"))
        expect(row, f"нет строки права {perm}").to_have_count(1)
        expect(row.locator("td").first).to_contain_text(data["permission_labels"][perm])
        for col, label in enumerate(header[1:], start=1):
            icon = "lucide-check" if perm in by_label[label]["permissions"] else "lucide-minus"
            expect(row.locator("td").nth(col).locator("svg"), f"{perm} × {label}").to_have_class(re.compile(icon))


# Кейс: Права, которые нельзя выдать, заблокированы
def test_never_grantable_permissions_locked(editor: Page):
    data = perms(editor)
    main = editor.locator("main")
    expect(main).to_contain_text(
        f"Выдать можно {len(data['grantable'])} из {len(data['all_permissions'])} прав платформы"
    )

    screen_button(editor, "Создать роль").click()
    for perm in data["all_permissions"]:
        box = main.get_by_role("checkbox", name=data["permission_labels"][perm])
        if perm in data["never_grantable"]:
            expect(box, perm).to_be_disabled()
            expect(box.locator("xpath=..")).to_contain_text("никогда")
        else:
            expect(box, perm).to_be_enabled()
    screen_button(editor, "Отменить").click()


# Кейс: Своя роль: создать, изменить, удалить
@pytest.mark.writes_data
def test_custom_role_create_edit_delete(editor: Page):
    main = editor.locator("main")
    name = role_name()

    role = create_in_form(editor, name, ["Отчёты — просмотр", "Уведомления — просмотр"])

    assert role["name"] == name and sorted(role["permissions"]) == ["reports.view", "view_notifications"]
    card = role_card(editor, name)
    expect(card).to_contain_text(role["slug"])
    expect(card).to_contain_text("своя роль организации")
    api_role = perms(editor)["roles"][role["slug"]]
    assert api_role["is_custom"] and sorted(api_role["permissions"]) == sorted(role["permissions"])

    card.get_by_role("button", name="Изменить").click()
    expect(main.get_by_role("textbox", name="Название")).to_have_value(name)
    main.get_by_role("checkbox", name="Комментарии — просмотр").check()
    with editor.expect_response(lambda r: f"{ORG_ROLES}/{role['id']}" in r.url and r.request.method in ("PUT", "PATCH")) as edited:
        screen_button(editor, "Сохранить").click()

    assert edited.value.ok, edited.value.text()
    assert "view_comments" in perms(editor)["roles"][role["slug"]]["permissions"]

    card.get_by_role("button", name="Удалить роль").click()
    dialog = editor.get_by_role("dialog", name=f"Удалить роль «{name}»? Действие необратимо.")
    with editor.expect_response(lambda r: f"{ORG_ROLES}/{role['id']}" in r.url and r.request.method == "DELETE") as deleted:
        dialog.get_by_role("button", name="Да").click()

    assert deleted.value.ok
    expect(main.get_by_text(name, exact=True)).to_have_count(0)
    assert role["slug"] not in perms(editor)["roles"]


# Кейс: Своя роль предлагается в приглашении
@pytest.mark.writes_data
def test_custom_role_offered_in_invite(roles_page: Page):
    """Своя роль сразу выбирается при приглашении сотрудника."""
    name = role_name()
    resp = api_post(roles_page, ORG_ROLES, {"name": name, "description": "", "permissions": ["view_notifications"], "color": "#6366f1"})
    assert resp.status == 201, resp.text()

    roles_page.goto("/admin/users")
    expect(roles_page.get_by_role("heading", name="Сотрудники организации")).to_be_visible()

    expect(roles_page.locator("main select").first.locator("option", has_text=name)).to_have_count(1)


# Кейс: Удаление роли можно отменить
@pytest.mark.writes_data
def test_delete_role_can_be_cancelled(editor: Page):
    name = role_name()
    create_in_form(editor, name, ["Уведомления — просмотр"])

    role_card(editor, name).get_by_role("button", name="Удалить роль").click()
    editor.get_by_role("dialog", name=re.compile("Удалить роль")).get_by_role("button", name="Отмена").click()

    expect(editor.locator("main").get_by_text(name, exact=True)).to_be_visible()
    assert any(r["name"] == name for r in api_get(editor, ORG_ROLES).json()["roles"].values())
