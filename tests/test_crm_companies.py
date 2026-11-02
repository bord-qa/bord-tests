"""Блок 33. CRM — компании (плагин «Компании»): crm.bord.one/admin/crm/companies.

Снято в MCP-браузере 30.10.2026:
- отдельного пункта меню нет: «Клиенты» → вкладки «Физлица» / «Юрлица»; в шапке
  кнопка «Новая компания». Список: GET /api/crm/companies/?is_active=true&page=1&limit=25
  → {items, total, page_deal_amount_total…}, таблица «Название, Категория, ИНН/VAT,
  Контактов, Сделок, Сумма сделок, Ответственный», внизу «1–N из M»;
- форма — окно (не role=dialog), «Создать» неактивна без названия. POST /companies/
  → 201, окно закрывается и открывается карточка (?id=<id>). Ответственного без
  выбора ставит сервер — того, кто создал;
- занятый ИНН: 409 {detail: {code: vat_duplicate, existing_company_id, …}}, форма
  пишет «Компания с таким ИНН/VAT уже существует» и даёт ссылку «Открыть …»; занят и
  архивной. Название при этом не проверяется: тёзки допустимы;
- карточка — боковая панель с вкладками, вкладка в адресе (&tab=contacts|deals|tasks|
  comments|custom): «Реквизиты» + «Сохранить», «Контакты» (поиск, «Новый», звезда
  «основной», роль по клику, «Отвязать» с подтверждением), «Сделки» (ссылки
  /admin/crm/deals?id=…), «Задачи» (ссылка /admin/crm/tasks?create_company=<id>
  открывает форму с подставленной компанией), «Комментарии» (POST /activities/);
- «Архивировать» → «Архивировать компанию «…»?» (Отмена / Продолжить) → DELETE
  /companies/<id> = is_active=false; насовсем компании не удаляются, в архиве
  кнопка «Вернуть из архива».

Плагин включается на время блока (plugins_on): меню ядра сверяет test_crm_menu.
Тестовые «QA Autotest …» копятся в архиве. Форма не проверяет формат email и сайта,
API принимает пустое название — как у клиентов, решено не заводить.
"""
import random
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import (CRM, QA_PREFIX, crm_delete, crm_get, crm_me, crm_post, crm_put,
                         open_crm, plugins_on)

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/companies"
CONTACT_COUNT_BUG = "bord-16: contact_count считает связи всей организации, а не компании"


@pytest.fixture(scope="module", autouse=True)
def companies_plugin(browser, auth_state, browser_context_args):
    yield from plugins_on(browser, auth_state, browser_context_args, "companies")


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


def vat() -> str:
    """ИНН уникален и среди архивных — случайный на каждый тест."""
    return "".join(random.choices("0123456789", k=13))


def phone() -> str:
    return "+3736" + "".join(random.choices("0123456789", k=7))


def create_company(page: Page, **fields) -> dict:
    resp = crm_post(page, "/companies/", {"name": f"{QA_PREFIX} {tag()}", "vat_code": vat(), **fields})
    assert resp.status == 201, resp.text()
    return resp.json()


def create_contact(page: Page) -> dict:
    resp = crm_post(page, "/contacts/", {"first_name": f"{QA_PREFIX} {tag()}", "phone": phone()})
    assert resp.status == 201, resp.text()
    return resp.json()


def create_deal(page: Page, **fields) -> dict:
    pipelines = crm_get(page, "/pipeline-statuses/pipelines").json()["items"]
    data = {"title": f"{QA_PREFIX} {tag()}", "pipeline_id": next(p for p in pipelines if p["is_main"])["id"],
            "responsible_id": crm_me(page)["id"], **fields}
    resp = crm_post(page, "/deals/", data)
    assert resp.status == 201, resp.text()
    return resp.json()


def link(page: Page, company: dict, contact: dict, **fields):
    resp = crm_post(page, f"/companies/{company['id']}/contacts", {"contact_id": contact["id"], **fields})
    assert resp.status == 201, resp.text()


