"""Блок 19. Второй сотрудник BORD QA: роль, удаление, задачи коллеге.

Вторая тест-учётка — qaColleague (lemido4693@moblha.com, роль «Сотрудник»,
модули по умолчанию). Снято 27.10.2026:
- «Пользователи»: у каждой строки «Показать права», «Редактировать»,
  «Сбросить пароль и отправить email», «Удалить из организации» (у себя —
  без удаления). «Редактировать» раскрывает в строке Имя, Фамилию, Email и
  <select> «Роль» (Владелец / Администратор / Редактор / Сотрудник / Гость) →
  «Сохранить» → PUT /api/admin/auth/users/<id> {role}; «Администратор» =
  head_of_support, «Сотрудник» = support_agent, «Гость» = employee;
- «Удалить из организации» → окно «Удалить сотрудника из организации?» →
  DELETE /api/admin/auth/users/<id> → 204. Если других организаций у
  человека нет, удаляется и сам аккаунт — поэтому удаляем только
  одноразового участника, а не qaColleague;
- приглашение с email и send_email=true СРАЗУ заводит аккаунт и членство
  (так задумано: «provisioned at invite-creation time»); приглашение после
  удаления участника остаётся активным — уборка его отзывает;
- у «Сотрудника» нет ни одного права на админку (users/roles/settings).

«Сбросить пароль» у qaColleague НЕ нажимать — сломается вход тестов.
Одноразовые участники — qa-autotest-member-<время>@example.com (example.com
писем не принимает).
"""
import re
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_delete, api_get, api_post, api_put
from helpers.modal import modal
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

USERS = "/api/admin/auth/users"
INVITES = "/api/admin/auth/invites"
TASKS = "/api/tasks/"
NAME_PREFIX = "QA Autotest"
THROWAWAY = "qa-autotest-member-"
EMPLOYEE, ADMIN = "support_agent", "head_of_support"


def members(page: Page) -> list:
    return api_get(page, USERS).json()


def member(page: Page, email: str) -> dict | None:
    return next((u for u in members(page) if u["email"] == email), None)


def cleanup(page: Page, colleague_email: str):
    colleague = member(page, colleague_email)
    if colleague and colleague["role"] != EMPLOYEE:
        api_put(page, f"{USERS}/{colleague['id']}/role", {"role": EMPLOYEE})
    for user in members(page):
        if user["email"].startswith(THROWAWAY):
            api_delete(page, f"{USERS}/{user['id']}")
    for invite in api_get(page, INVITES).json():
        if (invite.get("note") or "").startswith(NAME_PREFIX) or (invite.get("email") or "").startswith(THROWAWAY):
            api_delete(page, f"{INVITES}/{invite['code']}")
    tasks = api_get(page, f"{TASKS}?status=all&limit=100")
    for task in tasks.json()["items"] if tasks.ok else []:
        if task["title"].startswith(NAME_PREFIX):
            api_delete(page, f"{TASKS}{task['id']}")


@pytest.fixture
def owner(auth_page: Page, colleague_user: dict):
    auth_page.goto("/admin/users")
    expect(row(auth_page, colleague_user["email"])).to_be_visible()
    settle(auth_page)
    yield auth_page
    cleanup(auth_page, colleague_user["email"])


def member_rows(page: Page):
    # только список «Сотрудники организации»: карточки приглашений выше — тоже li с email
    heading = page.get_by_role("heading", name="Сотрудники организации")
    return heading.locator("xpath=ancestor::div[.//li][1]").locator("li")


def row(page: Page, email: str):
    return member_rows(page).filter(has_text=email)


def set_role(page: Page, email: str, label: str):
    rows = member_rows(page)
    expect(row(page, email)).to_have_count(1)
    # в режиме правки email уходит в поле ввода и has_text строку теряет — держим её по номеру
    index = next(i for i, text in enumerate(rows.all_inner_texts()) if email in text)
    line = rows.nth(index)
    line.get_by_role("button", name="Редактировать").click()
    line.locator("select").select_option(label=label)
    with page.expect_response(lambda r: re.search(rf"{USERS}/\d+$", r.url) and r.request.method == "PUT") as saved:
        line.get_by_role("button", name="Сохранить").click()
    assert saved.value.ok, saved.value.text()
    return saved.value.request.post_data_json


def provision_throwaway(page: Page) -> str:
    """Одноразовый участник: приглашение с письмом сразу заводит аккаунт."""
    email = f"{THROWAWAY}{int(time.time() * 1000)}@example.com"
    resp = api_post(page, INVITES, {"email": email, "role": EMPLOYEE, "note": f"{NAME_PREFIX} удаление", "send_email": True})
    assert resp.ok, resp.text()
    assert member(page, email), "приглашение с письмом больше не заводит участника сразу"
    return email


