"""Блок 21. CRM — клиенты: crm.bord.one/admin/crm/contacts.

Снято 27.10.2026:
- список: GET /api/crm/contacts/?offset=0&limit=50 → {items, total, page,
  limit, stats}; архив — &is_active=false; поиск по имени, email и телефону
  уходит в ?search=; в шапке «Клиенты <total>», внизу «Показано N из M»;
  таблица «Клиент, Телефон, Статус, Язык, LTV, Последний», у строки кнопки
  «Редактировать» / «В архив» (в архиве — «Вернуть из архива»);
- «Новый клиент» → окно (не role=dialog) с полями Имя* («Иван»), Фамилия
  («Иванов»), Email, Телефон («+373 60 123 456»), Ответственный; остальное под
  «Ещё поля: компания, источник, адрес, теги…». «Создать» неактивна без имени.
  POST /api/crm/contacts/ → 201, окно закрывается, строка в списке. Карточку
  ответственного без выбора оставляют ничьей (responsible_id null);
- занятый телефон: при вводе форма ищет ?search=<номер>&limit=3 и пишет «Этот
  номер уже у: <имя>» — имя кнопкой «Открыть карточку вместо новой»; API на
  такой номер отвечает 409 {code: phone_taken, existing_id, …};
- карточка «360°» открывается кликом по строке, id в адресе (?id=<id>), так
  её можно переслать; «Закрыть» убирает id. Поля правятся по клику на
  значение («не заполнено»), Enter → PUT /api/crm/contacts/<id> {поле};
- «В архив» → «Убрать в архив «<имя>»? Сделки и история останутся…»
  (Отмена / В архив) → POST /api/crm/bulk/contacts/delete {contact_ids};
  возврат — POST /api/crm/bulk/contacts/restore.

Удалить клиента нельзя — свои карточки «QA Autotest …» тесты убирают в архив,
там они и копятся. Стартовые примеры («(пример)») не трогаем: их «Убрать
примеры» удаляет насовсем.
"""
import random

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import CRM, QA_PREFIX, crm_get, crm_post, crm_ready, open_crm

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/contacts"


def phone() -> str:
    """Молдавский мобильный, которого точно нет в базе (архив тоже держит номер)."""
    return "+3736" + "".join(random.choices("0123456789", k=7))


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


def create_contact(page: Page, **fields) -> dict:
    data = {"first_name": f"{QA_PREFIX} {tag()}", "phone": phone(), **fields}
    resp = crm_post(page, "/contacts/", data)
    assert resp.status == 201, resp.text()
    return resp.json()


def full_name(c: dict) -> str:
    return " ".join(p for p in (c["first_name"], c.get("last_name")) if p)


def archive_qa_contacts(page: Page):
    resp = crm_get(page, "/contacts/", {"search": QA_PREFIX, "limit": 200})
    ids = [c["id"] for c in resp.json()["items"]
           if c["first_name"].startswith(QA_PREFIX)] if resp.ok else []
    if ids:
        crm_post(page, "/bulk/contacts/delete", {"contact_ids": ids})


@pytest.fixture
def contacts(auth_page: Page):
    open_crm(auth_page, PAGE)
    yield auth_page
    archive_qa_contacts(auth_page)


def rows(page: Page):
    return page.locator("main tbody tr")


def row(page: Page, name: str):
    return rows(page).filter(has_text=name)


def search(page: Page):
    return page.get_by_placeholder("Имя, email, телефон…")


def new_form(page: Page):
    page.get_by_role("button", name="Новый клиент").click()
    expect(page.get_by_role("heading", name="Новый клиент")).to_be_visible()
    return page


def card_heading(page: Page, name: str):
    return page.get_by_role("heading", name=name, level=2)


def field_value(page: Page, label: str):
    """Значение поля в карточке «360°» — кнопка рядом с подписью.

    У обязательных полей в подписи ещё «*» и ссылка «Открыть», поэтому
    подпись ищем по её собственному текстовому узлу, а не по всему тексту.
    """
    return page.locator(f"xpath=//div[text()[normalize-space()='{label}']]/following-sibling::button[1]")


def archive_confirm(page: Page, name: str):
    return page.get_by_text(f"Убрать в архив «{name}»?").locator(
        "xpath=ancestor::div[.//button[normalize-space()='Отмена']][1]")