def clean_up(page: Page):
    """Отвязать контакты (иначе чужие счётчики растут, bord-16), компании — в архив."""
    resp = crm_get(page, "/companies/", {"search": QA_PREFIX, "limit": 100})
    for company in resp.json()["items"] if resp.ok else []:
        if not company["name"].startswith(QA_PREFIX):
            continue
        for c in crm_get(page, f"/companies/{company['id']}/contacts", {"limit": 200}).json()["items"]:
            crm_delete(page, f"/companies/{company['id']}/contacts/{c['contact_id']}")
        crm_delete(page, f"/companies/{company['id']}")
    deals = crm_get(page, "/deals/", {"search": QA_PREFIX, "limit": 100})
    for d in deals.json()["items"] if deals.ok else []:
        if d["title"].startswith(QA_PREFIX):
            crm_delete(page, f"/deals/{d['id']}")
    contacts = crm_get(page, "/contacts/", {"search": QA_PREFIX, "limit": 200})
    ids = [c["id"] for c in contacts.json()["items"] if c["first_name"].startswith(QA_PREFIX)] if contacts.ok else []
    if ids:
        crm_post(page, "/bulk/contacts/delete", {"contact_ids": ids})


@pytest.fixture
def companies(auth_page: Page):
    open_crm(auth_page, PAGE)
    expect(rows(auth_page).first).to_be_visible()
    yield auth_page
    clean_up(auth_page)


def rows(page: Page):
    return page.locator("main tbody tr")


def row(page: Page, name: str):
    return rows(page).filter(has_text=name)


def search(page: Page):
    return page.get_by_placeholder("Поиск по названию, ИНН, email, телефону...")


def form(page: Page):
    return page.locator("div.fixed.inset-0.items-center").filter(has_text="Новая компания")


def new_form(page: Page):
    page.get_by_role("button", name="Новая компания").click()
    expect(form(page)).to_be_visible()
    return form(page)


def name_field(scope):
    return scope.get_by_placeholder("ООО «Компания»")


def vat_field(scope):
    return scope.get_by_placeholder("1234567890")


def card(page: Page):
    return page.locator("div.fixed.inset-0.justify-end")


def open_card(page: Page, company: dict, tab: str | None = None):
    page.goto(f"{CRM}{PAGE}?id={company['id']}" + (f"&tab={tab}" if tab else ""))
    expect(card(page).get_by_role("button", name="Реквизиты", exact=True)).to_be_visible(timeout=20_000)
    return card(page)


def pick(page: Page, option: str):
    """Список рисуется отдельным плавающим блоком, не внутри кнопки-выпадашки."""
    page.locator("div.shadow-xl[style*='position: fixed']").last.get_by_text(option, exact=True).click()


def confirm(page: Page, question: str):
    return page.locator("div.fixed.inset-0").filter(has_text=question).last


def contact_row(scope, name: str):
    return scope.get_by_text(name, exact=True).locator("xpath=ancestor::div[.//button[@title='Отвязать']][1]")


# Кейс: Вкладка «Юрлица» — таблица и итог совпадают с API
def test_list_matches_api(companies: Page):
    api = crm_get(companies, "/companies/", {"is_active": "true", "page": 1, "limit": 25}).json()

    expect(companies.get_by_title("Клиенты", exact=True)).to_have_text(f"Клиенты{api['total']}")
    expect(rows(companies)).to_have_count(len(api["items"]))
    expect(companies.locator("main").get_by_text(f"1–{len(api['items'])} из {api['total']}")).to_be_visible()
    for c in api["items"]:
        line = row(companies, c["name"]).first
        expect(line.locator("td").nth(2)).to_have_text(c["vat_code"] or "—")
        expect(line.locator("td").nth(4)).to_have_text(str(c["deal_count"]))


# Кейс: «Создать» неактивна без названия, «Отмена» закрывает окно
def test_create_requires_name(companies: Page):
    window = new_form(companies)
    create = window.get_by_role("button", name="Создать", exact=True)

    expect(create).to_be_disabled()
    vat_field(window).fill(vat())
    expect(create).to_be_disabled()
    name_field(window).fill(f"{QA_PREFIX} {tag()}")
    expect(create).to_be_enabled()
    window.get_by_role("button", name="Отмена", exact=True).click()
    expect(window).to_have_count(0)