# Кейс: Коллега в списке сотрудников с ролью «Сотрудник»
def test_colleague_listed_as_employee(owner: Page, colleague_user: dict):
    data = member(owner, colleague_user["email"])
    assert data and data["role"] == EMPLOYEE
    line = row(owner, colleague_user["email"])
    expect(line).to_contain_text("Сотрудник")
    expect(line).not_to_contain_text("(вы)")
    expect(line.get_by_role("button", name="Удалить из организации")).to_be_visible()


# Кейс: Смена роли сотрудника: «Администратор» и обратно
@pytest.mark.writes_data
def test_change_colleague_role(owner: Page, colleague_user: dict):
    email = colleague_user["email"]
    colleague_id = member(owner, email)["id"]

    assert set_role(owner, email, "Администратор")["role"] == ADMIN
    expect(row(owner, email)).to_contain_text("Администратор")
    assert member(owner, email)["role"] == ADMIN
    rights = api_get(owner, f"{USERS}/{colleague_id}/permissions").json()
    assert rights["role_label"] == "Администратор"

    assert set_role(owner, email, "Сотрудник")["role"] == EMPLOYEE
    expect(row(owner, email)).to_contain_text("Сотрудник")
    assert member(owner, email)["role"] == EMPLOYEE


# Кейс: Удаление сотрудника из организации
@pytest.mark.writes_data
def test_remove_member(owner: Page):
    email = provision_throwaway(owner)
    owner.reload()
    line = row(owner, email)
    expect(line).to_contain_text("Сотрудник")
    settle(owner)

    line.get_by_role("button", name="Удалить из организации").click()
    dialog = owner.get_by_role("dialog", name="Удалить сотрудника из организации?")
    with owner.expect_response(lambda r: re.search(rf"{USERS}/\d+$", r.url) and r.request.method == "DELETE") as deleted:
        dialog.get_by_role("button", name="Да").click()

    assert deleted.value.status == 204
    expect(row(owner, email)).to_have_count(0)
    assert member(owner, email) is None
    # других организаций у одноразового нет — аккаунт удалён с платформы
    assert api_get(owner, f"{USERS}/search?email={email}").json() == []


# Кейс: Удаление сотрудника можно отменить
def test_remove_member_can_be_cancelled(owner: Page, colleague_user: dict):
    deletes = []
    owner.on("request", lambda r: deletes.append(r.url) if r.method == "DELETE" and USERS in r.url else None)
    row(owner, colleague_user["email"]).get_by_role("button", name="Удалить из организации").click()
    dialog = owner.get_by_role("dialog", name="Удалить сотрудника из организации?")
    dialog.get_by_role("button", name="Отмена").click()

    expect(dialog).to_have_count(0)
    expect(row(owner, colleague_user["email"])).to_be_visible()
    assert deletes == []
    assert member(owner, colleague_user["email"])


# Кейс: У «Сотрудника» нет доступа к админке
def test_colleague_has_no_admin_access(colleague_page: Page, colleague_user: dict):
    colleague_page.goto("/admin")
    settle(colleague_page)
    for path in (USERS, "/api/admin/auth/role-permissions", INVITES):
        status = api_get(colleague_page, path).status
        assert status in (401, 403), f"{path} → {status}: «Сотруднику» отдали админские данные"


# Кейс: «Поручить коллеге»: задача видна коллеге, он берёт её в работу
@pytest.mark.writes_data
def test_task_assigned_to_colleague(owner: Page, colleague_page: Page, colleague_user: dict):
    colleague = member(owner, colleague_user["email"])
    name = f"{NAME_PREFIX} задача коллеге {int(time.time() * 1000)}"
    owner.goto("/admin/tasks")
    main = owner.locator("main")
    expect(main.get_by_role("button", name="Новая задача")).to_be_visible()
    settle(owner)
    main.get_by_role("button", name="Новая задача").click()
    form = modal(owner, "Новая задача")
    form.get_by_text("Что нужно сделать", exact=True).locator("xpath=following-sibling::*[self::input or self::textarea][1]").fill(name)
    form.get_by_role("button", name="Не назначено", exact=True).click()
    owner.get_by_role("option", name=colleague["telegram_first_name"], exact=True).click()
    with owner.expect_response(lambda r: r.url.endswith(TASKS) and r.request.method == "POST") as created:
        form.get_by_role("button", name="Поставить").click()

    assert created.value.status == 201, created.value.text()
    task = created.value.json()
    assert created.value.request.post_data_json["assignee_id"] == colleague["id"]

    colleague_page.goto("/admin/tasks")
    line = colleague_page.locator("main li").filter(has_text=name)
    expect(line).to_be_visible()
    settle(colleague_page)
    assert any(t["id"] == task["id"] for t in api_get(colleague_page, f"{TASKS}?status=active&limit=100").json()["items"])

    line.get_by_role("button", name="Новая", exact=True).click()
    with colleague_page.expect_response(lambda r: r.url.endswith(f"{TASKS}{task['id']}") and r.request.method == "PATCH") as moved:
        colleague_page.get_by_role("option", name="В работе", exact=True).click()
    assert moved.value.ok, moved.value.text()
    assert api_get(owner, f"{TASKS}{task['id']}").json()["status"] == "in_progress"
