"""Блок 5. Пользователи: app.bord.one/admin/users.

Снято 21.10.2026:
- список: /api/admin/auth/users, подписи ролей — /api/admin/auth/roles
  (superuser → «Владелец», support_agent → «Сотрудник», employee → «Гость»…);
- «Пригласить по ссылке» → POST /api/admin/auth/invites → «Приглашение создано!»,
  ссылка app.bord.one/invite/<code>, «Действует 24 часа · Одноразовая ссылка»,
  карточка в «Активные приглашения (N)»; «Отозвать» → окно «Отозвать приглашение?»
  → «Да» → DELETE /api/admin/auth/invites/<code>;
- гость по ссылке: страница берёт GET /api/admin/auth/invite/<code>/info,
  несуществующий/отозванный код → 404 и «Приглашение истекло».

В организации есть живой аккаунт человека — его строку тесты НЕ трогают
(никаких «Редактировать», «Сбросить пароль», «Удалить»). Пишем только свои
приглашения с заметкой «QA Autotest …» и отзываем их в любом исходе.
"""
import re
import time

import pytest
from playwright.sync_api import Browser, Page, expect

from helpers.api import api_delete, api_get, api_post
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

INVITES = "/api/admin/auth/invites"
NOTE_PREFIX = "QA Autotest"
# Было 21.10: /info отвечал 500 и гость видел «Приглашение истекло».
INVITE_BUG = "регрессия ecom-100148: ссылка-приглашение не открывается"


def note() -> str:
    return f"{NOTE_PREFIX} {int(time.time() * 1000)}"


def revoke_qa_invites(page: Page):
    resp = api_get(page, INVITES)
    for inv in resp.json() if resp.ok else []:
        if (inv.get("note") or "").startswith(NOTE_PREFIX):
            api_delete(page, f"{INVITES}/{inv['code']}")


@pytest.fixture
def users_page(auth_page: Page):
    auth_page.goto("/admin/users")
    expect(auth_page.get_by_role("heading", name="Сотрудники организации")).to_be_visible()
    settle(auth_page)
    yield auth_page
    revoke_qa_invites(auth_page)


@pytest.fixture
def api_users(users_page: Page) -> list:
    resp = api_get(users_page, "/api/admin/auth/users")
    assert resp.ok, f"/api/admin/auth/users → {resp.status}"
    return resp.json()


@pytest.fixture
def role_labels(users_page: Page) -> dict:
    resp = api_get(users_page, "/api/admin/auth/roles")
    assert resp.ok
    return resp.json()["labels"]


def staff_rows(page: Page):
    return page.locator("main li").filter(has=page.get_by_role("button", name="Показать права этого сотрудника"))


def display_name(user: dict) -> str:
    return " ".join(x for x in (user.get("telegram_first_name"), user.get("last_name")) if x)


@pytest.mark.smoke
def test_staff_count_matches_api(users_page: Page, api_users):
    expect(users_page.get_by_text(f"{len(api_users)} человек в организации")).to_be_visible()
    expect(staff_rows(users_page)).to_have_count(len(api_users))


def test_staff_rows_match_api(users_page: Page, api_users, role_labels, qa_user):
    for user in api_users:
        row = staff_rows(users_page).filter(has_text=user["email"])
        expect(row, f"нет строки {user['email']}").to_have_count(1)
        expect(row).to_contain_text(display_name(user))
        expect(row).to_contain_text(role_labels[user["role"]])
        is_me = user["email"] == qa_user["email"]
        expect(row.get_by_text("(вы)")).to_have_count(1 if is_me else 0)
        # себя удалить нельзя — кнопки нет только у своей строки
        expect(row.get_by_role("button", name="Удалить из организации")).to_have_count(0 if is_me else 1)


def test_module_access_counts_all_modules(users_page: Page):
    modules = api_get(users_page, "/api/admin/auth/invite-modules").json()["modules"]

    expect(users_page.get_by_text(re.compile(rf"Доступ к модулям: \d+ из {len(modules)}$"))).to_be_visible()


