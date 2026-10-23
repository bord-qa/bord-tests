"""Блок 7. Контрагенты: app.bord.one/admin/counterparties.

Снято 22.10.2026:
- список: GET /api/counterparties/?q=&limit=50&offset=0 (архив — &archived=true),
  справочники вида/ролей — /api/counterparties/meta; таблица «Название, IDNO,
  Город, Контакты, Роль, Модули»; пусто по поиску — «Ничего не нашлось…»;
- «Добавить контрагента» → окно «Новый контрагент» (div.fixed.inset-0, не
  role=dialog): Вид (Компания / Физическое лицо), Название, Юр. название, IDNO,
  Код НДС, Страна, Город, Адрес, Почта, Телефон, Заметки → POST
  /api/counterparties/ → 201, URL ?id=<id>, окно карточки с «Реквизитами» (helpers/modal.py);
- уникальность по IDNO: при вводе POST …/resolve находит карточку и форма
  пишет «IDNO … уже у карточки «…» (№ …)», сохранение → 409;
- «Изменить» → PATCH /api/counterparties/<id>; «В архив» → окно «Убрать «…»
  в архив? …» (Отмена / Да) → POST …/<id>/archive; в архиве строка с пометкой
  «в архиве» и кнопка «Вернуть из архива» → POST …/<id>/unarchive.

Удаления контрагентов в API нет — свои карточки «QA Autotest …» тесты
убирают в архив. IDNO у каждой карточки свой (из времени), чтобы архивные
карточки прошлых прогонов не мешали.
"""
import re
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_get, api_post
from helpers.modal import close_modal, modal
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

API = "/api/counterparties/"
NAME_PREFIX = "QA Autotest"


def unique() -> str:
    """13 цифр — как настоящий IDNO, и не повторяется между прогонами."""
    return str(int(time.time() * 1000))[-13:].rjust(13, "1")


def create_card(page: Page, **fields) -> dict:
    stamp = unique()
    data = {"kind": "company", "name": f"{NAME_PREFIX} {stamp}", "idno": stamp, **fields}
    resp = api_post(page, API, data)
    assert resp.status == 201, resp.text()
    return resp.json()


def archive_qa_cards(page: Page):
    resp = api_get(page, f"{API}?q={NAME_PREFIX}&limit=50&offset=0")
    for card in resp.json()["items"] if resp.ok else []:
        if card["name"].startswith(NAME_PREFIX) and not card["archived"]:
            api_post(page, f"{API}{card['id']}/archive", {})


@pytest.fixture
def cp(auth_page: Page):
    auth_page.goto("/admin/counterparties")
    expect(auth_page.locator("main").get_by_role("button", name="Добавить контрагента").first).to_be_visible()
    settle(auth_page)
    yield auth_page
    archive_qa_cards(auth_page)


def search(page: Page):
    return page.locator("main").get_by_placeholder("Название, юр. название или IDNO")


def rows(page: Page):
    return page.locator("main table tbody tr")


def form_field(form, label: str):
    return form.get_by_text(label, exact=True).locator("xpath=following-sibling::input[1]")


def requisite(card, term: str):
    return card.locator("dt", has_text=re.compile(rf"^{term}$")).locator("xpath=following-sibling::dd[1]")


def find(page: Page, text: str):
    """Отфильтровать список и дождаться, пока применится поиск."""
    with page.expect_response(lambda r: r.url.split("?")[0].endswith(API) and "q=" in r.url):
        search(page).fill(text)


