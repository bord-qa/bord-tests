"""Блок 22. CRM — сделки: crm.bord.one/admin/crm/deals.

Снято 27.10.2026:
- доска основной воронки: GET /api/crm/deals/pipeline?pipeline_id=<id>&… →
  список колонок {status, deals, count, total_amount}; шапка колонки —
  span[title=<этап>], рядом счётчик и сумма «25 000,00 MDL». Этапы основной
  воронки BORD QA: Новая, В работе, Предложение, Переговоры, Выиграна, Проиграна;
- «Новая сделка» → окно: Название* («Название или поиск товара…»), Контакт
  («Найти контакт (имя / телефон)…»), Ответственный (всплывашка с «Поиск…»),
  под «Ещё поля…» — Компания, Источник, Сумма («0.00»), Валюта, Вероятность,
  Ожид. закрытие. «Создать» неактивна без названия → POST /api/crm/deals/
  {title, amount, currency, contact_id, pipeline_id, responsible_id} → 201;
- карточка открывается кликом по названию (ссылка), id в адресе, заголовок
  вкладки «D-0000N <название> · Сделки · BORD CRM»; крестик «Закрыть (Esc)».
  Полоса этапов — кнопки с title «<этап>\\n\\n<описание>», «Сейчас: <этап>»;
  клик → PUT /api/crm/deals/<id>/stage {status_id}. «Выиграна»/«Проиграна»
  спрашивают причину («Сделка выиграна» / «Сделка проиграна», комментарий) →
  {status_id, result: won|lost, closure_reason};
- быстрая заметка «Заметка в ленту сделки…» + Enter → POST /api/crm/deal-notes;
- «В архив» внизу карточки → «Убрать сделку в архив?» → POST
  /api/crm/bulk/deals/archive; архив — кнопка «Архив: сделки, убранные из
  воронки» (таблица, &result=archived; там она же — «Выйти из архива»),
  в строке «Вернуть из архива» и
  «Удалить навсегда» (окно просит набрать УДАЛИТЬ) → DELETE /api/crm/deals/<id>;
- «Мои — где я ответственный» → отдельная доска /admin/crm/deals/my;
  «Таблица» → &view=table, GET /api/crm/deals/?pipeline_id=…

Тестовые сделки «QA Autotest …» ставим на тест-учётку и удаляем насовсем
(DELETE есть). Стартовые примеры («Пример: …») на живом аккаунте не трогаем.
"""
import random
import re

import pytest
from playwright.sync_api import Page, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from helpers.crm import CRM, QA_PREFIX, crm_delete, crm_get, crm_me, crm_post, crm_ready, open_crm

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/deals"


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


def money(text: str) -> float:
    """«25 000,00 MDL» / «777 MDL» → 25000.0 / 777.0 (пробелы бывают неразрывные)."""
    digits = re.sub(r"[^\d,]", "", text).replace(",", ".")
    return float(digits or 0)


def main_pipeline(page: Page) -> dict:
    items = crm_get(page, "/pipeline-statuses/pipelines").json()["items"]
    return next(p for p in items if p["is_main"])


def create_deal(page: Page, **fields) -> dict:
    data = {"title": f"{QA_PREFIX} {tag()}", "pipeline_id": main_pipeline(page)["id"],
            "responsible_id": crm_me(page)["id"], **fields}
    resp = crm_post(page, "/deals/", data)
    assert resp.status == 201, resp.text()
    return resp.json()


def delete_qa_deals(page: Page):
    for archived in ("false", "true"):
        resp = crm_get(page, "/deals/", {"search": QA_PREFIX, "archived": archived, "limit": 100})
        for d in resp.json()["items"] if resp.ok else []:
            if d["title"].startswith(QA_PREFIX):
                crm_delete(page, f"/deals/{d['id']}")
    resp = crm_get(page, "/contacts/", {"search": QA_PREFIX, "limit": 200})
    ids = [c["id"] for c in resp.json()["items"] if c["first_name"].startswith(QA_PREFIX)] if resp.ok else []
    if ids:
        crm_post(page, "/bulk/contacts/delete", {"contact_ids": ids})


@pytest.fixture
def deals(auth_page: Page):
    open_crm(auth_page, PAGE)
    yield auth_page
    delete_qa_deals(auth_page)


def column(page: Page, stage: str):
    """Шапка колонки доски: название, счётчик, сумма."""
    return page.locator(f"main span[title='{stage}']").locator(
        "xpath=ancestor::div[contains(@class,'justify-between')][1]/..")


def column_count(page: Page, stage: str):
    return page.locator(f"main span[title='{stage}']").locator("xpath=following-sibling::span[1]")


def deal_link(page: Page, title: str):
    return page.locator("main").get_by_role("link", name=title, exact=True)


