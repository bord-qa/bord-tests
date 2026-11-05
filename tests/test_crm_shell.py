"""Блок 20. CRM — вход и разделы: crm.bord.one/admin.

Снято 27.10.2026:
- вход только по SSO из app.bord.one (helpers/crm.py), в шапке организация
  «BORD QA · enterprise» и кнопка пользователя «maxim.turcan1»;
- меню слева: Работа (Сделки, Клиенты, Задачи, Календарь), Настройка (Рабочие
  процессы, Автоматизации), Аналитика (Отчёты, Отчёт по времени, Мониторинг),
  Администрирование (Оплата и подписки → app.bord.one/admin/subscription/billing
  в новой вкладке — с 04.11 вместо «Баланс ИИ-анализа», старый /admin/crm/wallet
  уводит туда же; Команда, Настройки, Плагины, Документация → help.bord.one/crm/). Разделы плагинов (Лиды, Компании,
  Проекты, Бронирование…) в BORD QA выключены и в меню не видны;
- заголовок вкладки — «<Раздел> · BORD CRM»;
- язык RU/RO/EN внизу меню → PUT /api/crm/user-prefs/me {ui_lang} (хранится
  на сервере у пользователя, поэтому тест возвращает RU в любом исходе).
"""
import pytest
from playwright.sync_api import Page, Response, expect

from helpers.crm import CRM, crm_get, crm_put, open_crm, sidebar

pytestmark = pytest.mark.requires_auth

# раздел → заголовок вкладки
SECTIONS = {
    "/admin/crm/deals": "Сделки",
    "/admin/crm/contacts": "Клиенты",
    "/admin/crm/tasks": "Задачи",
    "/admin/calendar": "Календарь",
    "/admin/crm/processes": "Рабочие процессы",
    "/admin/crm/automations": "Автоматизации",
    "/admin/crm/reports": "Отчёты",
    "/admin/crm/time-report": "Отчёт по времени",
    "/admin/crm/monitoring": "Мониторинг",
    "/admin/crm/team": "Команда",
    "/admin/crm/settings": "Настройки",
    "/admin/plugins": "Плагины",
}
BILLING = "https://app.bord.one/admin/subscription/billing"
_names = list(SECTIONS.values())
MENU = _names[:_names.index("Команда")] + ["Оплата и подписки"] + _names[_names.index("Команда"):] + ["Документация"]


# Кейс: CRM открывается из сессии BORD с организацией BORD QA
@pytest.mark.smoke
def test_crm_opens_with_org(auth_page: Page):
    open_crm(auth_page)

    assert auth_page.url.startswith(CRM + "/admin"), f"увело на {auth_page.url}"
    expect(auth_page.locator("input[type=password]")).to_have_count(0)
    expect(auth_page.get_by_title("Организация: BORD QA", exact=False)).to_be_visible()
    me = crm_get(auth_page, "/user-prefs/me")
    assert me.ok, f"API CRM не пускает с токеном SSO: {me.status}"


# Кейс: Меню CRM — все разделы ядра на месте
def test_crm_menu(auth_page: Page):
    open_crm(auth_page)

    links = sidebar(auth_page).locator("nav a")
    expect(links).to_have_text(MENU)
    expect(sidebar(auth_page).get_by_role("link", name="Документация")).to_have_attribute(
        "href", "https://help.bord.one/crm/")
    expect(sidebar(auth_page).get_by_role("link", name="Оплата и подписки")).to_have_attribute("href", BILLING)


# Кейс: Раздел CRM открывается без ошибок API
@pytest.mark.parametrize("path", list(SECTIONS), ids=list(SECTIONS.values()))
def test_crm_section_opens(auth_page: Page, path):
    errors: list[str] = []

    def on_response(resp: Response):
        if "/api/" in resp.url and resp.status >= 500:
            errors.append(f"{resp.status} {resp.request.method} {resp.url}")

    auth_page.on("response", on_response)
    auth_page.goto(CRM + path)
    expect(auth_page).to_have_title(f"{SECTIONS[path]} · BORD CRM", timeout=20_000)
    # данные раздела догружаются после заголовка; поток событий не затихает,
    # поэтому ждём не тишины в сети, а фиксированное окно на поздние ответы
    auth_page.wait_for_timeout(2_500)

    assert not errors, "API ответил 5xx:\n" + "\n".join(errors)
    expect(auth_page.get_by_text("Ошибка 500")).to_have_count(0)


# Кейс: Переход по меню открывает раздел
def test_crm_menu_navigation(auth_page: Page):
    open_crm(auth_page, "/admin/crm/reports")

    sidebar(auth_page).get_by_role("link", name="Клиенты", exact=True).click()
    expect(auth_page).to_have_url(CRM + "/admin/crm/contacts")
    expect(auth_page).to_have_title("Клиенты · BORD CRM")
    sidebar(auth_page).get_by_role("link", name="Задачи", exact=True).click()
    expect(auth_page).to_have_url(CRM + "/admin/crm/tasks")


# Кейс: Язык интерфейса RU → EN → RU, выбор хранится у пользователя
def test_crm_language_switch(auth_page: Page):
    open_crm(auth_page, "/admin/crm/contacts")
    menu = sidebar(auth_page)
    try:
        with auth_page.expect_response(lambda r: "/user-prefs/me" in r.url and r.request.method == "PUT") as resp:
            menu.get_by_role("button", name="EN", exact=True).click()
        assert resp.value.ok, f"смена языка: {resp.value.status}"
        expect(menu.get_by_role("link", name="Deals", exact=True)).to_be_visible()
        expect(auth_page).to_have_title("Clients · BORD CRM")
        assert crm_get(auth_page, "/user-prefs/me").json().get("ui_lang") == "en"

        menu.get_by_role("button", name="RU", exact=True).click()
        expect(menu.get_by_role("link", name="Сделки", exact=True)).to_be_visible()
    finally:
        crm_put(auth_page, "/user-prefs/me", {"ui_lang": "ru"})