# Кейс: Список клиентов совпадает с API
def test_list_matches_api(contacts: Page):
    api = crm_get(contacts, "/contacts/", {"offset": 0, "limit": 50}).json()

    expect(contacts.get_by_title("Клиенты", exact=True)).to_have_text(f"Клиенты{api['total']}")
    expect(rows(contacts)).to_have_count(len(api["items"]))
    for c in api["items"]:
        expect(row(contacts, full_name(c)).first).to_be_visible()
    expect(contacts.get_by_text(f"Показано {len(api['items'])} из {api['total']}")).to_be_visible()


# Кейс: «Создать» неактивна без имени
def test_create_requires_name(contacts: Page):
    new_form(contacts)
    create = contacts.get_by_role("button", name="Создать", exact=True)

    expect(create).to_be_disabled()
    contacts.get_by_placeholder("+373 60 123 456").fill(phone())
    expect(create).to_be_disabled()
    contacts.get_by_placeholder("Иван", exact=True).fill(f"{QA_PREFIX} {tag()}")
    expect(create).to_be_enabled()
    contacts.get_by_role("button", name="Отмена", exact=True).click()
    expect(contacts.get_by_role("heading", name="Новый клиент")).to_have_count(0)


# Кейс: Новый клиент через форму — все короткие поля доходят до API и списка
def test_create_contact_via_form(contacts: Page):
    first, last, tel = f"{QA_PREFIX} {tag()}", "Тестовый", phone()
    email = f"qa.{tag()}@example.com"
    new_form(contacts)
    contacts.get_by_placeholder("Иван", exact=True).fill(first)
    contacts.get_by_placeholder("Иванов", exact=True).fill(last)
    contacts.get_by_placeholder("ivan@company.com").fill(email)
    contacts.get_by_placeholder("+373 60 123 456").fill(tel)

    with contacts.expect_response(lambda r: r.url.endswith("/api/crm/contacts/")
                                  and r.request.method == "POST") as resp:
        contacts.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    created = resp.value.json()

    expect(contacts.get_by_role("heading", name="Новый клиент")).to_have_count(0)
    line = row(contacts, f"{first} {last}")
    expect(line).to_have_count(1)
    expect(line).to_contain_text(tel)
    saved = crm_get(contacts, f"/contacts/{created['id']}").json()
    assert (saved["first_name"], saved["last_name"], saved["email"], saved["phone"]) == (first, last, email, tel)
    assert saved["responsible_id"] is None, "ответственного не выбирали — карточка должна остаться ничьей"


# Кейс: Занятый телефон — подсказка «Этот номер уже у» открывает ту карточку
def test_duplicate_phone_hint_opens_existing(contacts: Page):
    existing = create_contact(contacts)
    new_form(contacts)
    contacts.get_by_placeholder("+373 60 123 456").fill(existing["phone"])

    hint = contacts.get_by_text("Этот номер уже у:")
    expect(hint).to_be_visible()
    hint.get_by_role("button", name=existing["first_name"]).click()

    expect(card_heading(contacts, existing["first_name"])).to_be_visible()
    expect(contacts).to_have_url(f"{CRM}{PAGE}?id={existing['id']}")
    expect(contacts.get_by_role("heading", name="Новый клиент")).to_have_count(0)


# Кейс: API не заводит второго клиента с тем же телефоном (409 phone_taken)
def test_duplicate_phone_rejected_by_api(contacts: Page):
    existing = create_contact(contacts)

    resp = crm_post(contacts, "/contacts/", {"first_name": f"{QA_PREFIX} {tag()}", "phone": existing["phone"]})

    assert resp.status == 409, f"дубль телефона принят: {resp.status} {resp.text()[:200]}"
    detail = resp.json()["detail"]
    assert detail["code"] == "phone_taken" and detail["existing_id"] == existing["id"], detail