def open_card(page: Page, deal: dict):
    title = re.compile(rf"^D-\d+ {re.escape(deal['title'])} · Сделки · BORD CRM$")
    for attempt in range(3):
        page.goto(f"{CRM}{PAGE}?id={deal['id']}")
        try:
            expect(page).to_have_title(title, timeout=10_000)
            return page
        except AssertionError:
            if attempt == 2:
                raise
            page.wait_for_timeout(2_000)  # см. reload_board: всплеск запросов → 429


def stage_button(page: Page, stage: str):
    # title кнопки — «<этап>\n\n<описание>»; перевод строки в CSS-селекторе не задать
    return page.locator(f"xpath=//button[starts-with(@title, '{stage}\n')]")


def current_stage(page: Page):
    return page.get_by_text(re.compile(r"^Сейчас: "))


def overlay(page: Page, text: str):
    """Окно подтверждения поверх карточки (div.fixed.inset-0, не role=dialog)."""
    return page.locator("div.fixed.inset-0").filter(has_text=text).last


def reload_board(page: Page):
    """Перезагрузить доску и дождаться её данных.

    API CRM режет всплески одновременных запросов (429): карточка сделки шлёт
    их ~35 разом, и если сразу после неё перезагрузить страницу, список воронок
    получает 429 — доска остаётся пустой и сама запрос не повторяет (снято
    27.10.2026). Поэтому ждём ответ доски и при неудаче повторяем через паузу."""
    for attempt in range(3):
        try:
            with page.expect_response(lambda r: "/api/crm/deals/pipeline?" in r.url and r.ok, timeout=10_000):
                page.reload()
            break
        except PlaywrightTimeoutError:
            page.wait_for_timeout(2_000)
    crm_ready(page)


# Кейс: Доска основной воронки совпадает с API — этапы, счётчики, суммы
def test_board_matches_api(deals: Page):
    pipeline = main_pipeline(deals)
    board = crm_get(deals, "/deals/pipeline", {"pipeline_id": pipeline["id"], "mirrors": "true", "limit": 50}).json()

    for col in board:
        stage = col["status"]["name"]
        expect(column_count(deals, stage)).to_have_text(str(col["count"]))
        head = column(deals, stage)
        expect(head).to_contain_text("MDL")
        assert money(head.inner_text().split("\n")[-1]) == pytest.approx(col["total_amount"]), \
            f"«{stage}»: сумма на доске {head.inner_text()!r}, в API {col['total_amount']}"
        for d in col["deals"]:
            # одноимённые сделки бывают (веб-чат: три «Заявка из чата — Посетитель сайта») — ищем по id
            link = deals.locator(f'main a[href="/admin/crm/deals?id={d["id"]}"]')
            expect(link, d["title"]).to_have_text(d["title"])


# Кейс: «Создать» неактивна без названия сделки
def test_create_requires_title(deals: Page):
    deals.get_by_role("button", name="Новая сделка").click()
    form = deals.get_by_role("heading", name="Новая сделка").locator(
        "xpath=ancestor::div[.//button[normalize-space()='Отмена']][1]")
    create = form.get_by_role("button", name="Создать", exact=True)

    expect(create).to_be_disabled()
    form.get_by_placeholder("Название или поиск товара…").fill(f"{QA_PREFIX} {tag()}")
    expect(create).to_be_enabled()
    form.get_by_role("button", name="Отмена", exact=True).click()
    expect(deals.get_by_role("heading", name="Новая сделка")).to_have_count(0)


