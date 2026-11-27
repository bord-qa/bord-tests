"""Блок 7. Контрагенты: app.bord.one/admin/counterparties.

Снято 22.10.2026:
- список: GET /api/counterparties/?q=&limit=50&offset=0 (архив — &archived=true),
  справочники вида/ролей — /api/counterparties/meta; таблица «Название, IDNO,
  Город, Контакты, Роль, Модули»; пусто по поиску — «Ничего не нашлось…»;
- «Добавить контрагента» → окно «Новый контрагент» (div.fixed.inset-0, не
  role=dialog): Вид (с 26.11 «Юр. лицо / Физ. лицо», было «Компания / Физическое
  лицо»; та же плашка в карточке и фильтр «Все виды» списка), Название, Юр. название,
  IDNO (у физлица — «IDNP/IDNO»), Код НДС, Страна, Город, Адрес, Директор, Почта,
  Телефон, Заметки → POST
  /api/counterparties/ → 201, URL ?id=<id>, окно карточки с «Реквизитами» (helpers/modal.py);
- уникальность по IDNO: при вводе POST …/resolve находит карточку и форма
  пишет «IDNO … уже у карточки «…» (№ …)», сохранение → 409;
- «Изменить» → PATCH /api/counterparties/<id>; «В архив» → окно «Убрать «…»
  в архив? …» (Отмена / Да) → POST …/<id>/archive; в архиве строка с пометкой
  «в архиве» и кнопка «Вернуть из архива» → POST …/<id>/unarchive.

Банковские счета (снято 27.10.2026) — блок в карточке: «Добавить счёт» →
строчная форма (IBAN или номер счёта, Банк, Код банка (BIC), Валюта) → POST
…/<id>/bank-accounts → 201; первый счёт сам становится основным («Первый счёт
станет основным.»), у следующих — галочка «Сделать основным». Кнопки строки —
иконки с title: «Сделать основным» / «Изменить» → PATCH …/bank-accounts/<id>,
«Удалить» → окно «Удалить счёт <IBAN>?» → DELETE. Удалили основной — основным
становится оставшийся. IBAN проверяется по контрольной сумме (400 «IBAN не
сходится по контрольной сумме — проверьте цифры»), пробелы и регистр
снимаются, повтор на той же карточке → 409 «Счёт … у этой карточки уже есть»;
номер без буквы страны (обычный счёт) принимается как есть.

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
from helpers.ui import screen_button, screen_field

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
    expect(screen_button(auth_page, "Добавить контрагента").first).to_be_visible()
    settle(auth_page)
    yield auth_page
    archive_qa_cards(auth_page)


def search(page: Page):
    return screen_field(page, "Название, юр. название или IDNO")


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


# Кейс: Новая компания-контрагент: карточка и строка в списке
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
    screen_button(cp, "Добавить контрагента").first.click()
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
    expect(card).to_contain_text("Юр. лицо")
    for label, value in values.items():
        expect(requisite(card, label), label).to_have_text(value)
    expect(card).to_contain_text(f"№ {card_data['id']}")

    # список постраничный (по 50, по имени): в BORD QA контрагентов больше 50, и
    # «QA Autotest …» оказывается не на первой странице — строку ищем поиском
    close_modal(cp, name)
    find(cp, stamp)
    row = rows(cp).filter(has_text=name)
    expect(row).to_have_count(1)
    expect(row).to_contain_text(stamp)
    expect(row).to_contain_text("Кишинёв, Молдова")
    expect(row).to_contain_text(f"qa-{stamp}@example.com · +37369000001")


# Кейс: Новое физлицо и фильтр по типу
@pytest.mark.writes_data
def test_person_create_and_kind_filter(cp: Page):
    company = create_card(cp)
    name = f"{NAME_PREFIX} Ион {unique()}"
    screen_button(cp, "Добавить контрагента").first.click()
    form = modal(cp, "Новый контрагент")
    form.get_by_role("button", name="Юр. лицо").click()
    cp.get_by_role("option", name="Физ. лицо").click()
    form.get_by_placeholder("Ион Пример").fill(name)
    with cp.expect_response(lambda r: r.url.endswith(API) and r.request.method == "POST") as created:
        form.get_by_role("button", name="Сохранить").click()

    assert created.value.status == 201 and created.value.json()["kind"] == "person"
    expect(modal(cp, name)).to_contain_text("Физ. лицо")
    close_modal(cp, name)
    find(cp, NAME_PREFIX)

    screen_button(cp, "Все виды").click()
    cp.get_by_role("option", name="Физ. лицо").click()
    expect(rows(cp).filter(has_text=name)).to_have_count(1)
    expect(rows(cp).filter(has_text=company["name"])).to_have_count(0)

    screen_button(cp, "Физ. лицо").click()
    cp.get_by_role("option", name="Юр. лицо").click()
    expect(rows(cp).filter(has_text=company["name"])).to_have_count(1)
    expect(rows(cp).filter(has_text=name)).to_have_count(0)


# Кейс: Повторный IDNO не принимается
@pytest.mark.writes_data
def test_duplicate_idno_blocked(cp: Page):
    existing = create_card(cp)
    screen_button(cp, "Добавить контрагента").first.click()
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


# Кейс: Поиск по названию и IDNO
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


# Кейс: Изменение карточки контрагента
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


# Кейс: В архив и обратно
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

    screen_button(cp, "Архив", exact=True).click()
    row = rows(cp).filter(has_text=card["name"])
    expect(row).to_contain_text("в архиве")
    row.click()
    with cp.expect_response(lambda r: r.url.endswith(f"{card['id']}/unarchive")) as restored:
        modal(cp, card["name"]).get_by_role("button", name="Вернуть из архива").click()

    assert restored.value.ok
    assert api_get(cp, f"{API}{card['id']}").json()["archived"] is False
    close_modal(cp, card["name"])
    screen_button(cp, "Действующие").click()
    expect(rows(cp).filter(has_text=card["name"])).to_have_count(1)


# Кейс: Отправку в архив можно отменить
@pytest.mark.writes_data
def test_archive_can_be_cancelled(cp: Page):
    card = create_card(cp)
    find(cp, card["idno"])
    rows(cp).filter(has_text=card["name"]).click()

    modal(cp, card["name"]).get_by_role("button", name="В архив").click()
    cp.get_by_role("dialog", name=re.compile("в архив\\?")).get_by_role("button", name="Отмена").click()

    expect(modal(cp, card["name"]).get_by_role("button", name="В архив")).to_be_visible()
    assert api_get(cp, f"{API}{card['id']}").json()["archived"] is False


# --- Банковские счета -------------------------------------------------------

IBAN_MDL = "MD24AG000225100013104168"
IBAN_EUR = "MD37VI000000222400100EUR"


def open_card(page: Page, card: dict):
    page.goto(f"/admin/counterparties?id={card['id']}")
    view = modal(page, card["name"])
    expect(view.get_by_role("heading", name="Банковские счета")).to_be_visible()
    settle(page)
    return view


def accounts(view):
    return view.locator("li")


def account(view, iban: str):
    return accounts(view).filter(has_text=iban)


def saved_accounts(page: Page, card: dict) -> dict:
    return {a["iban"]: a for a in api_get(page, f"{API}{card['id']}").json()["bank_accounts"]}


def add_account(page: Page, card: dict, iban: str, **fields):
    data = {"iban": iban, "bank_name": "", "bank_code": "", "currency": "", "is_main": False, **fields}
    resp = api_post(page, f"{API}{card['id']}/bank-accounts", data)
    assert resp.status == 201, resp.text()


def is_account_request(method: str, card: dict):
    return lambda r: f"{API}{card['id']}/bank-accounts" in r.url and r.request.method == method


# Кейс: Первый банковский счёт: добавление, становится основным
@pytest.mark.writes_data
def test_bank_account_first_becomes_main(cp: Page):
    card = create_card(cp)
    view = open_card(cp, card)
    expect(view.get_by_text("Счетов нет.")).to_be_visible()

    view.get_by_role("button", name="Добавить счёт").click()
    expect(view.get_by_text("Первый счёт станет основным.")).to_be_visible()
    expect(view.get_by_role("checkbox", name="Сделать основным")).to_have_count(0)
    view.get_by_placeholder("IBAN или номер счёта").fill(IBAN_MDL)
    view.get_by_placeholder("Банк", exact=True).fill("QA Bank")
    view.get_by_placeholder("Код банка (BIC)").fill("AGRNMD2X")
    with cp.expect_response(is_account_request("POST", card)) as created:
        view.get_by_role("button", name="Сохранить").click()

    assert created.value.status == 201, created.value.text()
    row = account(view, IBAN_MDL)
    expect(row).to_contain_text("основной")
    expect(row).to_contain_text("QA Bank · AGRNMD2X")
    expect(view.get_by_text("Счетов нет.")).to_have_count(0)
    saved = saved_accounts(cp, card)[IBAN_MDL]
    assert saved["is_main"] is True and saved["bank_name"] == "QA Bank" and saved["bank_code"] == "AGRNMD2X"


# Кейс: IBAN с неверной контрольной суммой не сохраняется
@pytest.mark.writes_data
def test_bank_account_bad_checksum_rejected(cp: Page):
    card = create_card(cp)
    view = open_card(cp, card)
    view.get_by_role("button", name="Добавить счёт").click()
    bad = IBAN_EUR.replace("MD37", "MD11")
    view.get_by_placeholder("IBAN или номер счёта").fill(bad)
    with cp.expect_response(is_account_request("POST", card)) as created:
        view.get_by_role("button", name="Сохранить").click()

    assert created.value.status == 400
    expect(view.get_by_text("IBAN не сходится по контрольной сумме — проверьте цифры")).to_be_visible()
    expect(view.get_by_placeholder("IBAN или номер счёта")).to_have_value(bad)
    assert saved_accounts(cp, card) == {}


# Кейс: Повтор счёта на карточке не принимается (пробелы и регистр не помогают)
@pytest.mark.writes_data
def test_bank_account_duplicate_rejected(cp: Page):
    card = create_card(cp)
    add_account(cp, card, IBAN_MDL)
    view = open_card(cp, card)
    view.get_by_role("button", name="Добавить счёт").click()
    spaced = " ".join(IBAN_MDL.lower()[i:i + 4] for i in range(0, len(IBAN_MDL), 4))
    view.get_by_placeholder("IBAN или номер счёта").fill(spaced)
    with cp.expect_response(is_account_request("POST", card)) as created:
        view.get_by_role("button", name="Сохранить").click()

    assert created.value.status == 409
    expect(view.get_by_text(f"Счёт {IBAN_MDL} у этой карточки уже есть")).to_be_visible()
    assert list(saved_accounts(cp, card)) == [IBAN_MDL]


# Кейс: Второй счёт: «Сделать основным», правка и удаление основного
@pytest.mark.writes_data
def test_bank_account_switch_main_edit_delete(cp: Page):
    card = create_card(cp)
    # «первый счёт — основной» решает форма, сервер сам is_main не ставит
    add_account(cp, card, IBAN_MDL, bank_name="QA Bank", is_main=True)
    add_account(cp, card, IBAN_EUR, bank_name="QA Bank 2", currency="EUR")
    view = open_card(cp, card)
    expect(accounts(view)).to_have_count(2)
    expect(account(view, IBAN_MDL)).to_contain_text("основной")
    expect(account(view, IBAN_EUR)).not_to_contain_text("основной")
    # у основного кнопки «Сделать основным» нет
    expect(account(view, IBAN_MDL).get_by_role("button", name="Сделать основным")).to_have_count(0)

    with cp.expect_response(is_account_request("PATCH", card)) as made_main:
        account(view, IBAN_EUR).get_by_role("button", name="Сделать основным").click()
    assert made_main.value.ok, made_main.value.text()
    expect(account(view, IBAN_EUR)).to_contain_text("основной")
    expect(account(view, IBAN_MDL)).not_to_contain_text("основной")
    saved = saved_accounts(cp, card)
    assert saved[IBAN_EUR]["is_main"] is True and saved[IBAN_MDL]["is_main"] is False

    account(view, IBAN_MDL).get_by_role("button", name="Изменить").click()
    view.get_by_placeholder("Банк", exact=True).fill("QA Bank изм.")
    view.get_by_placeholder("Валюта: MDL").fill("USD")
    with cp.expect_response(is_account_request("PATCH", card)) as edited:
        view.get_by_role("button", name="Сохранить").click()
    assert edited.value.ok, edited.value.text()
    expect(account(view, IBAN_MDL)).to_contain_text("QA Bank изм.")
    expect(account(view, IBAN_MDL)).to_contain_text("USD")
    saved = saved_accounts(cp, card)[IBAN_MDL]
    assert saved["bank_name"] == "QA Bank изм." and saved["currency"] == "USD" and saved["is_main"] is False

    account(view, IBAN_EUR).get_by_role("button", name="Удалить").click()
    dialog = cp.get_by_role("dialog", name=f"Удалить счёт {IBAN_EUR}?")
    with cp.expect_response(is_account_request("DELETE", card)) as deleted:
        dialog.get_by_role("button", name="Да").click()
    assert deleted.value.ok, deleted.value.text()
    expect(accounts(view)).to_have_count(1)
    expect(account(view, IBAN_MDL)).to_contain_text("основной")
    saved = saved_accounts(cp, card)
    assert list(saved) == [IBAN_MDL] and saved[IBAN_MDL]["is_main"] is True
