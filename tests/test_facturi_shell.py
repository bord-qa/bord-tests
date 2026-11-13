"""Блок 38. Facturi — вход и разделы: app.facturi.md/admin.

Снято 02.11.2026 (helpers/facturi.py — как устроен вход):
- гость: /admin/* → /login?next=<путь>, API → 401; на форме входа способы
  «Пароль / SMS / Ссылка / Telegram / BORD», неверная пара → 401 от
  /auth/local/password и «Неверная почта или пароль…»;
- кнопка «BORD» входит без пароля через OAuth app.bord.one и возвращает на next;
- шапка: «Организация: BORD QA · free» и кнопка пользователя; в её меню
  «Активные сессии», «Аккаунт и безопасность», «Выйти», «Выйти со всех
  устройств» (последнюю не жмём никогда);
- «Выйти» шлёт и app.bord.one/api/admin/sso/logout — поэтому тест выхода
  входит в BORD сам, иначе погасил бы общую SSO-сессию прогона;
- меню на десктопе — рельс из 9 групп (Обзор … Настройки), клик по группе
  открывает панель div.fixed[role=menu] с её ссылками; полный список (с 12.11 —
  61 экран + «Документация»: «Заказы магазина» в «Продажах», «Telegram-бот» в «Настройках») лежит и в скрытом мобильном меню aside. Набор
  зависит от плагинов BORD QA (e-Factura, банк-интеграции, зарплата, юнит-экономика);
- «С чего начать» на дашборде = /api/onboarding/checklist: «N из 5», у
  выполненных шагов нет подсказки. «Скрыть» не жмём — прячет для всей организации.
"""
import re

import pytest
from playwright.sync_api import Browser, Page, Response, expect

from helpers.auth import login
from helpers.facturi import (FACTURI, RAIL_GROUPS, bord_login, drawer, fx_get, fx_json, fx_me,
                             open_fx, rail_open, user_menu)

pytestmark = pytest.mark.requires_auth

# пункт меню → адрес (порядок как на экране)
MENU = [
    ("Дашборд", "/admin"),
    ("Инбокс", "/admin/inbox"),
    ("Документы", "/admin/documents"),
    ("Подтвердить накладные", "/admin/efactura-approvals"),
    ("Юнит-экономика", "/admin/unit-economics"),
    ("Документация", "https://help.bord.one/facturi/"),
    ("Прибыли и убытки", "/admin/reports?report=pnl"),
    ("Упрощённый P&L", "/admin/reports?report=pnl_simple"),
    ("Точка безубыточности", "/admin/reports?report=breakeven"),
    ("Денежный поток", "/admin/reports?report=cashflow"),
    ("План денег", "/admin/reports?report=cash_plan"),
    ("Динамика остатков", "/admin/reports?report=balance_dynamics"),
    ("Распределение", "/admin/reports?report=allocation"),
    ("Налоги", "/admin/reports?report=taxes"),
    ("По направлениям", "/admin/reports?report=business_lines"),
    ("Расход без документа", "/admin/reports?report=expenses_no_doc"),
    ("Деньги", "/admin/payments"),
    ("Касса", "/admin/cash-desk"),  # с 08.11
    ("Платёжный календарь", "/admin/payment-calendar"),
    ("Регулярные", "/admin/recurring"),
    ("Paynet", "/admin/paynet"),
    ("Платёжные шлюзы", "/admin/payment-gateways"),
    ("Бюджеты", "/admin/budgets"),
    ("Продукты", "/admin/subscriptions"),  # с 08.11
    ("Долги", "/admin/debtors"),
    ("Обязательства", "/admin/obligations"),
    ("Акты сверки", "/admin/reconciliation-acts"),
    ("Кредиты", "/admin/credits"),
    ("Сделки", "/admin/sales"),
    ("Заказы магазина", "/admin/ecom-orders"),  # с 12.11
    ("Офферы", "/admin/estimates"),  # до 04.11 — «Оферты»
    ("Счета на оплату", "/admin/payment-invoices"),
    ("Акты", "/admin/acts"),
    ("Накладные", "/admin/invoices"),
    ("Каталог услуг", "/admin/catalog"),
    ("Ведомости", "/admin/salary"),
    ("Сотрудники", "/admin/salary/employees"),
    ("Выплаты (архив)", "/admin/salary/payments"),
    ("Отсутствия", "/admin/salary/absences"),
    ("Премии по правилам", "/admin/salary/bonuses"),
    ("Производственный календарь", "/admin/salary/calendar"),
    ("Справочники", "/admin/salary/directories"),
    ("Контрагенты", "/admin/counterparties"),
    ("Организации", "/admin/organizations"),
    ("Юрлица", "/admin/legal-entities"),
    ("Банк. счета", "/admin/bank-accounts"),
    ("Счета учёта", "/admin/accounts"),
    ("Категории", "/admin/categories"),
    ("Бизнес-линии", "/admin/business-lines"),
    ("Валюты", "/admin/currencies"),
    ("Сальдо на начало", "/admin/opening-balances"),
    ("Шаблоны документов", "/admin/settings/templates"),
    ("Нумерация", "/admin/settings/numbering"),
    ("Банк-интеграции", "/admin/bank-integrations"),
    ("e-Factura", "/admin/settings/efactura"),
    ("Автоматизации", "/admin/automations"),
    ("Команда", "/admin/team"),
    ("Плагины", "/admin/plugins"),
    ("Проверки", "/admin/selfcheck"),
    ("Общие настройки", "/admin/settings"),
    ("Telegram-бот", "/admin/settings/telegram"),  # с 12.11
    ("Профиль", "/admin/profile"),
]
SCREENS = [(name, href) for name, href in MENU if href.startswith("/admin")]
# сколько пунктов MENU (по порядку) лежит в каждой группе рельса RAIL_GROUPS
GROUP_SIZES = [6, 10, 12, 7, 7, 9, 2, 2, 7]