@pytest.mark.writes_data
def test_company_create_shows_card_and_row(cp: Page):
    stamp = unique()
    values = {
        "Юридическое название": f"{NAME_PREFIX} {stamp} SRL",
        "IDNO": stamp,
        "Код НДС": f"MD{stamp[-7:]}",
        "Страна": "Молдова",
        "Город": "Кишинёв",
        "Адрес": "str. QA 1",
        "Почта": f"qa-{stamp}@example.com",
        "Телефон": "+37369000001",
    }
    name = f"{NAME_PREFIX} {stamp}"
    cp.locator("main").get_by_role("button", name="Добавить контрагента").first.click()
    form = modal(cp, "Новый контрагент")
    form.get_by_placeholder("Альфа SRL").fill(name)
    for label, value in values.items():
        form_field(form, label).fill(value)

    with cp.expect_response(lambda r: r.url.endswith(API) and r.request.method == "POST") as created:
        form.get_by_role("button", name="Сохранить").click()

    assert created.value.status == 201, created.value.text()
    card_data = created.value.json()
    assert card_data["kind"] == "company" and card_data["idno"] == stamp and card_data["city"] == "Кишинёв"
    expect(cp).to_have_url(re.compile(rf"\?id={card_data['id']}$"))
    card = modal(cp, name)
    expect(card).to_contain_text("Компания")
    for label, value in values.items():
        expect(requisite(card, label), label).to_have_text(value)
    expect(card).to_contain_text(f"№ {card_data['id']}")

    row = rows(cp).filter(has_text=name)
    expect(row).to_have_count(1)
    expect(row).to_contain_text(stamp)
    expect(row).to_contain_text("Кишинёв, Молдова")
    expect(row).to_contain_text(f"qa-{stamp}@example.com · +37369000001")


@pytest.mark.writes_data
def test_person_create_and_kind_filter(cp: Page):
    company = create_card(cp)
    name = f"{NAME_PREFIX} Ион {unique()}"
    cp.locator("main").get_by_role("button", name="Добавить контрагента").first.click()
    form = modal(cp, "Новый контрагент")
    form.get_by_role("button", name="Компания").click()
    cp.get_by_role("option", name="Физическое лицо").click()
    form.get_by_placeholder("Ион Пример").fill(name)
    with cp.expect_response(lambda r: r.url.endswith(API) and r.request.method == "POST") as created:
        form.get_by_role("button", name="Сохранить").click()

    assert created.value.status == 201 and created.value.json()["kind"] == "person"
    expect(modal(cp, name)).to_contain_text("Физическое лицо")
    close_modal(cp, name)
    find(cp, NAME_PREFIX)

    cp.locator("main").get_by_role("button", name="Все виды").click()
    cp.get_by_role("option", name="Физическое лицо").click()
    expect(rows(cp).filter(has_text=name)).to_have_count(1)
    expect(rows(cp).filter(has_text=company["name"])).to_have_count(0)

    cp.locator("main").get_by_role("button", name="Физическое лицо").click()
    cp.get_by_role("option", name="Компания").click()
    expect(rows(cp).filter(has_text=company["name"])).to_have_count(1)
    expect(rows(cp).filter(has_text=name)).to_have_count(0)


@pytest.mark.writes_data
def test_duplicate_idno_blocked(cp: Page):
    existing = create_card(cp)
    cp.locator("main").get_by_role("button", name="Добавить контрагента").first.click()
    form = modal(cp, "Новый контрагент")
    form.get_by_placeholder("Альфа SRL").fill(f"{NAME_PREFIX} дубль {unique()}")

    with cp.expect_response(lambda r: r.url.endswith(f"{API}resolve")) as resolved:
        form_field(form, "IDNO").fill(existing["idno"])
        form_field(form, "IDNO").blur()
    assert resolved.value.json()["match"]["id"] == existing["id"]
    hint = f"IDNO {existing['idno']} уже у карточки «{existing['name']}» (№ {existing['id']})"
    expect(form.get_by_text(hint).first).to_be_visible()
    expect(form.get_by_role("button", name="Открыть её")).to_be_visible()

    with cp.expect_response(lambda r: r.url.endswith(API) and r.request.method == "POST") as created:
        form.get_by_role("button", name="Сохранить").click()

    assert created.value.status == 409
    expect(form).to_be_visible()
    same_idno = api_get(cp, f"{API}?q={existing['idno']}&limit=50&offset=0").json()["items"]
    assert [c["id"] for c in same_idno] == [existing["id"]]


