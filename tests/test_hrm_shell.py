"""Блок 49. HRM — каркас: вход по SSO, меню, шапка, язык, уведомления, скрытые экраны.

Снято 02.11.2026 (в видимом MCP-браузере):
- hrm.bord.one/admin пускает по куке BORD (SSO-обмен ?module=hrm), тест-учётка в BORD QA —
  superuser, в шапке «BORD QA · enterprise», внизу меню — email и организация;
- меню — 4 группы, 20 пунктов (Управление / Учет / Рекрутинг / Документы и отчёты), каждый
  открывает свой экран с заголовком вкладки «<Пункт> — BORD HRM»; «Инструкции» →
  help.bord.one/hrm/ в новой вкладке;
- без пункта в меню, только по адресу: «Заявки на кадры» (/admin/requisitions),
  «Эффективность» (/admin/performance), «Вовлечённость» (/admin/engagement),
  «Должностные инструкции» (/admin/job-instructions — пункт меню появляется только с
  плагином job_instructions, сам экран открывается и без него);
- язык — кнопка «ru» → «Русский / Română», выбор хранится в куке NEXT_LOCALE (только у
  этого браузера), меню и заголовки переводятся сразу;
- колокольчик «Уведомления» — счётчик = GET /notifications/count {unread}, клик открывает
  последние 20 (GET /notifications/?limit=20) и «Прочитать все» (не жмём — это не наше).
"""
import re

import pytest
from playwright.sync_api import Browser, Page, expect

from helpers.hrm import HRM, hrm_get, hrm_json, hrm_me, hrm_org, items, open_hrm, screen_title, sidebar

pytestmark = pytest.mark.requires_auth

MENU = {
    "Управление": [("Дашборд", "/admin"), ("Сотрудники", "/admin/employees"), ("Оргструктура", "/admin/orgchart"),
                   ("Проекты", "/admin/projects")],
    "Учет": [("Отсутствия", "/admin/leave"), ("Табель", "/admin/timesheet"), ("ТМЦ", "/admin/tmc"),
             ("Компенсация", "/admin/compensation"), ("Повышение квалификации", "/admin/raise-requests"),
             ("Дисциплина", "/admin/discipline")],
    "Рекрутинг": [("Рекрутинг", "/admin/recruitment"), ("Источники откликов", "/admin/recruitment/sources"),
                  ("Адаптация", "/admin/onboarding"), ("Навыки", "/admin/skills"), ("Обучение", "/admin/training")],
    "Документы и отчёты": [("Документы", "/admin/doc-templates"), ("Отчёты", "/admin/reports"),
                           ("Уведомления", "/admin/notifications"), ("Настройки", "/admin/settings"),
                           ("Журнал действий", "/admin/audit")],
}
ALL_ITEMS = [item for group in MENU.values() for item in group]
HIDDEN = [("/admin/requisitions", "Заявки на кадры"), ("/admin/performance", "Эффективность"),
          ("/admin/engagement", "Вовлечённость"), ("/admin/job-instructions", "Должностные инструкции")]
RO_MENU_START = ["Tablou de bord", "Angajați", "Structura organizatorică", "Proiecte", "Absențe"]


def menu(page: Page):
    return sidebar(page).locator("nav")


# Кейс: Вход по куке BORD — HRM открывается под BORD QA, в шапке организация и тариф
def test_sso_entry(auth_page: Page):
    open_hrm(auth_page, "/admin")
    expect(auth_page).to_have_url(f"{HRM}/admin")
    org, me = hrm_org(auth_page), hrm_me(auth_page)
    assert (org["name"], org["role"]) == ("BORD QA", "superuser"), org
    badge = auth_page.locator("header").get_by_title(re.compile(r"^Организация: "))
    expect(badge).to_have_attribute("title", f"Организация: {org['name']} · {org['plan']}")
    expect(sidebar(auth_page)).to_contain_text(me["email"])
    expect(sidebar(auth_page)).to_contain_text(org["name"])


# Кейс: Гость без сессии BORD в HRM не попадает
def test_guest_is_not_let_in(browser: Browser, browser_context_args):
    ctx = browser.new_context(**browser_context_args)
    try:
        page = ctx.new_page()
        page.goto(f"{HRM}/admin/employees")
        expect(page).not_to_have_title("Сотрудники — BORD HRM", timeout=8_000)
        expect(page.get_by_role("button", name="Добавить", exact=True)).to_have_count(0)
        assert page.request.get(f"{HRM}/api/hrm/employees").status in (401, 403)
    finally:
        ctx.close()


# Кейс: Меню — 4 группы и 20 пунктов по порядку
def test_menu_groups(auth_page: Page):
    open_hrm(auth_page, "/admin")
    nav = menu(auth_page)
    for group in MENU:  # группа «Рекрутинг» и её пункт «Рекрутинг» называются одинаково
        expect(nav.get_by_text(group, exact=True).first).to_be_visible()
    expect(nav.locator("a")).to_have_text([name for name, _ in ALL_ITEMS])
    for name, href in ALL_ITEMS:
        expect(nav.get_by_role("link", name=name, exact=True)).to_have_attribute("href", href)