def menu_links(page: Page):
    # логотип «F Facturi» тоже ссылка на /admin — пункты меню идут в nav после него
    return drawer(page).locator("nav a")


# Кейс: Вход кнопкой «BORD» без пароля возвращает на нужный экран
@pytest.mark.smoke
def test_bord_login_keeps_next(browser: Browser, auth_state, browser_context_args):
    ctx = browser.new_context(**browser_context_args, storage_state=auth_state)
    page = ctx.new_page()
    try:
        page.goto(f"{FACTURI}/admin/counterparties")
        # своей куки у Facturi ещё нет — даже при живой сессии BORD сначала форма
        page.wait_for_url(re.compile(r"/login\?next=%2Fadmin%2Fcounterparties"), timeout=20_000)

        page.get_by_test_id("bord-login").click()
        page.wait_for_url(f"{FACTURI}/admin/counterparties", timeout=30_000)
        expect(page).to_have_title("Контрагенты — Facturi", timeout=20_000)
        expect(page.locator("input[type=password]")).to_have_count(0)
    finally:
        ctx.close()


# Кейс: Вошли в организацию BORD QA тест-учёткой
@pytest.mark.smoke
def test_me_is_qa_user_in_bord_qa(fx_page: Page, qa_user):
    open_fx(fx_page)
    me = fx_me(fx_page)

    assert me["current_org"]["name"] == qa_user.get("org", "BORD QA"), me["current_org"]
    # с 08.11 верхний role у владельца — «superuser» (права «*» в своей организации);
    # главное — владелец BORD QA и НЕ администратор платформы
    assert me["user"]["role"] == "owner", me["user"]["role"]
    assert [(o["name"], o["role"]) for o in me["orgs"]] == [(me["current_org"]["name"], "owner")], me["orgs"]
    assert me["platform_role"] is None and me["user"]["platform_role"] is None, me["platform_role"]
    assert me["user"]["bord_linked"] is True
    head = fx_page.locator("header.sticky")
    expect(head.get_by_title(f"Организация: {me['current_org']['name']} · {me['current_org']['plan']}")).to_be_visible()
    expect(user_menu(fx_page, me["user"]["name"])).to_be_visible()


# Кейс: Гость попадает на вход и не получает данных
def test_guest_redirected_to_login(browser: Browser, browser_context_args):
    ctx = browser.new_context(**browser_context_args)
    page = ctx.new_page()
    try:
        page.goto(f"{FACTURI}/admin/payments")
        page.wait_for_url(re.compile(r"/login\?next=%2Fadmin%2Fpayments"), timeout=20_000)
        expect(page.get_by_role("heading", name="Facturi")).to_be_visible()
        expect(page.get_by_text("Вход по паролю")).to_be_visible()

        api = page.request.get(f"{FACTURI}/api/counterparties/")
        assert api.status == 401, f"API без входа ответил {api.status}"
    finally:
        ctx.close()


