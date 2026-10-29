"""Блок 32. CRM — глобальный поиск и экспорт CSV.

Снято 29.10.2026:
- поиск — палитра по Ctrl+K или кнопке «Поиск» в шапке: поле «Найти что
  угодно...»; без запроса — ссылки на разделы («Клиенты», «Сделки»,
  «Задачи», «Календарь», «Отчёты», «Настройки»); с запросом — GET
  /api/crm/search/?q=…&limit=20 → {results: {contacts, companies, deals
  (group open_deals / closed_deals), tasks, …}, total}; группы «Контакты»,
  «Открытые сделки», «Закрытые сделки», «Задачи» (на экране капсом через
  CSS), пункты — ссылки /contacts?id=, /deals?id=, /tasks?task=; ищет по
  имени, телефону, номеру сделки D-…; ничего — «Ничего не найдено»;
  ↑/↓ двигают выделение (bg-brand-50), Enter открывает, Esc закрывает;
- экспорт — иконка «Экспорт CSV» (title) у списка клиентов и сделок:
  GET /export/contacts?format=csv[&search=…] → contacts.csv, GET
  /export/deals?format=csv&pipeline_id=… → deals.csv; колонки по-английски
  (ID, Number, Title, Pipeline, Status…; ID, First Name, Phone…); поиск на
  странице клиентов уходит в выгрузку; архивных клиентов в ней нет.

Свои записи заводим с уникальным словом и убираем: сделки и задачи
удаляются, клиенты — в архив (удаления нет).
"""
import csv
import io
import random
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import CRM, QA_PREFIX, crm_delete, crm_get, crm_me, crm_post, crm_put, open_crm

pytestmark = pytest.mark.requires_auth


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=10))


def main_pipeline(page: Page) -> dict:
    return next(p for p in crm_get(page, "/pipeline-statuses/pipelines").json()["items"] if p["is_main"])


def make_contact(page: Page, word: str) -> dict:
    resp = crm_post(page, "/contacts/", {"first_name": f"{QA_PREFIX} {word}",
                                         "phone": "+3736" + "".join(random.choices("0123456789", k=7))})
    assert resp.status == 201, resp.text()
    page._qa_contacts.append(resp.json()["id"])
    return resp.json()


def make_deal(page: Page, word: str, won: bool = False) -> dict:
    pipeline = main_pipeline(page)
    resp = crm_post(page, "/deals/", {"title": f"{QA_PREFIX} {word} сделка", "pipeline_id": pipeline["id"],
                                      "responsible_id": crm_me(page)["id"]})
    assert resp.status == 201, resp.text()
    deal = resp.json()
    page._qa_deals.append(deal["id"])
    if won:
        status = next(s for s in pipeline["statuses"] if s["is_close"] and not s["is_lost"])
        assert crm_put(page, f"/deals/{deal['id']}/stage", {"status_id": status["id"], "result": "won"}).ok
        deal = crm_get(page, f"/deals/{deal['id']}").json()
    return deal


def make_task(page: Page, word: str) -> dict:
    resp = crm_post(page, "/tasks/", {"title": f"{QA_PREFIX} {word} задача", "responsible_id": crm_me(page)["id"]})
    assert resp.status == 201, resp.text()
    page._qa_tasks.append(resp.json()["id"])
    return resp.json()


@pytest.fixture
def crm(auth_page: Page):
    auth_page._qa_contacts, auth_page._qa_deals, auth_page._qa_tasks = [], [], []
    yield auth_page
    for deal_id in auth_page._qa_deals:
        crm_delete(auth_page, f"/deals/{deal_id}")
    for task_id in auth_page._qa_tasks:
        crm_delete(auth_page, f"/tasks/{task_id}")
        crm_delete(auth_page, f"/tasks/{task_id}/permanent")
    if auth_page._qa_contacts:
        crm_post(auth_page, "/bulk/contacts/delete", {"contact_ids": auth_page._qa_contacts})


def palette(page: Page):
    return page.locator("div.fixed.inset-0").filter(has=page.get_by_role("textbox", name="Найти что угодно...")).last


def search(page: Page, query: str):
    """Открыть палитру по Ctrl+K и дождаться ответа поиска на запрос."""
    page.keyboard.press("Control+k")
    box = page.get_by_role("textbox", name="Найти что угодно...")
    expect(box).to_be_focused()
    with page.expect_response(lambda r: "/api/crm/search/" in r.url and r.ok):
        box.fill(query)
    return palette(page)