# Кейс: Новая сделка через форму — клиент, ответственный, сумма — встаёт в «Новая»
def test_create_deal_via_form(deals: Page):
    me = crm_me(deals)
    contact = crm_post(deals, "/contacts/", {"first_name": f"{QA_PREFIX} {tag()}",
                                             "phone": "+3736" + "".join(random.choices("0123456789", k=7))}).json()
    title = f"{QA_PREFIX} {tag()}"
    before = int(column_count(deals, "Новая").inner_text())

    deals.get_by_role("button", name="Новая сделка").click()
    form = deals.get_by_role("heading", name="Новая сделка").locator(
        "xpath=ancestor::div[.//button[normalize-space()='Отмена']][1]")
    form.get_by_placeholder("Название или поиск товара…").fill(title)
    form.get_by_placeholder("Найти контакт (имя / телефон)…").fill(contact["first_name"])
    deals.get_by_text(contact["first_name"], exact=True).last.click()
    form.get_by_role("button", name="— не назначен —").click()
    deals.get_by_placeholder("Поиск…").locator("xpath=ancestor::div[.//button][1]").get_by_role(
        "button", name=me["email"].split("@")[0], exact=True).click()
    form.get_by_role("button", name=re.compile("^Ещё поля")).click()
    form.get_by_placeholder("0.00").fill("12345")

    with deals.expect_response(lambda r: r.url.endswith("/api/crm/deals/") and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    created = resp.value.json()
    assert (created["contact_id"], created["responsible_id"], created["amount"]) == (contact["id"], me["id"], 12345)
    assert created["status_name"] in (None, "Новая") and created["pipeline_id"] == main_pipeline(deals)["id"]

    expect(deal_link(deals, title)).to_be_visible()
    expect(column_count(deals, "Новая")).to_have_text(str(before + 1))


# Кейс: Карточка сделки открывается кликом и по ссылке, закрытие убирает её из адреса
def test_card_open_link_close(deals: Page):
    d = create_deal(deals, amount=777)
    reload_board(deals)

    deal_link(deals, d["title"]).click()
    expect(deals).to_have_url(re.compile(rf"[?&]id={d['id']}(&|$)"))
    expect(deals).to_have_title(f"{d['number']} {d['title']} · Сделки · BORD CRM")
    expect(current_stage(deals)).to_have_text("Сейчас: Новая")

    open_card(deals, d)  # reload той же ссылки ?id=, с повтором при 429
    expect(deals).to_have_title(f"{d['number']} {d['title']} · Сделки · BORD CRM")
    deals.get_by_title("Закрыть (Esc)").click()
    expect(deals).not_to_have_url(re.compile(r"[?&]id="))
    expect(current_stage(deals)).to_have_count(0)


# Кейс: Смена этапа полосой в карточке — API и доска
def test_move_stage(deals: Page):
    d = create_deal(deals)
    open_card(deals, d)

    with deals.expect_response(lambda r: r.url.endswith(f"/deals/{d['id']}/stage") and r.request.method == "PUT") as resp:
        stage_button(deals, "В работе").click()
    assert resp.value.ok, resp.value.text()

    expect(current_stage(deals)).to_have_text("Сейчас: В работе")
    assert crm_get(deals, f"/deals/{d['id']}").json()["status_name"] == "В работе"
    deals.get_by_title("Закрыть (Esc)").click()
    reload_board(deals)
    expect(column(deals, "В работе").locator("xpath=..").get_by_role("link", name=d["title"])).to_be_visible()


# Кейс: Закрыть сделку как проигранную с комментарием
def test_close_lost_with_comment(deals: Page):
    d = create_deal(deals)
    open_card(deals, d)
    reason = f"{QA_PREFIX} причина {tag()}"

    stage_button(deals, "Проиграна").click()
    dialog = overlay(deals, "Сделка проиграна")
    dialog.get_by_placeholder(re.compile("комментарий", re.I)).fill(reason)
    with deals.expect_response(lambda r: r.url.endswith(f"/deals/{d['id']}/stage")) as resp:
        dialog.get_by_role("button", name="Закрыть как проигранную").click()
    assert resp.value.ok, resp.value.text()

    expect(current_stage(deals)).to_have_text("Сейчас: Проиграна")
    saved = crm_get(deals, f"/deals/{d['id']}").json()
    assert (saved["result"], saved["closure_reason"]) == ("lost", reason), saved


# Кейс: Закрыть сделку как выигранную
def test_close_won(deals: Page):
    d = create_deal(deals, amount=500)
    open_card(deals, d)

    stage_button(deals, "Выиграна").click()
    with deals.expect_response(lambda r: r.url.endswith(f"/deals/{d['id']}/stage")) as resp:
        overlay(deals, "Сделка выиграна").get_by_role("button", name="Закрыть как выигранную").click()
    assert resp.value.ok, resp.value.text()

    expect(current_stage(deals)).to_have_text("Сейчас: Выиграна")
    saved = crm_get(deals, f"/deals/{d['id']}").json()
    assert saved["result"] == "won" and saved["closed_at"], saved


# Кейс: Отмена в окне «Сделка выиграна» оставляет этап
def test_close_won_cancel(deals: Page):
    d = create_deal(deals)
    open_card(deals, d)

    stage_button(deals, "Выиграна").click()
    overlay(deals, "Сделка выиграна").get_by_role("button", name="Отмена").click()

    expect(deals.get_by_text("Сделка выиграна")).to_have_count(0)
    expect(current_stage(deals)).to_have_text("Сейчас: Новая")
    assert crm_get(deals, f"/deals/{d['id']}").json()["status_name"] == "Новая"


# Кейс: Быстрая заметка в карточке уходит в ленту сделки
def test_quick_note(deals: Page):
    d = create_deal(deals)
    open_card(deals, d)
    text = f"{QA_PREFIX} заметка {tag()}"

    deals.get_by_placeholder(re.compile("^Заметка в ленту сделки")).fill(text)
    with deals.expect_response(lambda r: r.url.endswith("/api/crm/deal-notes") and r.request.method == "POST") as resp:
        deals.keyboard.press("Enter")
    assert resp.value.status in (200, 201), resp.value.text()
    assert resp.value.json()["deal_id"] == d["id"]

    expect(deals.get_by_text(text, exact=True)).to_be_visible()


# Кейс: Сделка в архив из карточки и обратно из архива
def test_archive_and_restore(deals: Page):
    d = create_deal(deals)
    open_card(deals, d)

    deals.get_by_role("button", name="В архив", exact=True).last.click()
    confirm = overlay(deals, "Убрать сделку в архив?")
    expect(confirm).to_contain_text("история и задачи останутся")
    with deals.expect_response(lambda r: "/bulk/deals/archive" in r.url) as resp:
        confirm.get_by_role("button", name="В архив", exact=True).click()
    assert resp.value.ok, resp.value.text()
    reload_board(deals)
    expect(deal_link(deals, d["title"])).to_have_count(0)

    deals.get_by_title("Архив: сделки, убранные из воронки").click()
    archived = deals.locator("main tbody tr").filter(has_text=d["title"])
    with deals.expect_response(lambda r: "/bulk/deals/restore" in r.url) as resp:
        archived.get_by_role("button", name="Вернуть из архива").click()
    assert resp.value.ok, resp.value.text()
    expect(archived).to_have_count(0)

    # в архиве та же кнопка называется «Выйти из архива»
    deals.get_by_title("Выйти из архива", exact=True).click()
    expect(deals).not_to_have_url(re.compile(r"result=archived"))
    expect(deal_link(deals, d["title"])).to_be_visible()


# Кейс: «Удалить навсегда» только после слова УДАЛИТЬ — сделки больше нет
def test_delete_forever(deals: Page):
    d = create_deal(deals)
    crm_post(deals, "/bulk/deals/archive", {"deal_ids": [d["id"]]})
    deals.goto(f"{CRM}{PAGE}?result=archived&view=table")
    crm_ready(deals)
    row = deals.locator("main tbody tr").filter(has_text=d["title"])

    row.get_by_role("button", name="Удалить навсегда").click()
    confirm = overlay(deals, "Удалить сделку навсегда?")
    submit = confirm.get_by_role("button", name="Удалить навсегда")
    expect(submit).to_be_disabled()
    confirm.locator("input").fill("УДАЛ")
    expect(submit).to_be_disabled()
    confirm.locator("input").fill("УДАЛИТЬ")
    with deals.expect_response(lambda r: r.url.endswith(f"/api/crm/deals/{d['id']}") and r.request.method == "DELETE") as resp:
        submit.click()
    assert resp.value.status == 204, resp.value.text()

    expect(row).to_have_count(0)
    assert crm_get(deals, f"/deals/{d['id']}").status == 404


# Кейс: «Мои» — только сделки, где я ответственный
def test_my_deals(deals: Page):
    me = crm_me(deals)
    mine = create_deal(deals)
    pipeline = main_pipeline(deals)
    board = crm_get(deals, "/deals/pipeline", {"pipeline_id": pipeline["id"], "limit": 50}).json()
    others = [d for col in board for d in col["deals"] if d["responsible_id"] != me["id"]]
    reload_board(deals)

    deals.get_by_title("Мои — где я ответственный").click()
    expect(deals).to_have_url(re.compile(r"/admin/crm/deals/my"))
    expect(deal_link(deals, mine["title"])).to_be_visible()
    for d in others:
        expect(deal_link(deals, d["title"])).to_have_count(0)


# Кейс: Таблица сделок — строка совпадает с API
def test_table_view(deals: Page):
    d = create_deal(deals, amount=4321)
    reload_board(deals)

    with deals.expect_response(lambda r: "/api/crm/deals/?" in r.url and "pipeline_id=" in r.url):
        deals.get_by_title("Таблица").click()
    expect(deals).to_have_url(re.compile(r"view=table"))
    expect(deals.locator("main thead")).to_contain_text("Название")  # капс — только CSS
    row = deals.locator("main tbody tr").filter(has_text=d["title"])
    expect(row).to_contain_text(d["number"])
    expect(row).to_contain_text("Новая")
    expect(row).to_contain_text(crm_me(deals)["email"].split("@")[0])
    assert money(row.locator("td").filter(has_text="MDL").first.inner_text()) == 4321


# Кейс: Номера сделок не повторяются после удаления (ecom-100248 починен 29.10.2026)
def test_deal_numbers_unique_after_delete(deals: Page):
    a, b = create_deal(deals), create_deal(deals)
    crm_delete(deals, f"/deals/{a['id']}")

    c = create_deal(deals)

    assert c["number"] != b["number"], f"новая сделка получила номер живой: {c['number']} = {b['number']}"