# Кейс: Новая компания через форму — все поля доходят до API, открывается карточка
def test_create_via_form(companies: Page):
    name, code, tel = f"{QA_PREFIX} {tag()}", vat(), phone()
    window = new_form(companies)
    name_field(window).fill(name)
    vat_field(window).fill(code)
    window.get_by_placeholder("Общество с ограниченной ответственностью…").fill(f"{name} SRL")
    window.get_by_placeholder("Строительство").fill("Логистика")
    window.get_by_placeholder("https://example.com").fill("https://example.com/qa")
    window.get_by_placeholder("+373 …").fill(tel)
    window.get_by_placeholder("office@example.com").fill("qa@example.com")
    window.get_by_placeholder("ул. …").fill("ул. Тестовая, 1")
    window.get_by_placeholder("Кишинёв").fill("Бельцы")
    window.get_by_placeholder("IBAN, банк, …").fill("MD00QA00000000000000")
    window.get_by_placeholder("Дополнительная информация…").fill("Заметка теста")

    with companies.expect_response(lambda r: r.url.endswith("/api/crm/companies/")
                                   and r.request.method == "POST") as resp:
        window.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    created = resp.value.json()

    expect(companies).to_have_url(f"{CRM}{PAGE}?id={created['id']}")
    expect(name_field(card(companies))).to_have_value(name)
    saved = crm_get(companies, f"/companies/{created['id']}").json()
    expected = {"name": name, "vat_code": code, "full_legal_name": f"{name} SRL", "industry": "Логистика",
                "website": "https://example.com/qa", "phone": tel, "email": "qa@example.com",
                "address": "ул. Тестовая, 1", "city": "Бельцы", "bank_details": "MD00QA00000000000000",
                "notes": "Заметка теста", "responsible_id": crm_me(companies)["id"], "is_active": True}
    assert {k: saved[k] for k in expected} == expected
    card(companies).locator("button:has(svg.lucide-x)").last.click()
    expect(row(companies, name)).to_have_count(1)


# Кейс: Занятый ИНН — форма не создаёт компанию и ведёт на существующую
def test_duplicate_vat_form(companies: Page):
    existing = create_company(companies)
    window = new_form(companies)
    name_field(window).fill(f"{QA_PREFIX} {tag()}")
    vat_field(window).fill(existing["vat_code"])

    with companies.expect_response(lambda r: r.url.endswith("/api/crm/companies/")
                                   and r.request.method == "POST") as resp:
        window.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 409, resp.value.text()

    expect(window).to_contain_text("Компания с таким ИНН/VAT уже существует")
    window.get_by_text(f"Открыть «{existing['name']}»").click()
    expect(companies).to_have_url(re.compile(rf"[?&]id={existing['id']}"))
    expect(name_field(card(companies))).to_have_value(existing["name"])


# Кейс: Занятый ИНН отбивается API, и архивной компанией тоже (409 vat_duplicate)
def test_duplicate_vat_api_includes_archived(companies: Page):
    existing = create_company(companies)
    assert crm_delete(companies, f"/companies/{existing['id']}").ok

    resp = crm_post(companies, "/companies/", {"name": f"{QA_PREFIX} {tag()}", "vat_code": existing["vat_code"]})

    assert resp.status == 409, f"дубль ИНН принят: {resp.status} {resp.text()[:200]}"
    detail = resp.json()["detail"]
    assert (detail["code"], detail["existing_company_id"], detail["existing_company_active"]) == \
        ("vat_duplicate", existing["id"], False), detail


# Кейс: Поиск по названию, ИНН, email и телефону
@pytest.mark.parametrize("by", ["название", "ИНН", "email", "телефон"])
def test_search(companies: Page, by):
    mark = tag()
    target = create_company(companies, name=f"{QA_PREFIX} Поиск{mark}", email=f"qa.{mark}@example.com",
                            phone=phone())
    other = create_company(companies)
    companies.reload()
    expect(row(companies, target["name"])).to_have_count(1)
    query = {"название": f"Поиск{mark}", "ИНН": target["vat_code"], "email": target["email"],
             "телефон": target["phone"]}[by]

    with companies.expect_response(lambda r: "/api/crm/companies/" in r.url and "search=" in r.url):
        search(companies).fill(query)

    expect(rows(companies)).to_have_count(1)
    expect(row(companies, target["name"])).to_be_visible()
    expect(row(companies, other["name"])).to_have_count(0)