@pytest.mark.writes_data
def test_invite_link_create_and_revoke(users_page: Page):
    text = note()
    users_page.locator("main select").first.select_option(label="Сотрудник")
    users_page.get_by_placeholder("Для кого приглашение").fill(text)

    with users_page.expect_response(lambda r: r.url.endswith(INVITES) and r.request.method == "POST") as created:
        users_page.get_by_role("button", name="Создать ссылку").click()
    assert created.value.status == 200
    sent = created.value.request.post_data_json
    invite = created.value.json()
    assert sent["role"] == "support_agent" and sent["note"] == text and sent["send_email"] is False
    assert invite["web_link"] == f"https://app.bord.one/invite/{invite['code']}"
    assert invite["expires_in"] == 24 * 3600

    expect(users_page.get_by_text("Приглашение создано! (Сотрудник)")).to_be_visible()
    expect(users_page.get_by_text(invite["web_link"], exact=True)).to_be_visible()
    expect(users_page.get_by_text("Действует 24 часа · Одноразовая ссылка")).to_be_visible()
    card = users_page.locator("main li").filter(has_text=text)
    expect(card).to_contain_text("Сотрудник")
    expect(card).to_contain_text(re.compile(r"Осталось 2[34]ч"))

    card.get_by_role("button", name="Отозвать").click()
    dialog = users_page.get_by_role("dialog", name="Отозвать приглашение?")
    with users_page.expect_response(lambda r: f"{INVITES}/{invite['code']}" in r.url and r.request.method == "DELETE") as revoked:
        dialog.get_by_role("button", name="Да").click()

    assert revoked.value.status == 200
    expect(card).to_have_count(0)
    assert all(i["code"] != invite["code"] for i in api_get(users_page, INVITES).json())


@pytest.mark.writes_data
def test_revoke_can_be_cancelled(users_page: Page):
    text = note()
    assert api_post(users_page, INVITES, {"role": "employee", "note": text, "send_email": False}).ok
    users_page.reload()
    card = users_page.locator("main li").filter(has_text=text)

    card.get_by_role("button", name="Отозвать").click()
    users_page.get_by_role("dialog", name="Отозвать приглашение?").get_by_role("button", name="Отмена").click()

    expect(card).to_be_visible()
    assert any(i.get("note") == text for i in api_get(users_page, INVITES).json())


@pytest.mark.writes_data
def test_invite_link_opens_for_guest(users_page: Page, browser: Browser, browser_context_args):
    invite = api_post(users_page, INVITES, {"role": "employee", "note": note(), "send_email": False}).json()
    guest = browser.new_context(**{k: v for k, v in browser_context_args.items() if k != "storage_state"}).new_page()
    try:
        with guest.expect_response(lambda r: f"/invite/{invite['code']}/info" in r.url) as info:
            guest.goto(invite["web_link"])
        assert info.value.ok, f"invite info → {info.value.status} ({INVITE_BUG})"
        data = info.value.json()
        assert data["org_name"] == "BORD QA" and data["role_label"] == "Гость"
        expect(guest.get_by_role("heading", name="BORD QA")).to_be_visible()
        expect(guest.get_by_text(f"{data['creator_name']} приглашает вас в BORD QA")).to_be_visible()
        expect(guest.get_by_text("Роль: Гость")).to_be_visible()
        expect(guest.get_by_role("button", name="Присоединиться")).to_be_visible()
        expect(guest.get_by_role("button", name="Зарегистрироваться")).to_be_visible()
        expect(guest.get_by_text("Приглашение истекло")).to_have_count(0)
    finally:
        guest.context.close()


@pytest.mark.writes_data
def test_revoked_invite_link_is_dead(users_page: Page, browser: Browser, browser_context_args):
    invite = api_post(users_page, INVITES, {"role": "employee", "note": note(), "send_email": False}).json()
    assert api_delete(users_page, f"{INVITES}/{invite['code']}").ok
    guest = browser.new_context(**{k: v for k, v in browser_context_args.items() if k != "storage_state"}).new_page()
    try:
        with guest.expect_response(lambda r: f"/invite/{invite['code']}/info" in r.url) as info:
            guest.goto(invite["web_link"])

        assert info.value.status == 404
        expect(guest.get_by_text("Приглашение истекло")).to_be_visible()
        expect(guest.get_by_role("button", name=re.compile("Принять приглашение|Присоединиться"))).to_have_count(0)
    finally:
        guest.context.close()


def test_add_from_platform_unknown_email(users_page: Page, api_users):
    users_page.get_by_role("button", name="Добавить из платформы").click()
    with users_page.expect_response(lambda r: "/api/admin/auth/users/search" in r.url) as search:
        users_page.get_by_placeholder("name@company.com").fill("qa-nobody-404@example.com")

    assert search.value.ok and search.value.json() == []
    expect(users_page.get_by_text("Пользователи не найдены. Отправьте приглашение по ссылке.")).to_be_visible()
    expect(staff_rows(users_page)).to_have_count(len(api_users))
