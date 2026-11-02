"""Блок 36. CRM — бронирование (плагины «Бронирование» + «Syrve»).

Снято в MCP-браузере 30.10.2026:
- залы и столы приходят ТОЛЬКО синхронизацией из Syrve: ни в API, ни на экране
  завести зал нельзя (есть лишь PUT существующего), а бронь требует section_id. Ключей
  Syrve у BORD QA нет, поэтому календарь с бронями здесь не покрыть. Покрыто всё
  остальное: заведения, цены, чёрный список, пустые экраны;
- заведения: /admin/crm/venues (ссылка из подсказки на экране бронирования), «+
  Заведение» → окно, «Создать» неактивна без названия и slug, занятый slug — 409 и
  текст внизу формы. Строка раскрывается: блок Syrve и «Залов нет…»;
- без плагина Syrve экраны показывают сырой текст «Plugin 'syrve' is not enabled…»
  и ложное «Нет заведений» — bord-19;
- цены: /admin/crm/booking/prices, программы POST /reservations/venues/<id>/programs
  ({mode: per_person, per: <число>}), «1 MDL за каждого гостя»;
- чёрный список: POST / DELETE /reservations/blacklist/<contact_id>, кнопка «Убрать».

Плагины включаются на время блока.
"""
import random
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import (CRM, QA_PREFIX, crm_delete, crm_get, crm_post, crm_put, open_crm,
                         plugins_on, sidebar)

pytestmark = pytest.mark.requires_auth

VENUES = "/admin/crm/venues"
PLUGIN_ERROR = "bord-19: без Syrve сырой текст сервера и ложное «Нет заведений»"
SLUG_ERROR = "bord-19: занятый slug — ошибка по-английски"


@pytest.fixture(scope="module", autouse=True)
def booking_plugins(browser, auth_state, browser_context_args):
    yield from plugins_on(browser, auth_state, browser_context_args, "booking", "syrve")


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


def create_venue(page: Page, **fields) -> dict:
    mark = tag()
    resp = crm_post(page, "/venues/", {"name": f"{QA_PREFIX} {mark}", "slug": f"qa-{mark}",
                                       "timezone": "Europe/Chisinau", "currency": "MDL", **fields})
    assert resp.status == 201, resp.text()
    return resp.json()


def clean_up(page: Page):
    for v in crm_get(page, "/venues/").json()["items"]:
        if v["name"].startswith(QA_PREFIX):
            for p in crm_get(page, f"/reservations/venues/{v['id']}/programs").json()["items"]:
                crm_delete(page, f"/reservations/programs/{p['id']}")
            crm_delete(page, f"/venues/{v['id']}")
    for b in crm_get(page, "/reservations/blacklist").json()["items"]:
        if b["name"].startswith(QA_PREFIX):
            crm_delete(page, f"/reservations/blacklist/{b['contact_id']}")
    contacts = crm_get(page, "/contacts/", {"search": QA_PREFIX, "limit": 200})
    ids = [c["id"] for c in contacts.json()["items"] if c["first_name"].startswith(QA_PREFIX)] if contacts.ok else []
    if ids:
        crm_post(page, "/bulk/contacts/delete", {"contact_ids": ids})


@pytest.fixture
def booking(auth_page: Page):
    open_crm(auth_page, VENUES)
    yield auth_page
    clean_up(auth_page)


def venue_row(page: Page, name: str):
    return page.locator("main").get_by_text(name, exact=True)


def form(page: Page, title: str = "Новое заведение"):
    return page.locator("div.fixed.inset-0").filter(has_text=title).last


def new_form(page: Page):
    page.get_by_role("button", name="Заведение", exact=True).click()
    window = form(page)
    expect(window).to_be_visible()
    return window


def name_field(scope):
    return scope.get_by_placeholder("Например: «Терраса»")


def slug_field(scope):
    return scope.get_by_placeholder("terrasa")


def reload_venues(page: Page):
    page.goto(CRM + VENUES)
    expect(page.get_by_role("button", name="Заведение", exact=True)).to_be_visible(timeout=20_000)


# Кейс: Плагин включён — в меню есть «Бронирование» и «Цены»
def test_menu_items(booking: Page):
    menu = sidebar(booking)
    expect(menu.get_by_role("link", name="Бронирование", exact=True)).to_be_visible()
    expect(menu.get_by_role("link", name="Цены", exact=True)).to_be_visible()


# Кейс: Список заведений совпадает с API
def test_venues_match_api(booking: Page):
    venue = create_venue(booking)
    reload_venues(booking)

    api = crm_get(booking, "/venues/").json()["items"]

    expect(booking.get_by_title("Заведения", exact=True)).to_have_text(f"Заведения{len(api)}")
    for v in api:
        expect(venue_row(booking, v["name"])).to_be_visible()
    expect(booking.locator("main").get_by_text(f"/{venue['slug']}")).to_be_visible()


