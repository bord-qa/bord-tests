"""Блок 3. Мой профиль: app.bord.one/admin/profile.

Снято 21.10.2026:
- «Моя информация»: имя, email, Telegram, роль — из localStorage admin_user;
- «Мои модули»: карточки с кнопкой «Открыть» — из /api/modules/mine;
- смена пароля: POST /api/admin/auth/change-password {old_password, new_password}.
  Пустые поля и пароль короче 8 символов останавливает сам браузер (required,
  minlength=8), несовпадение — фронт («Пароли не совпадают»), запрос не уходит.
  Неверный текущий → 401, на экране текст сервера «Invalid current password»
  (по-английски — вынесено юзеру как находка). Успех → «Пароль обновлён».
"""
import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_get
from helpers.auth import LOGIN_API, login
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

CHANGE_API = "/api/admin/auth/change-password"


class PasswordForm:
    def __init__(self, page: Page):
        self.page = page
        self.current = page.get_by_placeholder("Текущий пароль")
        self.new = page.get_by_placeholder("Новый пароль (мин. 8 символов)")
        self.repeat = page.get_by_placeholder("Повторите новый пароль")
        self.submit = page.get_by_role("button", name="Сменить пароль")

    def fill(self, current, new, repeat=None):
        self.current.fill(current)
        self.new.fill(new)
        self.repeat.fill(new if repeat is None else repeat)

    def change(self, current, new):
        self.fill(current, new)
        with self.page.expect_response(lambda r: CHANGE_API in r.url) as resp:
            self.submit.click()
        return resp.value


def open_profile(page: Page):
    page.goto("/admin/profile")
    expect(page.get_by_role("heading", name="Моя информация")).to_be_visible()
    settle(page)


def collect_change_requests(page: Page) -> list:
    sent = []
    page.on("request", lambda r: sent.append(r.url) if CHANGE_API in r.url else None)
    return sent


@pytest.fixture
def profile(auth_page: Page):
    open_profile(auth_page)
    return auth_page


# Кейс: «Мои данные» совпадают с аккаунтом
@pytest.mark.smoke
def test_my_info_matches_account(profile: Page, qa_user):
    user = profile.evaluate("() => JSON.parse(localStorage.getItem('admin_user'))")
    info = profile.locator("dl")

    expect(info.locator("div", has=profile.locator("dt", has_text="Email")).locator("dd")).to_have_text(qa_user["email"])
    expect(info.locator("div", has=profile.locator("dt", has_text="Имя")).locator("dd")).to_have_text(user["telegram_first_name"])
    expect(info.locator("div", has=profile.locator("dt", has_text="Роль")).locator("dd")).to_have_text("Владелец")
    tg = user.get("telegram_username")
    expect(info.locator("div", has=profile.locator("dt", has_text="Telegram")).locator("dd")).to_have_text(f"@{tg}" if tg else "—")


# Кейс: «Мои системы» совпадают с API
def test_my_modules_match_api(profile: Page):
    mine = api_get(profile, "/api/modules/mine")
    assert mine.ok, f"/api/modules/mine → {mine.status}"
    expected = [m["name"] for m in mine.json() if m["is_enabled"]]
    section = profile.locator("section").filter(
        has=profile.get_by_role("heading", name="Мои модули", exact=True)
    )

    names = section.get_by_role("heading", level=3).all_inner_texts()
    assert names == expected
    expect(section.get_by_role("link", name="Открыть")).to_have_count(len(expected))


# Кейс: Пустой и короткий пароль не отправляются
def test_password_empty_and_short_blocked_by_browser(profile: Page):
    form = PasswordForm(profile)
    sent = collect_change_requests(profile)

    form.submit.click()
    for field in (form.current, form.new, form.repeat):
        assert field.evaluate("e => e.validity.valueMissing")

    form.fill("any-current-pass", "short")
    form.submit.click()
    for field in (form.new, form.repeat):
        assert field.evaluate("e => e.validity.tooShort")

    profile.wait_for_timeout(500)
    assert not sent, "форма с невалидными полями ушла на сервер"


# Кейс: Пароли не совпадают — ошибка
def test_password_mismatch(profile: Page, qa_user):
    form = PasswordForm(profile)
    sent = collect_change_requests(profile)

    form.fill(qa_user["password"], "NewPassw0rd!", "OtherPassw0rd!")
    form.submit.click()

    expect(profile.get_by_text("Пароли не совпадают")).to_be_visible()
    assert not sent, "при несовпадении паролей запрос всё равно ушёл"


# Кейс: Неверный текущий пароль — ошибка
def test_password_wrong_current(profile: Page):
    resp = PasswordForm(profile).change("WrongCurrent1!", "NewPassw0rd!")

    assert resp.status == 401
    # Текст ошибки приходит с сервера, фронт показывает его как есть (до 28.10.2026 был по-английски).
    expect(profile.get_by_text(resp.json()["detail"])).to_have_text("Неверный текущий пароль")


# Кейс: Смена пароля и возврат старого
def test_password_change_and_revert(page: Page, qa_user, playwright):
    """Смена пароля туда и обратно. Свой вход (не общая сессия прогона).
    Пароль возвращается в finally; если откат не прошёл, рабочий пароль —
    tempPassword из users.json, тест об этом кричит."""
    temp = qa_user.get("tempPassword")
    if not temp:
        pytest.skip("нет tempPassword в users.json / USERS_JSON")
    api = playwright.request.new_context(base_url="https://app.bord.one")

    def can_login(password):
        return api.post(LOGIN_API, data={"email": qa_user["email"], "password": password}).status == 200

    login(page, qa_user)
    open_profile(page)
    form = PasswordForm(page)
    changed = False
    try:
        assert form.change(qa_user["password"], temp).status == 200
        changed = True
        expect(page.get_by_text("Пароль обновлён")).to_be_visible()
        for field in (form.current, form.new, form.repeat):
            expect(field).to_have_value("")
        assert can_login(temp), "новый пароль не пускает"
        assert not can_login(qa_user["password"]), "старый пароль всё ещё пускает"
    finally:
        if changed:
            page.reload()
            expect(form.current).to_be_visible()
            settle(page)
            reverted = form.change(temp, qa_user["password"]).status == 200
            assert reverted and can_login(qa_user["password"]), (
                "ПАРОЛЬ НЕ ВОЗВРАЩЁН: учётка сейчас на tempPassword из users.json"
            )
        api.dispose()