def result(page: Page, href: str):
    return palette(page).locator(f"a[href='{href}']")


def group_of(link) -> str:
    """Подпись группы, в которой стоит пункт: первый элемент его блока."""
    return link.locator("xpath=../*[1]").text_content().strip()


def api_search(page: Page, query: str) -> dict:
    return crm_get(page, "/search/", {"q": query, "limit": 20}).json()["results"]


def export_csv(page: Page, url_part: str) -> tuple[str, list[dict], str]:
    """«Экспорт CSV» → (имя файла, строки, адрес запроса)."""
    with page.expect_request(lambda r: url_part in r.url) as req, page.expect_download() as download:
        page.locator("button[title='Экспорт CSV']").click()
    text = open(download.value.path(), encoding="utf-8-sig").read()
    return download.value.suggested_filename, list(csv.DictReader(io.StringIO(text))), req.value.url


# Кейс: Поиск по слову находит своего клиента, открытую и закрытую сделку и задачу — группы и ссылки = API
def test_search_finds_own_records(crm: Page):
    word = tag()
    contact, deal, won, task = (make_contact(crm, word), make_deal(crm, word), make_deal(crm, word, won=True),
                                make_task(crm, word))
    open_crm(crm)
    search(crm, word)

    found = api_search(crm, word)
    assert [c["id"] for c in found["contacts"]] == [contact["id"]], found
    assert {(d["id"], d["group"]) for d in found["deals"]} == {(deal["id"], "open_deals"), (won["id"], "closed_deals")}
    assert [t["id"] for t in found["tasks"]] == [task["id"]], found
    expected = {f"/admin/crm/contacts?id={contact['id']}": "Контакты",
                f"/admin/crm/deals?id={deal['id']}": "Открытые сделки",
                f"/admin/crm/deals?id={won['id']}": "Закрытые сделки",
                f"/admin/crm/tasks?task={task['id']}": "Задачи"}
    expect(palette(crm).locator("a")).to_have_count(len(expected))
    for href, group in expected.items():
        link = result(crm, href)
        expect(link).to_be_visible()
        assert group_of(link) == group, f"{href} в группе «{group_of(link)}», ждали «{group}»"
    expect(result(crm, f"/admin/crm/contacts?id={contact['id']}")).to_contain_text(contact["phone"])
    expect(result(crm, f"/admin/crm/deals?id={deal['id']}")).to_contain_text(deal["number"])
    expect(result(crm, f"/admin/crm/deals?id={won['id']}")).to_contain_text("Выиграна")


# Кейс: Поиск по номеру сделки D-… и по телефону клиента
def test_search_by_number_and_phone(crm: Page):
    word = tag()
    contact, deal = make_contact(crm, word), make_deal(crm, word)
    open_crm(crm)

    search(crm, deal["number"])
    expect(result(crm, f"/admin/crm/deals?id={deal['id']}")).to_be_visible()
    crm.keyboard.press("Escape")
    search(crm, contact["phone"])
    expect(result(crm, f"/admin/crm/contacts?id={contact['id']}")).to_be_visible()


# Кейс: Клавиатура в поиске — ↓ переводит выделение, Enter открывает выбранную сделку
def test_search_keyboard(crm: Page):
    word = tag()
    contact, deal = make_contact(crm, word), make_deal(crm, word)
    open_crm(crm)
    search(crm, word)
    first, second = (result(crm, f"/admin/crm/contacts?id={contact['id']}"),
                     result(crm, f"/admin/crm/deals?id={deal['id']}"))

    expect(first).to_have_class(re.compile(r"\bbg-brand-50\b"))
    crm.keyboard.press("ArrowDown")
    expect(second).to_have_class(re.compile(r"\bbg-brand-50\b"))
    expect(first).not_to_have_class(re.compile(r"\bbg-brand-50\b"))
    crm.keyboard.press("Enter")
    expect(crm).to_have_url(re.compile(rf"/admin/crm/deals\?.*\bid={deal['id']}\b"))
    expect(crm.get_by_role("heading", name=deal["title"], level=2)).to_be_visible()