# Кейс: «Создать» неактивна без названия и адреса
def test_create_requires_name_and_slug(booking: Page):
    window = new_form(booking)
    create = window.get_by_role("button", name="Создать", exact=True)

    expect(create).to_be_disabled()
    name_field(window).fill(f"{QA_PREFIX} {tag()}")
    expect(create).to_be_disabled()
    slug_field(window).fill(f"qa-{tag()}")
    expect(create).to_be_enabled()
    window.get_by_role("button", name="Отмена", exact=True).click()
    expect(window).to_have_count(0)


# Кейс: Новое заведение через форму появляется в списке
def test_create_via_form(booking: Page):
    name, slug = f"{QA_PREFIX} {tag()}", f"qa-{tag()}"
    window = new_form(booking)
    name_field(window).fill(name)
    slug_field(window).fill(slug)

    with booking.expect_response(lambda r: r.url.endswith("/api/crm/venues/")
                                 and r.request.method == "POST") as resp:
        window.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()

    expect(venue_row(booking, name)).to_be_visible()
    saved = next(v for v in crm_get(booking, "/venues/").json()["items"] if v["slug"] == slug)
    assert (saved["name"], saved["timezone"], saved["currency"]) == (name, "Europe/Chisinau", "MDL")


# Кейс: Занятый адрес заведения отбивается, форма остаётся открытой
def test_duplicate_slug_refused(booking: Page):
    existing = create_venue(booking)
    window = new_form(booking)
    name_field(window).fill(f"{QA_PREFIX} {tag()}")
    slug_field(window).fill(existing["slug"])

    with booking.expect_response(lambda r: r.url.endswith("/api/crm/venues/")
                                 and r.request.method == "POST") as resp:
        window.get_by_role("button", name="Создать", exact=True).click()

    assert resp.value.status == 409, resp.value.text()
    expect(window).to_be_visible()
    assert len([v for v in crm_get(booking, "/venues/").json()["items"] if v["slug"] == existing["slug"]]) == 1


# Кейс: Ошибка занятого адреса написана по-русски
@pytest.mark.xfail(reason=SLUG_ERROR, strict=False)
def test_duplicate_slug_message_is_russian(booking: Page):
    existing = create_venue(booking)
    window = new_form(booking)
    name_field(window).fill(f"{QA_PREFIX} {tag()}")
    slug_field(window).fill(existing["slug"])
    with booking.expect_response(lambda r: r.url.endswith("/api/crm/venues/") and r.request.method == "POST"):
        window.get_by_role("button", name="Создать", exact=True).click()

    booking.wait_for_timeout(500)
    expect(window.get_by_text("A venue with this slug already exists")).to_have_count(0, timeout=1_000)


# Кейс: Правка заведения сохраняется
def test_edit_venue(booking: Page):
    venue = create_venue(booking)
    reload_venues(booking)
    new_name = f"{QA_PREFIX} Правка {tag()}"

    row = venue_row(booking, venue["name"]).locator("xpath=ancestor::div[.//button[@title='Редактировать']][1]")
    row.get_by_role("button", name="Редактировать").click()
    window = form(booking, "Сохранить")
    name_field(window).fill(new_name)
    with booking.expect_response(lambda r: r.url.endswith(f"/api/crm/venues/{venue['id']}")
                                 and r.request.method == "PUT") as resp:
        window.get_by_role("button", name=re.compile("Сохранить")).click()
    assert resp.value.ok, resp.value.text()

    expect(venue_row(booking, new_name)).to_be_visible()
    assert crm_get(booking, f"/venues/{venue['id']}").json()["name"] == new_name


# Кейс: Удаление заведения спрашивает подтверждение
def test_delete_venue(booking: Page):
    venue = create_venue(booking)
    reload_venues(booking)

    row = venue_row(booking, venue["name"]).locator("xpath=ancestor::div[.//button[@title='Удалить']][1]")
    row.get_by_role("button", name="Удалить").click()
    question = booking.locator("div.fixed.inset-0").filter(has_text="Отмена").last
    with booking.expect_response(lambda r: r.url.endswith(f"/api/crm/venues/{venue['id']}")
                                 and r.request.method == "DELETE") as resp:
        question.get_by_role("button", name=re.compile("Продолжить|Удалить")).last.click()
    assert resp.value.ok, resp.value.text()

    expect(venue_row(booking, venue["name"])).to_have_count(0)
    assert crm_get(booking, f"/venues/{venue['id']}").status == 404


