"""Вход в BORD (app.bord.one/admin) по email и паролю.

Как устроена авторизация (снято 21.10.2026):
- форма шлёт POST /api/admin/auth/email/login {email, password};
  успех → 200, неверные данные (и пароль, и несуществующий email) → 401
  с одним и тем же текстом «Неверный логин или пароль»;
- токен ложится в localStorage `admin_token` (рядом admin_user, admin_orgs,
  admin_current_org, admin_enabled_modules);
- для остальных систем (crm.bord.one и т.д.) ставится SSO-кука `bord_sso`
  на .bord.one — по ней система получает свой вход через /api/admin/sso/exchange.
"""
import json
import os
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, expect

from helpers.wait import settle

LOGIN_API = "/api/admin/auth/email/login"
USERS_FILE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "users.json"


def load_user(key: str = "qaUser") -> dict | None:
    """Учётка из env (CI) или tests/fixtures/users.json (локально).

    BORD_EMAIL/BORD_PASSWORD — только основная учётка: иначе «коллега»
    молча оказался бы тем же владельцем.
    """
    if key == "qaUser" and os.environ.get("BORD_EMAIL") and os.environ.get("BORD_PASSWORD"):
        return {
            "email": os.environ["BORD_EMAIL"],
            "password": os.environ["BORD_PASSWORD"],
            "org": os.environ.get("BORD_ORG", "BORD QA"),
        }
    if os.environ.get("USERS_JSON"):
        return json.loads(os.environ["USERS_JSON"]).get(key)
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text(encoding="utf-8")).get(key)
    return None


def classic_view(ctx: BrowserContext):
    """Классический вид BORD (боковое меню, каждая система на своём домене).

    С 17.11.2026 по умолчанию включён вид «BORD One» (кука bord_view=one на .bord.one):
    app.bord.one/admin/one/<система>/… — общая верхняя панель, а CRM, HRM и др. открываются
    внутри неё в iframe; заход на crm.bord.one/admin/… сразу переадресует в оболочку.
    Классический вид остался (bord_view=classic, переключатель вида у пользователя) —
    суита гоняет его; оболочку «BORD One» проверяет отдельный блок.
    """
    ctx.add_cookies([{"name": "bord_view", "value": "classic", "domain": ".bord.one", "path": "/",
                      "secure": True, "sameSite": "Lax"}])


class LoginForm:
    def __init__(self, page: Page):
        self.page = page
        self.email = page.get_by_placeholder("Логин или email")
        self.password = page.get_by_placeholder("Пароль")
        self.submit = page.get_by_role("button", name="Войти", exact=True)
        self.error = page.get_by_text("Неверный логин или пароль")
        self.forgot = page.get_by_role("button", name="Забыли пароль?")
        self.magic_link = page.get_by_role("button", name="Войти по ссылке")
        self.register = page.get_by_role("button", name="Зарегистрироваться")
        self.heading = page.get_by_role("heading", name="Вход в BORD")

    def open(self, path: str = "/admin"):
        self.page.goto(path)
        expect(self.email).to_be_visible()
        # Next.js: форма видна до гидрации, а клик до неё теряется.
        settle(self.page)
        return self

    def fill(self, email: str, password: str):
        self.email.fill(email)
        self.password.fill(password)
        return self


def logout_button(page: Page):
    # Иконка без текста внизу бокового меню, подпись только в title.
    return page.locator("aside button[title='Выйти']")


def login(page: Page, user: dict):
    """Войти через форму и дождаться интерфейса организации."""
    form = LoginForm(page).open()
    form.fill(user["email"], user["password"])
    with page.expect_response(lambda r: LOGIN_API in r.url) as resp:
        form.submit.click()
    assert resp.value.status == 200, f"вход вернул {resp.value.status}"
    expect(logout_button(page)).to_be_visible()
    settle(page)


def has_token(page: Page) -> bool:
    return page.evaluate("() => !!localStorage.getItem('admin_token')")