# Кейс: Категория в карточке сохраняется и работает фильтром списка
def test_category_saved_and_filtered(companies: Page):
    target, other = create_company(companies), create_company(companies)
    panel = open_card(companies, target)

    panel.get_by_role("button", name="— Не выбрана —").click()
    pick(companies, "Партнёры")
    with companies.expect_response(lambda r: f"/api/crm/companies/{target['id']}" in r.url
                                   and r.request.method in ("PUT", "PATCH")) as resp:
        panel.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    category = crm_get(companies, f"/companies/{target['id']}").json()["category"]
    assert category, "категория не сохранилась"

    companies.goto(CRM + PAGE)
    expect(row(companies, target["name"])).to_have_count(1)
    with companies.expect_response(lambda r: "/api/crm/companies/" in r.url and f"category={category}" in r.url):
        companies.locator("main").get_by_role("button", name="Категория").click()
        pick(companies, "Партнёры")

    expect(row(companies, target["name"])).to_have_count(1)
    expect(row(companies, other["name"])).to_have_count(0)


# Кейс: Правка реквизитов в карточке сохраняется и переживает reload
def test_edit_requisites(companies: Page):
    c = create_company(companies)
    panel = open_card(companies, c)
    new_name = f"{QA_PREFIX} Правка {tag()}"

    name_field(panel).fill(new_name)
    panel.get_by_placeholder("Кишинёв").fill("Сороки")
    with companies.expect_response(lambda r: f"/api/crm/companies/{c['id']}" in r.url
                                   and r.request.method in ("PUT", "PATCH")) as resp:
        panel.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()

    saved = crm_get(companies, f"/companies/{c['id']}").json()
    assert (saved["name"], saved["city"], saved["vat_code"]) == (new_name, "Сороки", c["vat_code"])
    panel = open_card(companies, c)
    expect(name_field(panel)).to_have_value(new_name)
    expect(panel.get_by_placeholder("Кишинёв")).to_have_value("Сороки")


# Кейс: Вкладка карточки хранится в адресе и открывается по ссылке
def test_tab_deep_link(companies: Page):
    c = create_company(companies)

    panel = open_card(companies, c, "contacts")
    expect(panel.get_by_placeholder("Найти контакт (имя / телефон)…")).to_be_visible()
    panel.get_by_role("button", name="Комментарии", exact=True).click()
    expect(companies).to_have_url(f"{CRM}{PAGE}?id={c['id']}&tab=comments")
    expect(panel.get_by_placeholder("Комментарий по компании…")).to_be_visible()

    panel.locator("button:has(svg.lucide-x)").last.click()
    expect(panel).to_have_count(0)
    expect(companies).to_have_url(CRM + PAGE)


# Кейс: Новый контакт прямо из карточки компании привязывается к ней
def test_new_contact_from_card(companies: Page):
    c = create_company(companies)
    panel = open_card(companies, c, "contacts")
    name, tel = f"{QA_PREFIX} {tag()}", phone()

    panel.get_by_role("button", name="Новый", exact=True).click()
    companies.get_by_placeholder("Имя *").fill(name)
    companies.get_by_placeholder("+373 60 123 456").fill(tel)
    with companies.expect_response(lambda r: f"/api/crm/companies/{c['id']}/contacts" in r.url
                                   and r.request.method == "POST") as resp:
        companies.get_by_role("button", name="Создать и выбрать").click()
    assert resp.value.status == 201, resp.value.text()

    expect(contact_row(panel, name)).to_contain_text(tel)
    items = crm_get(companies, f"/companies/{c['id']}/contacts").json()["items"]
    assert [(i["first_name"], i["phone"]) for i in items] == [(name, tel)]