# Кейс: Каждый пункт меню открывает свой экран — адрес и заголовок экрана в шапке
def test_menu_items_open(auth_page: Page):
    open_hrm(auth_page, "/admin/employees")
    nav = menu(auth_page)
    for name, href in ALL_ITEMS:
        nav.get_by_role("link", name=name, exact=True).click()
        expect(auth_page).to_have_url(HRM + href)
        expect(screen_title(auth_page)).to_have_attribute("title", name, timeout=15_000)


# Кейс: Заголовок вкладки следует за меню — «<Пункт> — BORD HRM»
@pytest.mark.xfail(strict=False, reason="bord-44: примерно в 1 переходе из 10 заголовок вкладки остаётся «BORD HRM»")
def test_menu_tab_titles(auth_page: Page):
    open_hrm(auth_page, "/admin/employees")
    stuck = []
    for name, href in ALL_ITEMS:
        menu(auth_page).get_by_role("link", name=name, exact=True).click()
        expect(screen_title(auth_page)).to_have_attribute("title", name, timeout=15_000)
        try:
            expect(auth_page).to_have_title(f"{name} — BORD HRM", timeout=8_000)
        except AssertionError:
            stuck.append(f"{name}: «{auth_page.title()}»")
    assert not stuck, stuck


# Кейс: «Инструкции» ведут в справку HRM в новой вкладке
def test_help_link(auth_page: Page):
    open_hrm(auth_page, "/admin")
    link = sidebar(auth_page).get_by_role("link", name="Инструкции", exact=True)
    expect(link).to_have_attribute("href", "https://help.bord.one/hrm/")
    expect(link).to_have_attribute("target", "_blank")


# Кейс: Экраны без пункта в меню открываются по адресу
@pytest.mark.parametrize("path,title", HIDDEN, ids=[t for _, t in HIDDEN])
def test_hidden_screens_open(auth_page: Page, path, title):
    errors = []
    auth_page.on("response", lambda r: errors.append(f"{r.status} {r.url}") if "/api/hrm/" in r.url and r.status >= 500 else None)
    open_hrm(auth_page, path)
    expect(screen_title(auth_page)).to_have_attribute("title", title)
    expect(menu(auth_page).get_by_role("link", name=title, exact=True)).to_have_count(0)
    expect(auth_page.get_by_text("Application error")).to_have_count(0)
    assert not errors, errors


# Кейс: Язык — Română переводит меню и заголовок, Русский возвращает
def test_language_switch(auth_page: Page):
    open_hrm(auth_page, "/admin/employees")
    header = auth_page.locator("header")
    try:
        header.get_by_role("button", name="ru", exact=True).click()
        for lang in ("Русский", "Română", "English"):  # English добавили 03.11.2026
            expect(header.get_by_role("button", name=lang, exact=True)).to_be_visible()
        header.get_by_role("button", name="Română", exact=True).click()
        expect(screen_title(auth_page)).to_have_attribute("title", "Angajați")
        expect(auth_page.locator("html")).to_have_attribute("lang", "ro")
        expect(menu(auth_page).locator("a").first).to_have_text(RO_MENU_START[0])
        for name in RO_MENU_START:
            expect(menu(auth_page).get_by_role("link", name=name, exact=True)).to_be_visible()
        assert any(c["name"] == "NEXT_LOCALE" and c["value"] == "ro" for c in auth_page.context.cookies(HRM))
        auth_page.reload()
        expect(screen_title(auth_page)).to_have_attribute("title", "Angajați")  # выбор переживает перезагрузку

        header.get_by_role("button", name="ro", exact=True).click()
        header.get_by_role("button", name="Rusă", exact=True).click()  # в румынском интерфейсе пункт переведён
        expect(screen_title(auth_page)).to_have_attribute("title", "Сотрудники")
        expect(menu(auth_page).get_by_role("link", name="Сотрудники", exact=True)).to_be_visible()
    finally:
        auth_page.context.add_cookies([{"name": "NEXT_LOCALE", "value": "ru", "url": HRM}])


# Кейс: Колокольчик — счётчик непрочитанных и список = API
def test_notifications_bell(auth_page: Page):
    with auth_page.expect_response(lambda r: "/api/hrm/notifications/count" in r.url) as resp:
        open_hrm(auth_page, "/admin")
    unread = resp.value.json()["unread"]
    bell = auth_page.locator("header").get_by_role("button", name="Уведомления")
    if unread:
        expect(bell).to_contain_text(str(unread) if unread < 100 else "99")
    with auth_page.expect_response(lambda r: "/api/hrm/notifications/?" in r.url and "limit=20" in r.url) as lst:
        bell.click()
    data = items(lst.value.json())
    expect(auth_page.get_by_text("Прочитать все", exact=True)).to_be_visible()
    for n in data[:3]:
        expect(auth_page.get_by_text(n["title"], exact=True).first).to_be_visible()
    auth_page.keyboard.press("Escape")


# Кейс: «Сотрудники» — счётчик в шапке = активные из API, тест-учётка среди них
def test_me_in_employees(auth_page: Page):
    me = hrm_me(auth_page)
    emps = items(hrm_json(hrm_get(auth_page, "/employees", {"status_filter": "active", "limit": 1000})))
    assert me["id"] in [e["id"] for e in emps], [e["id"] for e in emps]
    open_hrm(auth_page, "/admin/employees")
    expect(screen_title(auth_page)).to_have_text(re.compile(rf"Сотрудники\s*{len(emps)}$"))