# Кейс: Неверная почта или пароль — ошибка на форме
def test_wrong_password(browser: Browser, browser_context_args):
    ctx = browser.new_context(**browser_context_args)
    page = ctx.new_page()
    try:
        page.goto(f"{FACTURI}/login")
        page.wait_for_load_state("networkidle")  # клик до гидрации теряется
        page.get_by_label("Электронная почта").fill("qa-autotest-nobody@example.com")
        page.get_by_label("Пароль").fill("wrong-password-123")
        with page.expect_response(lambda r: "/auth/local/password" in r.url) as resp:
            page.get_by_role("button", name="Войти", exact=True).click()

        assert resp.value.status == 401, resp.value.status
        expect(page.get_by_text("Неверная почта или пароль. Проверьте данные и попробуйте снова.")).to_be_visible()
        assert "/login" in page.url
    finally:
        ctx.close()


# Кейс: Способы входа переключаются (SMS, ссылка, Telegram, пароль)
@pytest.mark.parametrize("method, title, control", [
    ("SMS", "Войти по SMS", "Получить код"),
    ("Ссылка", "Вход по ссылке из письма", "Получить ссылку для входа"),
    ("Telegram", "Вход через Telegram", None),
], ids=["SMS", "Ссылка", "Telegram"])
def test_login_methods_switch(browser: Browser, browser_context_args, method, title, control):
    ctx = browser.new_context(**browser_context_args)
    page = ctx.new_page()
    try:
        page.goto(f"{FACTURI}/login")
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name=method, exact=True).click()

        expect(page.get_by_text(title, exact=True)).to_be_visible()
        if control:
            expect(page.get_by_role("button", name=control)).to_be_visible()
        # обратно на пароль — кнопка «Пароль» появляется вместо выбранного способа
        page.get_by_role("button", name="Пароль", exact=True).click()
        expect(page.get_by_text("Вход по паролю", exact=True)).to_be_visible()
        expect(page.get_by_test_id("bord-login")).to_be_visible()
    finally:
        ctx.close()


# Кейс: Меню Facturi — все разделы на месте
def test_menu(fx_page: Page):
    open_fx(fx_page)

    links = menu_links(fx_page)
    expect(links).to_have_text([name for name, _ in MENU])
    hrefs = [links.nth(i).get_attribute("href") for i in range(links.count())]
    assert hrefs == [href for _, href in MENU]


# Кейс: Группы рельса открывают те же разделы, что и меню
def test_rail_groups(fx_page: Page):
    open_fx(fx_page)

    # панель предыдущей группы сменяется не сразу — считать ссылки мгновенно нельзя
    # (ловили nth(10) от старой панели), поэтому ждём ровно пункты своей группы
    start = 0
    for group, size in zip(RAIL_GROUPS, GROUP_SIZES):
        names = [name for name, _ in MENU[start:start + size]]
        panel = rail_open(fx_page, group)
        expect(panel.locator("a")).to_have_text(names)
        hrefs = panel.locator("a").evaluate_all("els => els.map(e => e.getAttribute('href'))")
        assert hrefs == [href for _, href in MENU[start:start + size]], f"группа «{group}»: {hrefs}"
        start += size
        fx_page.keyboard.press("Escape")
    assert start == len(MENU), "на рельсе не все пункты меню"


# Кейс: Раздел Facturi открывается без ошибок
@pytest.mark.parametrize("name, path", SCREENS, ids=[n for n, _ in SCREENS])
def test_screen_opens(fx_page: Page, name, path):
    errors: list[str] = []

    def on_response(resp: Response):
        if "/api/" in resp.url and resp.status >= 500:
            errors.append(f"{resp.status} {resp.request.method} {resp.url}")

    fx_page.on("response", on_response)
    fx_page.on("pageerror", lambda e: errors.append(f"JS: {e}"))
    open_fx(fx_page, path)
    expect(fx_page.locator("main")).to_be_visible()
    # данные экрана догружаются после заголовка
    fx_page.wait_for_timeout(2_500)

    assert not errors, "\n".join(errors)
    expect(fx_page.get_by_text("Application error")).to_have_count(0)
    assert "/login" not in fx_page.url