# Кейс: Контакт из базы — привязать, сделать основным, задать роль, отвязать
def test_link_existing_contact(companies: Page):
    c, person = create_company(companies), create_contact(companies)
    panel = open_card(companies, c, "contacts")

    panel.get_by_placeholder("Найти контакт (имя / телефон)…").press_sequentially(person["first_name"])
    with companies.expect_response(lambda r: f"/api/crm/companies/{c['id']}/contacts" in r.url
                                   and r.request.method == "POST") as resp:
        panel.get_by_role("button", name=re.compile(re.escape(person["first_name"]))).click()
    assert resp.value.status == 201, resp.value.text()
    line = contact_row(panel, person["first_name"])
    expect(line).to_contain_text("Роль не указана")

    line.locator("button:has(svg.lucide-star)").click()
    expect(line).to_contain_text("основной")
    line.get_by_text("Роль не указана").hover()
    line.locator("button:has(svg.lucide-pencil)").click()
    role_input = panel.get_by_placeholder("Роль / должность")
    role_input.fill("Директор")
    with companies.expect_response(lambda r: f"/contacts/{person['id']}" in r.url
                                   and r.request.method == "PUT") as resp:
        role_input.press("Enter")
    assert resp.value.ok, resp.value.text()
    expect(line).to_contain_text("Директор")
    item = crm_get(companies, f"/companies/{c['id']}/contacts").json()["items"][0]
    assert (item["role"], item["is_primary"]) == ("Директор", True)

    line.get_by_role("button", name="Отвязать").click()
    question = f"Отвязать «{person['first_name']}» от компании?"
    with companies.expect_response(lambda r: f"/contacts/{person['id']}" in r.url
                                   and r.request.method == "DELETE") as resp:
        confirm(companies, question).get_by_role("button", name="Продолжить").click()
    assert resp.value.ok, resp.value.text()
    expect(panel.get_by_text("Контактов пока нет")).to_be_visible()
    assert crm_get(companies, f"/companies/{c['id']}/contacts").json()["total"] == 0
    assert crm_get(companies, f"/contacts/{person['id']}").json()["is_active"] is True, "отвязка не должна архивировать клиента"


# Кейс: Основным контактом может быть только один
def test_only_one_primary_contact(companies: Page):
    c = create_company(companies)
    first, second = create_contact(companies), create_contact(companies)
    link(companies, c, first, is_primary=True)
    link(companies, c, second)
    panel = open_card(companies, c, "contacts")
    expect(contact_row(panel, first["first_name"])).to_contain_text("основной")

    contact_row(panel, second["first_name"]).locator("button:has(svg.lucide-star)").click()

    expect(contact_row(panel, second["first_name"])).to_contain_text("основной")
    expect(contact_row(panel, first["first_name"])).not_to_contain_text("основной")
    primary = [i["contact_id"] for i in crm_get(companies, f"/companies/{c['id']}/contacts").json()["items"]
               if i["is_primary"]]
    assert primary == [second["id"]]


# Кейс: Сделка компании — счётчик, сумма в списке, вкладка «Сделки» и переход
def test_company_deal(companies: Page):
    c = create_company(companies)
    deal = create_deal(companies, company_id=c["id"], amount=1500, currency="MDL")
    api = crm_get(companies, f"/companies/{c['id']}").json()
    assert (api["deal_count"], api["deal_amount_total"]) == (1, 1500)

    companies.reload()
    line = row(companies, c["name"])
    expect(line.locator("td").nth(4)).to_have_text("1")
    expect(line.locator("td").nth(5)).to_contain_text(re.compile(r"1\D500,00\sMDL"))

    panel = open_card(companies, c, "deals")
    item = panel.get_by_role("link", name=re.compile(re.escape(deal["title"])))
    expect(item).to_contain_text("Основная воронка")
    expect(item).to_have_attribute("href", f"/admin/crm/deals?id={deal['id']}")


# Кейс: «Создать задачу» из карточки — форма с компанией, задача видна на вкладке
def test_task_from_card(companies: Page):
    c = create_company(companies)
    panel = open_card(companies, c, "tasks")
    expect(panel.get_by_text("Нет связанных задач.")).to_be_visible()
    title = f"{QA_PREFIX} {tag()}"

    panel.get_by_role("link", name="Создать задачу").click()
    window = companies.locator("div.fixed.inset-0").filter(has_text="Новая задача").last
    expect(window).to_contain_text(c["name"])
    window.get_by_placeholder(re.compile("^Необязательно")).fill(title)
    with companies.expect_response(lambda r: r.url.endswith("/api/crm/tasks/")
                                   and r.request.method == "POST") as resp:
        window.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    task = resp.value.json()

    try:
        panel = open_card(companies, c, "tasks")
        expect(panel.get_by_text(title)).to_be_visible()
        api = crm_get(companies, "/tasks/", {"entity_type": "company", "entity_id": c["id"]}).json()
        assert [t["id"] for t in api["items"]] == [task["id"]]
    finally:
        crm_delete(companies, f"/tasks/{task['id']}/permanent")