# Кейс: Поиск по имени, email и телефону
@pytest.mark.parametrize("by", ["имя", "email", "телефон"])
def test_search(contacts: Page, by):
    mark = tag()
    target = create_contact(contacts, last_name=f"Поиск{mark}", email=f"qa.{mark}@example.com")
    other = create_contact(contacts)
    contacts.reload()
    crm_ready(contacts)
    query = {"имя": f"Поиск{mark}", "email": f"qa.{mark}@example.com", "телефон": target["phone"]}[by]

    with contacts.expect_response(lambda r: "/api/crm/contacts/?search=" in r.url):
        search(contacts).fill(query)

    expect(rows(contacts)).to_have_count(1)
    expect(row(contacts, full_name(target))).to_be_visible()
    expect(row(contacts, other["first_name"])).to_have_count(0)


# Кейс: Карточка клиента по ссылке ?id= и закрытие убирает id из адреса
def test_card_deep_link(contacts: Page):
    c = create_contact(contacts, city="Кишинёв")

    contacts.goto(f"{CRM}{PAGE}?id={c['id']}")
    expect(card_heading(contacts, c["first_name"])).to_be_visible(timeout=20_000)
    expect(field_value(contacts, "Город")).to_have_text("Кишинёв")
    expect(field_value(contacts, "Телефон")).to_have_text(c["phone"])

    contacts.get_by_role("button", name="Закрыть", exact=True).click()
    expect(card_heading(contacts, c["first_name"])).to_have_count(0)
    expect(contacts).to_have_url(CRM + PAGE)


# Кейс: Правка поля в карточке по клику сохраняется и переживает reload
def test_inline_edit_field(contacts: Page):
    c = create_contact(contacts)
    contacts.goto(f"{CRM}{PAGE}?id={c['id']}")
    expect(card_heading(contacts, c["first_name"])).to_be_visible(timeout=20_000)
    value = field_value(contacts, "Город")
    expect(value).to_have_text("не заполнено")

    value.click()
    contacts.keyboard.type("Бельцы")
    with contacts.expect_response(lambda r: r.url.endswith(f"/api/crm/contacts/{c['id']}")
                                  and r.request.method == "PUT") as resp:
        contacts.keyboard.press("Enter")
    assert resp.value.ok, resp.value.text()

    expect(value).to_have_text("Бельцы")
    assert crm_get(contacts, f"/contacts/{c['id']}").json()["city"] == "Бельцы"
    contacts.reload()
    expect(field_value(contacts, "Город")).to_have_text("Бельцы", timeout=20_000)


# Кейс: Клиент в архив и обратно
def test_archive_and_restore(contacts: Page):
    c = create_contact(contacts)
    contacts.reload()
    crm_ready(contacts)
    line = row(contacts, c["first_name"])

    line.get_by_role("button", name="В архив").click()
    confirm = archive_confirm(contacts, c["first_name"])
    expect(confirm).to_contain_text("Сделки и история останутся")
    with contacts.expect_response(lambda r: "/bulk/contacts/delete" in r.url) as resp:
        confirm.get_by_role("button", name="В архив").click()
    assert resp.value.ok, resp.value.text()
    expect(line).to_have_count(0)
    assert crm_get(contacts, f"/contacts/{c['id']}").json()["is_active"] is False

    contacts.get_by_role("button", name="Архив", exact=True).click()
    archived = row(contacts, c["first_name"])
    with contacts.expect_response(lambda r: "/bulk/contacts/restore" in r.url) as resp:
        archived.get_by_role("button", name="Вернуть из архива").click()
    assert resp.value.ok, resp.value.text()
    expect(archived).to_have_count(0)
    assert crm_get(contacts, f"/contacts/{c['id']}").json()["is_active"] is True

    contacts.get_by_role("button", name="Активные", exact=True).click()
    expect(row(contacts, c["first_name"])).to_have_count(1)


# Кейс: «Отмена» в окне архива оставляет клиента
def test_archive_cancel(contacts: Page):
    c = create_contact(contacts)
    contacts.reload()
    crm_ready(contacts)

    row(contacts, c["first_name"]).get_by_role("button", name="В архив").click()
    archive_confirm(contacts, c["first_name"]).get_by_role("button", name="Отмена").click()

    expect(contacts.get_by_text(f"Убрать в архив «{c['first_name']}»?")).to_have_count(0)
    expect(row(contacts, c["first_name"])).to_have_count(1)
    assert crm_get(contacts, f"/contacts/{c['id']}").json()["is_active"] is True
