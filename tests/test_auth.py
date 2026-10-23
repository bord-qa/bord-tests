"""Блок 1. Вход и выход на app.bord.one/admin.

Тексты и поведение сняты с живой формы 21.10.2026, см. helpers/auth.py.
Формы «Забыли пароль?», «Войти по ссылке» и регистрации только открываются —
отправка шлёт реальное письмо и создаёт учётку, это отдельные кейсы.
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.auth import LOGIN_API, LoginForm, has_token, login, logout_button

pytestmark = pytest.mark.requires_auth


@pytest.mark.smoke
def test_login_success(page: Page, qa_user):
    login(page, qa_user)

    expect(page).to_have_title(re.compile("Стартовая страница"))
    expect(page.get_by_title(re.compile(f"Организация: {qa_user['org']}"))).to_be_visible()
    assert has_token(page), "после входа нет admin_token в localStorage"


def test_login_wrong_password(page: Page, qa_user):
    form = LoginForm(page).open().fill(qa_user["email"], "WrongPass123!")
    with page.expect_response(lambda r: LOGIN_API in r.url) as resp:
        form.submit.click()

    assert resp.value.status == 401
    expect(form.error).to_be_visible()
    expect(form.heading).to_be_visible()
    assert not has_token(page)


def test_login_unknown_email_same_error(page: Page):
    """Несуществующий email даёт ту же ошибку, что и неверный пароль:
    по ответу нельзя узнать, зарегистрирована ли почта."""
    form = LoginForm(page).open().fill("qa-nobody-404@example.com", "WrongPass123!")
    with page.expect_response(lambda r: LOGIN_API in r.url) as resp:
        form.submit.click()

    assert resp.value.status == 401
    expect(form.error).to_be_visible()
    assert not has_token(page)


def test_login_empty_fields_not_sent(page: Page):
    form = LoginForm(page).open()
    sent = []
    page.on("request", lambda r: sent.append(r.url) if LOGIN_API in r.url else None)

    form.submit.click()
    page.wait_for_timeout(1000)

    assert not sent, "форма с пустыми полями ушла на сервер"
    for field in (form.email, form.password):
        assert field.evaluate("e => e.required && !e.validity.valid")
    expect(form.heading).to_be_visible()


@pytest.mark.smoke
@pytest.mark.parametrize("path", ["/admin/tasks", "/admin/users", "/admin/settings/company"])
def test_protected_page_shows_login_for_guest(page: Page, path):
    page.goto(path)

    expect(LoginForm(page).heading).to_be_visible()
    expect(logout_button(page)).to_have_count(0)


def test_session_survives_reload(auth_page: Page):
    auth_page.goto("/admin/tasks")
    expect(logout_button(auth_page)).to_be_visible()

    auth_page.reload()

    expect(logout_button(auth_page)).to_be_visible()
    expect(LoginForm(auth_page).heading).to_have_count(0)


def test_logout(page: Page, qa_user):
    # Свой вход, а не auth_page: выход не должен ломать общую сессию прогона.
    login(page, qa_user)

    logout_button(page).click()

    expect(LoginForm(page).heading).to_be_visible()
    assert not has_token(page)
    page.goto("/admin/tasks")
    expect(LoginForm(page).heading).to_be_visible()


@pytest.mark.parametrize(
    "link, heading, submit",
    [
        ("forgot", "Сброс пароля", "Отправить"),
        ("magic_link", "Вход по ссылке", "Отправить ссылку"),
        ("register", "Регистрация", "Зарегистрироваться"),
    ],
)
def test_auxiliary_forms_open_and_back(page: Page, link, heading, submit):
    form = LoginForm(page).open()

    getattr(form, link).click()

    expect(page.get_by_role("heading", name=heading)).to_be_visible()
    expect(page.get_by_role("button", name=submit, exact=True)).to_be_visible()
    back = page.get_by_role("button", name=re.compile("Назад ко входу|Войти$"))
    back.last.click()
    expect(form.heading).to_be_visible()


def test_sso_opens_crm_without_password(auth_page: Page):
    """Единый вход: после входа на app.bord.one CRM открывается без пароля."""
    with auth_page.expect_response(lambda r: "/api/admin/sso/exchange" in r.url) as resp:
        auth_page.goto("https://crm.bord.one/")

    assert resp.value.status == 200
    expect(auth_page).to_have_url(re.compile(r"crm\.bord\.one/admin/crm/"))
    expect(auth_page).to_have_title(re.compile("BORD CRM"))