# Кейс: Комментарий по компании появляется на вкладке и в API
def test_comment(companies: Page):
    c = create_company(companies)
    panel = open_card(companies, c, "comments")
    expect(panel.get_by_text("Комментариев нет")).to_be_visible()
    text = f"{QA_PREFIX} комментарий {tag()}"

    panel.get_by_placeholder("Комментарий по компании…").fill(text)
    with companies.expect_response(lambda r: r.url.endswith("/api/crm/activities/")
                                   and r.request.method == "POST") as resp:
        panel.get_by_role("button", name="Добавить", exact=True).click()
    assert resp.value.status == 201, resp.value.text()

    expect(panel.get_by_text(text)).to_be_visible()
    items = crm_get(companies, "/activities/", {"entity_type": "company", "entity_id": c["id"],
                                                "type": "comment", "limit": 200}).json()["items"]
    assert len(items) == 1


# Кейс: Компания в архив и обратно
def test_archive_and_restore(companies: Page):
    c = create_company(companies)
    panel = open_card(companies, c)

    panel.get_by_role("button", name="Архивировать").click()
    with companies.expect_response(lambda r: r.url.endswith(f"/api/crm/companies/{c['id']}")
                                   and r.request.method == "DELETE") as resp:
        confirm(companies, f"Архивировать компанию «{c['name']}»?").get_by_role("button", name="Продолжить").click()
    assert resp.value.ok, resp.value.text()
    expect(row(companies, c["name"])).to_have_count(0)
    assert crm_get(companies, f"/companies/{c['id']}").json()["is_active"] is False

    companies.locator("main").get_by_role("button", name="Архив", exact=True).click()
    archived = row(companies, c["name"])
    with companies.expect_response(lambda r: f"/api/crm/companies/{c['id']}" in r.url
                                   and r.request.method in ("PUT", "PATCH", "POST")) as resp:
        archived.get_by_role("button", name="Вернуть из архива").click()
        return_btn = companies.get_by_role("button", name="Продолжить")
        if return_btn.count():
            return_btn.click()
    assert resp.value.ok, resp.value.text()
    expect(archived).to_have_count(0)
    assert crm_get(companies, f"/companies/{c['id']}").json()["is_active"] is True

    companies.locator("main").get_by_role("button", name="Активные", exact=True).click()
    expect(row(companies, c["name"])).to_have_count(1)


# Кейс: «Отмена» в окне архива оставляет компанию
def test_archive_cancel(companies: Page):
    c = create_company(companies)
    panel = open_card(companies, c)

    panel.get_by_role("button", name="Архивировать").click()
    window = confirm(companies, f"Архивировать компанию «{c['name']}»?")
    window.get_by_role("button", name="Отмена").click()

    expect(companies.get_by_text(f"Архивировать компанию «{c['name']}»?")).to_have_count(0)
    assert crm_get(companies, f"/companies/{c['id']}").json()["is_active"] is True


# Кейс: Счётчик контактов считает только контакты самой компании
@pytest.mark.xfail(reason=CONTACT_COUNT_BUG, strict=False)
def test_contact_count_is_per_company(companies: Page):
    linked, untouched = create_company(companies), create_company(companies)
    link(companies, linked, create_contact(companies))

    listed = {c["id"]: c["contact_count"] for c in crm_get(companies, "/companies/", {"limit": 100}).json()["items"]}
    real = crm_get(companies, f"/companies/{untouched['id']}/contacts").json()["total"]

    assert real == 0
    assert listed[untouched["id"]] == 0, f"у компании без контактов счётчик {listed[untouched['id']]}"
    assert listed[linked["id"]] == 1
