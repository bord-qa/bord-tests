"""Блок 47. Facturi — настройки: общие, нумерация, шаблоны, плагины, команда, автоматизации,
проверки, почта, e-Factura и банк-интеграции (только чтение).

Снято 02.11.2026 (в видимом MCP-браузере):
- «Общие настройки» (/admin/settings) — оглавление: «Документы» (Организация, Юрлица,
  Шаблоны документов, Нумерация, Почта), «Интеграции» (e-Factura, Банковские интеграции),
  «Справочники» (Категории и правила, Банковские счета, Валюты, Контрагенты). «Почта»
  (/admin/settings/email) есть только здесь, в меню её нет;
- «Нумерация» = GET /api/numbering/ (items: label, prefix, next_number, padding,
  reset_period, example; id=null — «правило не задано — номер продолжает существующий
  ряд»): строка «Префикс / Следующий номер / Длина / Сброс / Пример» и «Завести» (POST),
  у заведённого правила — PUT. Правила не удаляются — тест работает со «Заявкой на
  оплату» (в BORD QA заявок нет) и возвращает префикс PR;
- «Шаблоны документов» — выбор типа и языка в шапке («Русский / Română / English»):
  клик по языку СРАЗУ заводит копию встроенного шаблона («Factură de plată (încorporat,
  RO)», «по умолчанию») и открывает редактор (HTML, переменные, «Открыть PDF»);
  «Удалить» → окно «Удалить шаблон …?» → «Да»;
- «Плагины» = GET /api/plugins/: «Включено: N из M», карточки «Включён / Отключён»,
  «Включить плагин / Отключить плагин», поиск «Поиск плагина...»;
- «Команда» = GET /api/team/members (в BORD QA: живой «Maxim» и тест-учётка) — только
  чтение: не приглашаем, роли не меняем, не удаляем;
- «Автоматизации» = /api/automations/overview: вкладки «Услуги · N / По контрагенту · N /
  По тексту · N / По направлению · N»; окно «Новая услуга» не сохраняем («Применить к уже
  лежащим документам» переразметит документы организации);
- «Проверки» = /api/selfcheck/latest (ночной прогон в 03:00); «Проверить сейчас» не жмём;
- «Почта» = /api/email-settings/: «Сейчас работает: … от no-reply@bord.one»; имя
  отправителя меняем и возвращаем, «Отправить пробное письмо» не жмём;
- e-Factura и банк-интеграции — «Нет подключений», «Добавить» / «Проверить связь» не жмём.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.facturi import (screen, FACTURI, QA_PREFIX, confirm, field, fx_delete, fx_get, fx_json, fx_post, fx_put,
                             open_fx, stamp)

pytestmark = pytest.mark.requires_auth

# с 15.11 оглавление перегруппировано: + «Уведомления» и «Правила и система», разделы переименованы
HUB = {
    "Документы": [("Организация", "/admin/organizations"), ("Юрлица", "/admin/legal-entities"),
                  ("Шаблоны документов", "/admin/settings/templates"), ("Нумерация", "/admin/settings/numbering"),
                  ("Почта для документов", "/admin/settings/email"),
                  ("Напоминания об оплате", "/admin/settings/payment-reminders")],
    "Уведомления": [("Telegram-бот", "/admin/settings/telegram")],
    "Интеграции": [("e-Factura", "/admin/settings/efactura"), ("Банк-интеграции", "/admin/bank-integrations"),
                   ("Выгрузка в 1С", "/admin/settings/onec")],
    "Правила и система": [("Автоматизации", "/admin/automations"), ("Плагины", "/admin/plugins"),
                          ("Команда", "/admin/team")],
    "Справочники": [("Статьи", "/admin/categories"), ("Направления", "/admin/business-lines"),
                    ("Сальдо на начало", "/admin/opening-balances"),
                    ("Банковские счета и кассы", "/admin/bank-accounts"), ("Валюты", "/admin/currencies"),
                    ("Контрагенты", "/admin/counterparties")],
}


def head(page: Page):
    return page.locator("header.sticky")


# ───────────────────────── общие настройки ─────────────────────────

# Кейс: «Общие настройки» — ссылки на разделы по группам
def test_settings_hub(fx_page: Page):
    open_fx(fx_page, "/admin/settings")
    main = fx_page.locator("main")
    for group, links in HUB.items():
        section = main.locator("div").filter(has=fx_page.get_by_role("heading", name=group, exact=True)).last
        for name, href in links:
            # по началу текста: в описании «Выгрузки в 1С» (с 10.11) есть слово «Контрагенты»
            link = section.get_by_role("link").filter(has_text=re.compile("^" + re.escape(name)))
            expect(link, name).to_have_attribute("href", href)
    screen(main).get_by_role("link").filter(has_text="Почта для документов").first.click()
    expect(fx_page).to_have_url(FACTURI + "/admin/settings/email")
    expect(fx_page).to_have_title("Почта — Facturi")


# ───────────────────────── нумерация ─────────────────────────

def numbering(page: Page) -> dict:
    return {i["entity_type"]: i for i in fx_json(fx_get(page, "/numbering/"))["items"]}


def numbering_row(page: Page, label: str):
    return page.locator("main tbody tr").filter(has=page.get_by_text(label, exact=True))


# Кейс: Нумерация — типы документов, префиксы и примеры = API
def test_numbering_matches_api(fx_page: Page):
    with fx_page.expect_response(lambda r: "/api/numbering/" in r.url):
        open_fx(fx_page, "/admin/settings/numbering")
    items = numbering(fx_page)
    expect(fx_page.locator("main tbody tr")).to_have_count(len(items))
    for it in items.values():
        row = numbering_row(fx_page, it["label"])
        expect(row).to_contain_text(it["example"])
        expect(row.locator("input").first).to_have_value(it["prefix"])
        if it["id"] is None:
            expect(row).to_contain_text("правило не задано")
    expect(fx_page.locator("main")).to_contain_text("Номера не переиспользуются")


@pytest.fixture
def pr_rule(fx_page: Page):
    """Правило «Заявки на оплату» — после теста префикс PR и номер 1, как по умолчанию."""
    yield
    rule = numbering(fx_page)["payment_request"]
    if rule["id"]:
        fx_put(fx_page, f"/numbering/{rule['id']}", {"prefix": "PR", "next_number": 1, "padding": 4, "reset_period": "yearly"})


# Кейс: Правило нумерации — пример меняется на лету, сохранение = API
@pytest.mark.writes_data
def test_numbering_rule_save(fx_page: Page, pr_rule):
    open_fx(fx_page, "/admin/settings/numbering")
    row = numbering_row(fx_page, "Заявка на оплату")
    inputs = row.locator("input")
    inputs.nth(0).fill("PRQ")
    inputs.nth(1).fill("7")
    inputs.nth(2).fill("5")
    expect(row).to_contain_text(f"PRQ-{dt.date.today().year}-00007")  # пример — до сохранения

    with fx_page.expect_response(lambda r: "/api/numbering/" in r.url and r.request.method in ("POST", "PUT")) as resp:
        row.get_by_role("button", name=re.compile("^(Завести|Сохранить)$")).click()
    assert resp.value.ok, resp.value.text()
    rule = numbering(fx_page)["payment_request"]
    assert (rule["prefix"], rule["next_number"], rule["padding"]) == ("PRQ", 7, 5), rule
    assert rule["id"] is not None
    fx_page.reload()
    expect(numbering_row(fx_page, "Заявка на оплату").locator("input").first).to_have_value("PRQ")


# ───────────────────────── шаблоны ─────────────────────────

@pytest.fixture
def templates(fx_page: Page):
    before = {t["id"] for t in fx_json(fx_get(fx_page, "/pdf-templates/"))}
    yield
    for t in fx_json(fx_get(fx_page, "/pdf-templates/")):
        if t["id"] not in before:
            fx_delete(fx_page, f"/pdf-templates/{t['id']}")


# Кейс: Шаблон из встроенного по клику на язык — редактор, удаление
@pytest.mark.writes_data
def test_template_from_builtin(fx_page: Page, templates):
    with fx_page.expect_response(lambda r: "/api/pdf-templates/" in r.url):
        open_fx(fx_page, "/admin/settings/templates")
    expect(fx_page.locator("main")).to_contain_text("Своих шаблонов нет")
    with fx_page.expect_response(lambda r: "/api/pdf-templates/" in r.url and r.request.method == "POST") as resp:
        head(fx_page).get_by_role("button", name="Română", exact=True).click()
    assert resp.value.ok, resp.value.text()
    tpl = resp.value.json()
    assert (tpl["entity_type"], tpl["name"]) == ("payment_invoice", "Factură de plată (încorporat, RO)"), tpl

    main = fx_page.locator("main")
    expect(main.get_by_text("Factură de plată (încorporat, RO)").first).to_be_visible()
    expect(main.locator("textarea")).to_have_value(re.compile(r'<html lang="ro"'))
    expect(screen(main).get_by_role("button", name=re.compile(r"^\{\{number\}\}"))).to_be_visible()
    expect(head(fx_page).get_by_title("Шаблоны документов")).to_have_text("Шаблоны документов1")

    screen(main).get_by_role("button", name="Удалить", exact=True).click()
    with fx_page.expect_response(lambda r: f"/api/pdf-templates/{tpl['id']}" in r.url and r.request.method == "DELETE") as gone:
        text = confirm(fx_page, "Да")
    assert "Удалить шаблон «Factură de plată (încorporat, RO)»?" in text
    assert gone.value.ok
    assert tpl["id"] not in [t["id"] for t in fx_json(fx_get(fx_page, "/pdf-templates/"))]


# ───────────────────────── плагины ─────────────────────────

def plugin_card(page: Page, name: str):
    """Карточка целиком: переключатель (кнопка без текста, title «Включить/Отключить плагин»)
    сидит в шапке рядом с заголовком, статус — в подвале."""
    return page.locator("main div.rounded-xl").filter(has=page.get_by_role("heading", name=name, exact=True))


def plugin_status(enabled: bool):
    """Включённый плагин со своими настройками (e-Factura, банк-интеграции) вместо «Включён»
    показывает ссылку «Настроить»."""
    return re.compile("Включён|Настроить") if enabled else re.compile("Отключён")


# Кейс: Плагины — «Включено N из M» и статусы = API
def test_plugins_match_api(fx_page: Page):
    with fx_page.expect_response(lambda r: r.url.endswith("/api/plugins/")) as resp:
        open_fx(fx_page, "/admin/plugins")
    plugins = resp.value.json()
    on = sum(p["is_enabled"] for p in plugins)
    expect(fx_page.locator("main")).to_contain_text(f"Включено: {on} из {len(plugins)}")
    for p in plugins:
        card = plugin_card(fx_page, p["name"])
        expect(card).to_contain_text(plugin_status(p["is_enabled"]))
        expect(card.get_by_role("button", name="Отключить плагин" if p["is_enabled"] else "Включить плагин")).to_be_visible()


# Кейс: Плагин «Акт сверки» — включить и выключить
@pytest.mark.writes_data
def test_plugin_toggle(fx_page: Page):
    was = next(p for p in fx_json(fx_get(fx_page, "/plugins/")) if p["plugin_key"] == "akt_sverki")["is_enabled"]
    open_fx(fx_page, "/admin/plugins")
    card = plugin_card(fx_page, "Акт сверки")
    try:
        for target in (not was, was):
            with fx_page.expect_response(lambda r: "/api/plugins/akt_sverki" in r.url and r.request.method == "PUT") as resp:
                card.get_by_role("button", name="Включить плагин" if target else "Отключить плагин").click()
            assert resp.value.ok, resp.value.text()
            expect(card).to_contain_text(plugin_status(target))
            now = next(p for p in fx_json(fx_get(fx_page, "/plugins/")) if p["plugin_key"] == "akt_sverki")["is_enabled"]
            assert now is target
    finally:
        fx_put(fx_page, "/plugins/akt_sverki", {"is_enabled": was})


# Кейс: Плагины — поиск по названию
def test_plugins_search(fx_page: Page):
    open_fx(fx_page, "/admin/plugins")
    screen(fx_page).get_by_placeholder("Поиск плагина...").fill("Зарплата")
    titles = fx_page.locator("main h3")
    expect(titles).to_have_text(["Зарплата", "Зарплата от сделок"])


# ───────────────────────── команда, автоматизации, проверки ─────────────────────────

# Кейс: Команда — участники и роли = API (только чтение)
def test_team_matches_api(fx_page: Page):
    with fx_page.expect_response(lambda r: "/api/team/members" in r.url) as resp:
        open_fx(fx_page, "/admin/team")
    members = resp.value.json()
    roles = {"owner": "Владелец", "admin": "Администратор", "accountant": "Бухгалтер", "viewer": "Наблюдатель"}
    rows = fx_page.locator("main tbody tr")
    expect(rows).to_have_count(len(members))
    for m in members:
        row = rows.filter(has_text=m["name"]).first
        expect(row.get_by_role("button", name=roles.get(m["role"], m["role"]))).to_be_visible()
    expect(rows.filter(has_text="(вы)")).to_contain_text("maxim.turcan1")
    expect(head(fx_page).get_by_role("button", name="Пригласить")).to_be_visible()


# Кейс: Автоматизации — счётчики вкладок = API, окно «Новая услуга» закрывается без сохранения
def test_automations(fx_page: Page):
    with fx_page.expect_response(lambda r: "/api/automations/overview" in r.url) as resp:
        open_fx(fx_page, "/admin/automations")
    counts = resp.value.json()["counts"]
    main = fx_page.locator("main")
    expect(screen(main).get_by_role("button", name=f"Услуги · {counts['services']}")).to_be_visible()

    posts = []
    fx_page.on("request", lambda r: posts.append(r.url) if r.method != "GET" and "/api/" in r.url else None)
    screen(main).get_by_role("button", name="Услуга", exact=True).click()
    form = fx_page.locator("div.fixed.inset-0").filter(has=fx_page.get_by_role("heading", name="Новая услуга")).last
    for text in ("Продаём (доход)", "Покупаем (расход)", "Посмотреть, что изменится"):
        expect(form.get_by_role("button", name=text)).to_be_visible()
    form.get_by_role("button", name="Отмена", exact=True).click()
    expect(form).to_have_count(0)
    assert not posts, posts


# Кейс: Проверки — список ночных проверок = API
def test_selfcheck_matches_api(fx_page: Page):
    with fx_page.expect_response(lambda r: "/api/selfcheck/latest" in r.url) as resp:
        open_fx(fx_page, "/admin/selfcheck")
    items = resp.value.json()["items"]
    main = fx_page.locator("main")
    for it in items:
        expect(main.get_by_text(it["title"], exact=True).first).to_be_visible()
    run_at = max(i["run_at"] for i in items if i.get("run_at"))
    local = dt.datetime.fromisoformat(run_at).astimezone(dt.timezone(dt.timedelta(hours=2)))
    expect(main).to_contain_text(f"Последний прогон: {local.strftime('%d.%m.%Y')}")
    expect(head(fx_page).get_by_role("button", name="Проверить сейчас")).to_be_visible()


# ───────────────────────── почта, e-Factura, банки ─────────────────────────

@pytest.fixture
def email_restore(fx_page: Page):
    before = fx_json(fx_get(fx_page, "/email-settings/"))
    yield before
    fx_put(fx_page, "/email-settings/", {k: before[k] for k in ("provider", "email_from", "email_from_name", "email_reply_to", "email_cc")})


# Кейс: Почта — действующий способ = API, имя отправителя сохраняется
@pytest.mark.writes_data
def test_email_settings(fx_page: Page, email_restore):
    open_fx(fx_page, "/admin/settings/email")
    eff = email_restore["effective"]
    main = fx_page.locator("main")
    expect(main).to_contain_text(f"письма уходят от {eff['from_email']}")

    name = f"{QA_PREFIX} {stamp()}"
    field(main, "Имя отправителя").fill(name)
    with fx_page.expect_response(lambda r: "/api/email-settings" in r.url and r.request.method == "PUT") as resp:
        screen(main).get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    assert fx_json(fx_get(fx_page, "/email-settings/"))["email_from_name"] == name
    fx_page.reload()
    expect(field(fx_page.locator("main"), "Имя отправителя")).to_have_value(name)


# Кейс: e-Factura — нет подключений, вкладки открываются
def test_efactura_settings(fx_page: Page):
    open_fx(fx_page, "/admin/settings/efactura")
    assert fx_json(fx_get(fx_page, "/efactura/configs"))["items"] == []
    main = fx_page.locator("main")
    expect(main.get_by_text("Нет подключений")).to_be_visible()
    for tab in ("Полнота", "Автовыставление", "Подписанты", "Логи API", "Подключения"):
        screen(main).get_by_role("button", name=tab, exact=True).click()
        expect(screen(main).get_by_role("button", name=tab, exact=True)).to_be_visible()
    expect(main.get_by_text("Нет подключений")).to_be_visible()


# Кейс: Банковские интеграции — подключения = API
def test_bank_integrations(fx_page: Page):
    with fx_page.expect_response(lambda r: "/api/banks/credentials" in r.url) as resp:
        open_fx(fx_page, "/admin/bank-integrations")
    creds = resp.value.json()
    main = fx_page.locator("main")
    if not creds:
        expect(main.get_by_text("Нет подключений")).to_be_visible()
    for tab in ("Выписки файлом", "Оплаты картой", "Подключения"):
        expect(screen(main).get_by_role("button", name=tab, exact=True)).to_be_visible()