@pytest.mark.writes_data
def test_search_by_name_and_idno(cp: Page):
    card = create_card(cp, legal_name="QA Search Legal SRL")
    other = create_card(cp)

    for query in (card["idno"], card["name"], "QA Search Legal"):
        find(cp, query)
        expect(rows(cp).filter(has_text=card["name"]), query).to_have_count(1)
        expect(rows(cp).filter(has_text=other["name"]), query).to_have_count(0)

    find(cp, f"нет-такого-{unique()}")
    expect(rows(cp)).to_have_count(0)
    expect(cp.locator("main").get_by_text("Ничего не нашлось. Попробуйте IDNO или другое написание названия.")).to_be_visible()


@pytest.mark.writes_data
def test_edit_card(cp: Page):
    card = create_card(cp)
    new_name = f"{card['name']} изм."
    find(cp, card["idno"])
    rows(cp).filter(has_text=card["name"]).click()
    view = modal(cp, card["name"])
    view.get_by_role("button", name="Изменить").click()

    form = modal(cp, f"Изменить: {card['name']}")
    form.get_by_placeholder("Альфа SRL").fill(new_name)
    form_field(form, "Город").fill("Бельцы")
    with cp.expect_response(lambda r: r.url.endswith(f"{API}{card['id']}") and r.request.method == "PATCH") as patched:
        form.get_by_role("button", name="Сохранить").click()

    assert patched.value.ok, patched.value.text()
    expect(requisite(modal(cp, new_name), "Город")).to_have_text("Бельцы")
    saved = api_get(cp, f"{API}{card['id']}").json()
    assert saved["name"] == new_name and saved["city"] == "Бельцы" and saved["idno"] == card["idno"]


@pytest.mark.writes_data
def test_archive_and_restore(cp: Page):
    card = create_card(cp)
    main = cp.locator("main")
    find(cp, card["idno"])
    rows(cp).filter(has_text=card["name"]).click()

    modal(cp, card["name"]).get_by_role("button", name="В архив").click()
    dialog = cp.get_by_role("dialog", name=re.compile(rf"Убрать «{re.escape(card['name'])}» в архив\?"))
    with cp.expect_response(lambda r: r.url.endswith(f"{card['id']}/archive")) as archived:
        dialog.get_by_role("button", name="Да").click()

    assert archived.value.ok
    assert api_get(cp, f"{API}{card['id']}").json()["archived"] is True
    close_modal(cp, card["name"])
    expect(rows(cp).filter(has_text=card["name"])).to_have_count(0)

    main.get_by_role("button", name="Архив", exact=True).click()
    row = rows(cp).filter(has_text=card["name"])
    expect(row).to_contain_text("в архиве")
    row.click()
    with cp.expect_response(lambda r: r.url.endswith(f"{card['id']}/unarchive")) as restored:
        modal(cp, card["name"]).get_by_role("button", name="Вернуть из архива").click()

    assert restored.value.ok
    assert api_get(cp, f"{API}{card['id']}").json()["archived"] is False
    close_modal(cp, card["name"])
    main.get_by_role("button", name="Действующие").click()
    expect(rows(cp).filter(has_text=card["name"])).to_have_count(1)


@pytest.mark.writes_data
def test_archive_can_be_cancelled(cp: Page):
    card = create_card(cp)
    find(cp, card["idno"])
    rows(cp).filter(has_text=card["name"]).click()

    modal(cp, card["name"]).get_by_role("button", name="В архив").click()
    cp.get_by_role("dialog", name=re.compile("в архив\\?")).get_by_role("button", name="Отмена").click()

    expect(modal(cp, card["name"]).get_by_role("button", name="В архив")).to_be_visible()
    assert api_get(cp, f"{API}{card['id']}").json()["archived"] is False