# Кейс: Переход по меню открывает раздел
def test_menu_navigation(fx_page: Page):
    open_fx(fx_page)
    rail_open(fx_page, "Справочники").get_by_role("link", name="Контрагенты", exact=True).click()

    expect(fx_page).to_have_url(f"{FACTURI}/admin/counterparties")
    expect(fx_page).to_have_title("Контрагенты — Facturi")


# Кейс: «С чего начать» на дашборде совпадает с API
def test_onboarding_checklist_matches_api(fx_page: Page):
    with fx_page.expect_response(lambda r: "/api/onboarding/checklist" in r.url) as resp:
        open_fx(fx_page)
    data = resp.value.json()
    if data.get("dismissed"):
        pytest.skip("карточку «С чего начать» в BORD QA скрыли — сверять нечего")
    items = data["items"]
    done = sum(i["done"] for i in items)

    card = fx_page.locator("main section").filter(has=fx_page.get_by_role("heading", name="С чего начать"))
    expect(card).to_contain_text(f"{done} из {len(items)}")
    rows = card.locator("ol > li > a")
    expect(rows).to_have_count(len(items))
    for i, item in enumerate(items):
        row = rows.nth(i)
        expect(row).to_have_attribute("href", item["href"])
        expect(row).to_contain_text(item["title"])
        if item["done"]:
            expect(row).not_to_contain_text(item["hint"])
        else:
            expect(row).to_contain_text(item["hint"])


# Кейс: Меню пользователя — профиль и организация
def test_user_menu(fx_page: Page):
    open_fx(fx_page)
    me = fx_me(fx_page)
    user_menu(fx_page, me["user"]["name"]).click()

    menu = fx_page.get_by_role("menu")
    expect(menu).to_contain_text(me["current_org"]["name"])
    expect(menu).to_contain_text("Владелец")
    for name in ("Активные сессии", "Выйти", "Выйти со всех устройств"):
        expect(menu.get_by_role("button", name=name, exact=True)).to_be_visible()
    menu.get_by_role("link", name="Аккаунт и безопасность").click()

    expect(fx_page).to_have_url(f"{FACTURI}/admin/profile")
    expect(fx_page).to_have_title("Аккаунт и безопасность — Facturi")
    orgs = fx_page.locator("main").filter(has_text="Ваши организации")
    for org in me["orgs"]:
        expect(orgs).to_contain_text(org["name"])


# Кейс: Выход из Facturi
@pytest.mark.writes_data
def test_logout(browser: Browser, qa_user, browser_context_args):
    # свой вход в BORD: «Выйти» гасит и SSO-сессию BORD, общая сессия прогона должна жить
    ctx = browser.new_context(**browser_context_args)
    page = ctx.new_page()
    try:
        login(page, qa_user)
        bord_login(page)
        me = fx_me(page)
        user_menu(page, me["user"]["name"]).click()
        with page.expect_response(lambda r: "/auth/logout" in r.url) as resp:
            page.get_by_role("menu").get_by_role("button", name="Выйти", exact=True).click()

        assert resp.value.status < 400, resp.value.status  # 302 на /login — норма
        page.wait_for_url(re.compile(r"/login"), timeout=20_000)
        assert fx_get(page, "/auth/me").status == 401
        page.goto(f"{FACTURI}/admin")
        page.wait_for_url(re.compile(r"/login\?next=%2Fadmin"), timeout=20_000)
    finally:
        ctx.close()


# Кейс: Ссылка «Документация» ведёт в справку Facturi
def test_docs_link(fx_page: Page):
    open_fx(fx_page)
    link = rail_open(fx_page, "Обзор").get_by_role("link", name="Документация", exact=True)
    expect(link).to_have_attribute("href", "https://help.bord.one/facturi/")
    expect(link).to_have_attribute("target", "_blank")


# Кейс: API Facturi видит ту же организацию, что и экран
def test_api_same_org(fx_page: Page):
    open_fx(fx_page, "/admin/organizations")
    orgs = fx_json(fx_get(fx_page, "/organizations/"))
    me = fx_me(fx_page)

    assert [o["id"] for o in orgs] == [me["current_org"]["id"]], orgs