# Кейс: Строка заведения раскрывается: Syrve не настроен, залов нет
def test_venue_details(booking: Page):
    venue = create_venue(booking)
    reload_venues(booking)

    venue_row(booking, venue["name"]).click()

    main = booking.locator("main")
    expect(main).to_contain_text("Syrve не настроен")
    expect(main).to_contain_text("Залов нет. Синхронизируйте из Syrve")


# Кейс: Заведение без залов — календарь объясняет, что делать
def test_calendar_without_halls(booking: Page):
    venue = create_venue(booking)

    booking.goto(f"{CRM}/admin/crm/booking/calendar?venue={venue['id']}")

    expect(booking.get_by_text("У заведения нет залов")).to_be_visible(timeout=20_000)


# Кейс: Разделы бронирования открываются без ошибок
@pytest.mark.parametrize("section, text", [
    ("list", None), ("history", None), ("trash", None), ("unlinked", None),
    ("blacklist", "В чёрном списке пока никого нет."),
])
def test_sections_open(booking: Page, section, text):
    errors = []
    booking.on("response", lambda r: errors.append(f"{r.status} {r.url}")
               if "/api/crm/" in r.url and r.status >= 500 else None)

    booking.goto(f"{CRM}/admin/crm/booking/{section}")
    expect(sidebar(booking).get_by_role("link", name="Сделки", exact=True)).to_be_visible(timeout=20_000)
    if text and not crm_get(booking, "/reservations/blacklist").json()["items"]:
        expect(booking.get_by_text(text)).to_be_visible()
    booking.wait_for_timeout(1_000)

    assert errors == []


# Кейс: Чёрный список: гость появляется в таблице, «Убрать» снимает его
def test_blacklist(booking: Page):
    person = crm_post(booking, "/contacts/", {"first_name": f"{QA_PREFIX} {tag()}",
                                              "phone": "+3736" + "".join(random.choices("0123456789", k=7))}).json()
    added = crm_post(booking, f"/reservations/blacklist/{person['id']}", {})
    assert added.ok, added.text()

    booking.goto(f"{CRM}/admin/crm/booking/blacklist")
    line = booking.locator("main tbody tr").filter(has_text=person["first_name"])
    expect(line).to_contain_text(person["phone"], timeout=20_000)
    expect(line).to_contain_text("внесён руками")

    line.get_by_role("button", name="Убрать").click()
    question = booking.locator("div.fixed.inset-0").filter(has_text="из чёрного списка?").last
    expect(question).to_contain_text("Отметки «не пришли» на его бронях останутся")
    with booking.expect_response(lambda r: f"/blacklist/{person['id']}" in r.url
                                 and r.request.method == "DELETE") as resp:
        question.get_by_role("button", name="Убрать").click()
    assert resp.value.ok, resp.value.text()
    expect(line).to_have_count(0)


# Кейс: Программа заведения видна в ценах; без цены не заводится
def test_program_in_prices(booking: Page):
    venue = create_venue(booking)
    refused = crm_post(booking, f"/reservations/venues/{venue['id']}/programs",
                       {"name": "QA", "pricing": {"mode": "per_person"}})
    assert refused.status == 400, refused.text()
    name = f"{QA_PREFIX} программа {tag()}"
    created = crm_post(booking, f"/reservations/venues/{venue['id']}/programs",
                       {"name": name, "pricing": {"mode": "per_person", "per": 250}})
    assert created.status == 201, created.text()

    booking.goto(f"{CRM}/admin/crm/booking/prices")
    main = booking.locator("main")
    expect(main.get_by_text(name)).to_be_visible(timeout=20_000)
    expect(main).to_contain_text("250 MDL за каждого гостя")


# Кейс: Без плагина Syrve экран объясняет причину по-русски и не пишет «Нет заведений»
@pytest.mark.xfail(reason=PLUGIN_ERROR, strict=False)
def test_booking_without_syrve(booking: Page):
    create_venue(booking)
    assert crm_put(booking, "/plugins/syrve", {"is_enabled": False}).ok
    try:
        booking.goto(f"{CRM}/admin/crm/booking/calendar")
        expect(sidebar(booking).get_by_role("link", name="Сделки", exact=True)).to_be_visible(timeout=20_000)
        booking.wait_for_timeout(2_000)
        main = booking.locator("main")
        expect(main.get_by_text("Plugin 'syrve' is not enabled")).to_have_count(0, timeout=1_000)
        expect(main.get_by_text("Нет заведений")).to_have_count(0, timeout=1_000)
    finally:
        crm_put(booking, "/plugins/syrve", {"is_enabled": True})