# Кейс: Клик по задаче в поиске — боковая панель «Задача» поверх текущей страницы, «Открыть полностью» ведёт в задачи
def test_search_opens_task(crm: Page):
    task = make_task(crm, tag())
    open_crm(crm)
    search(crm, task["title"].split()[-2])

    result(crm, f"/admin/crm/tasks?task={task['id']}").click()
    # ссылка ведёт в /tasks?task=, но клик открывает панель на месте: ?panel=task:<id>
    expect(crm).to_have_url(re.compile(rf"/admin/crm/reports\?.*\bpanel=task(%3A|:){task['id']}\b"))
    panel = crm.locator("div.fixed.inset-0.justify-end").filter(has_text="Открыть полностью").last
    expect(panel).to_contain_text(task["title"], timeout=20_000)
    panel.get_by_text("Открыть полностью").click()
    expect(crm).to_have_url(re.compile(rf"/admin/crm/tasks\?.*\btask={task['id']}\b"))


# Кейс: Нет совпадений — «Ничего не найдено»; Esc закрывает палитру
def test_search_nothing_found(crm: Page):
    open_crm(crm)
    search(crm, f"qqnothing{tag()}")

    expect(palette(crm)).to_contain_text("Ничего не найдено")
    expect(palette(crm).locator("a")).to_have_count(0)
    crm.keyboard.press("Escape")
    expect(palette(crm)).to_have_count(0)


# Кейс: Кнопка «Поиск» в шапке открывает палитру; без запроса — ссылки на разделы CRM
def test_palette_from_header_shows_pages(crm: Page):
    open_crm(crm)
    crm.locator("header").get_by_role("button", name="Поиск").click()
    pages = {"Клиенты": "/admin/crm/contacts", "Сделки": "/admin/crm/deals", "Задачи": "/admin/crm/tasks",
             "Календарь": "/admin/calendar", "Отчёты": "/admin/crm/reports", "Настройки": "/admin/crm/settings"}

    expect(palette(crm)).to_contain_text("Страницы")
    for name, href in pages.items():
        expect(palette(crm).locator(f"a[href='{href}']")).to_contain_text(name)
    palette(crm).locator("a[href='/admin/crm/tasks']").click()
    expect(crm).to_have_url(re.compile(r"/admin/crm/tasks"))
    expect(palette(crm)).to_have_count(0)


# Кейс: «Экспорт CSV» сделок — deals.csv, свои сделки с номером, воронкой, статусом и результатом
def test_export_deals_csv(crm: Page):
    word = tag()
    deal, won = make_deal(crm, word), make_deal(crm, word, won=True)
    open_crm(crm, "/admin/crm/deals?view=table")
    expect(crm.locator("button[title='Экспорт CSV']")).to_be_visible()

    name, rows, url = export_csv(crm, "/api/crm/export/deals")
    assert name == "deals.csv" and "format=csv" in url, (name, url)
    assert rows and list(rows[0])[:5] == ["ID", "Number", "Title", "Pipeline", "Status"], list(rows[0])
    mine = {int(r["ID"]): r for r in rows if word in r["Title"]}
    assert set(mine) == {deal["id"], won["id"]}, mine
    pipeline = main_pipeline(crm)["name"]
    assert (mine[deal["id"]]["Number"], mine[deal["id"]]["Title"], mine[deal["id"]]["Pipeline"],
            mine[deal["id"]]["Status"]) == (deal["number"], deal["title"], pipeline, "Новая"), mine[deal["id"]]
    assert (mine[won["id"]]["Status"], mine[won["id"]]["Result"]) == ("Выиграна", "won"), mine[won["id"]]


# Кейс: «Экспорт CSV» клиентов учитывает поиск на странице; архивного клиента в выгрузке нет
def test_export_contacts_csv_with_search(crm: Page):
    word = tag()
    contact, archived = make_contact(crm, word), make_contact(crm, word)
    assert crm_post(crm, "/bulk/contacts/delete", {"contact_ids": [archived["id"]]}).ok
    open_crm(crm, "/admin/crm/contacts")
    with crm.expect_response(lambda r: "/api/crm/contacts" in r.url and word in r.url):
        crm.locator("main").get_by_placeholder("Имя, email, телефон…").fill(word)

    name, rows, url = export_csv(crm, "/api/crm/export/contacts")
    assert name == "contacts.csv" and f"search={word}" in url, (name, url)
    assert list(rows[0])[:5] == ["ID", "First Name", "Last Name", "Email", "Phone"], list(rows[0]) if rows else rows
    assert [(int(r["ID"]), r["First Name"], r["Phone"]) for r in rows] == [
        (contact["id"], contact["first_name"], contact["phone"])], rows
