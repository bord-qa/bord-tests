"""Блок 12. Домены: app.bord.one/admin/domains.

Снято 22.10.2026:
- API: /api/domains/ → {items, summary{total, upcoming, expired, via_api,
  manual, gone}}, /api/domains/meta (kinds, states, remind_days=30),
  /api/domains/sources (источники);
- «Источники» → «Ручной источник» (окно: Название, Ссылка на кабинет
  регистратора; поля предзаполнены «nic.md») → POST …/sources; у карточки
  источника «Импорт списка», «Изменить», «Отключить» → окно «Отключить «…»?
  Домены останутся в списке без источника.» → DELETE …/sources/<id>;
- «Домен вручную» → окно «Новый домен»: Домен (пусто — «Сохранить»
  неактивна), Где зарегистрирован, Действует до, Цена, Валюта, Заметка →
  POST /api/domains/; не домен → 400 «…не похоже на домен» (полосой над
  списком); строка: домен, источник, дата, «<состояние> · N дн.», «Оплаты»
  (ссылка в Facturi), «Продлено» (сразу, срок + год → POST …/<id>/renewed),
  «Изменить», «Убрать» → окно «Убрать <домен> из списка?» → DELETE;
- «Импорт списка»: «домен [дата] [цена]» по строке → POST /api/domains/import
  → {added, skipped, errors}; уже заведённые не меняются.

Подключение name.com не проверяем: нужны настоящие ключи регистратора.
Свои домены «qa-autotest-…» и источники «QA Autotest …» удаляем в любом исходе.
"""
import datetime as dt
import re
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_delete, api_get, api_post
from helpers.modal import modal
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

API = "/api/domains/"
SOURCE_PREFIX = "QA Autotest"
DOMAIN_PREFIX = "qa-autotest-"
COUNTERS = {
    "Всего доменов": "total",
    "Истекают за 30 дн.": "upcoming",
    "Срок прошёл": "expired",
    "Управляются по API": "via_api",
    "Ведутся вручную": "manual",
}


def stamp() -> str:
    return str(int(time.time() * 1000))


def domain_name(tag: str = "") -> str:
    return f"{DOMAIN_PREFIX}{tag}{stamp()}.md"


def ru(day: dt.date) -> str:
    return day.strftime("%d.%m.%Y")


def summary(page: Page) -> dict:
    return api_get(page, API).json()["summary"]


def domains(page: Page) -> list:
    return api_get(page, API).json()["items"]


def find_domain(page: Page, name: str) -> dict | None:
    return next((d for d in domains(page) if d["name"] == name), None)


def create_domain(page: Page, source: dict, days: int | None = 10, **fields) -> dict:
    expires = (dt.date.today() + dt.timedelta(days=days)).isoformat() if days is not None else None
    data = {"name": domain_name(), "source_id": source["id"], "expires_on": expires, **fields}
    resp = api_post(page, API, data)
    assert resp.ok, resp.text()
    return resp.json()


def cleanup(page: Page):
    resp = api_get(page, API)
    for item in resp.json()["items"] if resp.ok else []:
        if item["name"].startswith(DOMAIN_PREFIX):
            api_delete(page, f"{API}{item['id']}")
    resp = api_get(page, f"{API}sources")
    for src in resp.json() if resp.ok else []:
        if src["name"].startswith(SOURCE_PREFIX):
            api_delete(page, f"{API}sources/{src['id']}")


@pytest.fixture
def dom(auth_page: Page):
    auth_page.goto("/admin/domains")
    expect(auth_page.locator("main").get_by_role("button", name=re.compile("^Источники"))).to_be_visible()
    settle(auth_page)
    yield auth_page
    cleanup(auth_page)


@pytest.fixture
def source(dom: Page) -> dict:
    resp = api_post(dom, f"{API}sources", {"kind": "manual", "name": f"{SOURCE_PREFIX} источник {stamp()}", "renew_url": "https://qa.example.com"})
    assert resp.ok, resp.text()
    return resp.json()


def reload(page: Page):
    page.reload()
    expect(page.locator("main").get_by_role("button", name=re.compile("^Источники"))).to_be_visible()
    settle(page)


def row(page: Page, name: str):
    return page.locator("main table tbody tr").filter(has_text=name)


def counter(page: Page, label: str):
    return page.locator("main button").filter(has_text=re.compile(rf"^\d+\s*{re.escape(label)}$"))


def sources_tab(page: Page):
    page.locator("main").get_by_role("button", name=re.compile("^Источники")).click()
    expect(page.locator("main").get_by_role("button", name="Ручной источник")).to_be_visible()


def source_card(page: Page, src: dict):
    # не exact: в том же элементе после названия идёт плашка «ручной»
    return page.locator("main").get_by_text(src["name"]).locator(
        "xpath=ancestor-or-self::*[.//button[normalize-space()='Отключить']][1]")


def pick_source(page: Page, form, src: dict):
    form.get_by_text("Где зарегистрирован", exact=True).locator("xpath=following::button[@aria-haspopup='listbox'][1]").click()
    page.get_by_role("option", name=src["name"], exact=True).click()


@pytest.mark.smoke
def test_counters_match_summary(dom: Page):
    data = summary(dom)

    for label, key in COUNTERS.items():
        expect(counter(dom, label), label).to_have_text(re.compile(rf"^{data[key]}\s*{re.escape(label)}$"))


@pytest.mark.writes_data
def test_manual_source_create(dom: Page):
    name = f"{SOURCE_PREFIX} источник {stamp()}"
    sources_tab(dom)
    dom.locator("main").get_by_role("button", name="Ручной источник").click()
    form = modal(dom, "Ручной источник")
    form.locator("input").first.fill(name)
    form.get_by_placeholder("https://").fill("https://qa-registrar.example.com")

    with dom.expect_response(lambda r: r.url.endswith(f"{API}sources") and r.request.method == "POST") as created:
        form.get_by_role("button", name="Создать").click()

    assert created.value.ok, created.value.text()
    assert created.value.request.post_data_json == {"kind": "manual", "name": name, "renew_url": "https://qa-registrar.example.com"}
    src = created.value.json()
    assert (src["via_api"], src["domains"]) == (False, 0)
    card = source_card(dom, src)
    expect(card).to_contain_text("Доменов: 0")
    expect(card.get_by_role("button", name="Импорт списка")).to_be_visible()


@pytest.mark.writes_data
def test_add_domain_via_form(dom: Page, source):
    reload(dom)
    before = summary(dom)
    name = domain_name()
    expires = dt.date.today() + dt.timedelta(days=10)
    dom.locator("main").get_by_role("button", name="Домен вручную").click()
    form = modal(dom, "Новый домен")
    save = form.get_by_role("button", name="Сохранить")
    expect(save).to_be_disabled()

    form.get_by_placeholder("example.md").fill("Не домен")
    with dom.expect_response(lambda r: r.url.endswith(API) and r.request.method == "POST") as refused:
        save.click()
    assert refused.value.status == 400
    expect(dom.get_by_text("«Не домен» не похоже на домен")).to_be_visible()

    form.get_by_placeholder("example.md").fill(name)
    pick_source(dom, form, source)
    form.locator("input[type=date]").fill(expires.isoformat())
    form.get_by_text("Цена продления (справочно)", exact=True).locator("xpath=following-sibling::input[1]").fill("450")
    form.get_by_placeholder("Чей, для чего").fill("домен автотеста")
    with dom.expect_response(lambda r: r.url.endswith(API) and r.request.method == "POST") as created:
        save.click()

    assert created.value.ok, created.value.text()
    sent = created.value.request.post_data_json
    assert (sent["name"], sent["source_id"], sent["expires_on"], sent["currency"]) == (name, source["id"], expires.isoformat(), "MDL")
    assert float(sent["price"]) == 450
    saved = created.value.json()
    assert (saved["state"], saved["days_left"]) == ("upcoming", 10)
    line = row(dom, name)
    for text in (source["name"], ru(expires), "Скоро продлевать · 10 дн."):
        expect(line, text).to_contain_text(text)
    expect(line.get_by_role("link", name="Оплаты")).to_have_attribute("href", re.compile(re.escape(name)))
    after = summary(dom)
    assert (after["total"], after["upcoming"], after["manual"]) == (before["total"] + 1, before["upcoming"] + 1, before["manual"] + 1)


@pytest.mark.writes_data
def test_domain_states(dom: Page, source):
    """Состояние в строке = state_label из API; прошедший срок и «не указан» — однозначно."""
    before = summary(dom)
    made = {days: create_domain(dom, source, days) for days in (-5, 3, 200, None)}
    reload(dom)

    assert made[-5]["state"] == "expired" and made[None]["state"] == "unknown" and made[200]["state"] == "ok"
    for item in made.values():
        expect(row(dom, item["name"]), item["name"]).to_contain_text(item["state_label"])
    expect(row(dom, made[-5]["name"])).to_contain_text("Срок прошёл")
    expect(row(dom, made[None]["name"])).to_contain_text("Срок не указан")
    after = summary(dom)
    assert after["expired"] == before["expired"] + 1
    expect(counter(dom, "Срок прошёл")).to_have_text(re.compile(rf"^{after['expired']}\s*Срок прошёл$"))


@pytest.mark.writes_data
def test_mark_renewed_adds_year(dom: Page, source):
    item = create_domain(dom, source, days=10)
    old = dt.date.fromisoformat(item["expires_on"])
    reload(dom)

    with dom.expect_response(lambda r: r.url.endswith(f"{item['id']}/renewed")) as renewed:
        row(dom, item["name"]).get_by_role("button", name="Продлено").click()

    assert renewed.value.ok
    new = dt.date.fromisoformat(renewed.value.json()["expires_on"])
    assert new == old.replace(year=old.year + 1)
    assert renewed.value.json()["state"] == "ok"
    expect(row(dom, item["name"])).to_contain_text(ru(new))
    expect(row(dom, item["name"])).not_to_contain_text("Скоро продлевать")


@pytest.mark.writes_data
def test_edit_domain(dom: Page, source):
    item = create_domain(dom, source, days=None)
    new_date = dt.date.today() + dt.timedelta(days=90)
    reload(dom)

    row(dom, item["name"]).get_by_role("button", name="Изменить").click()
    form = dom.locator("div.fixed.inset-0").filter(has=dom.get_by_placeholder("Чей, для чего"))
    form.locator("input[type=date]").fill(new_date.isoformat())
    form.get_by_placeholder("Чей, для чего").fill("изменено автотестом")
    with dom.expect_response(lambda r: r.url.endswith(f"{API}{item['id']}") and r.request.method == "PATCH") as patched:
        form.get_by_role("button", name="Сохранить").click()

    assert patched.value.ok, patched.value.text()
    saved = find_domain(dom, item["name"])
    assert (saved["expires_on"], saved["note"], saved["state"]) == (new_date.isoformat(), "изменено автотестом", "ok")
    expect(row(dom, item["name"])).to_contain_text(ru(new_date))


@pytest.mark.writes_data
def test_remove_domain(dom: Page, source):
    item = create_domain(dom, source)
    reload(dom)

    row(dom, item["name"]).get_by_role("button", name="Убрать").click()
    dialog = dom.get_by_role("dialog", name=f"Убрать {item['name']} из списка?")
    with dom.expect_response(lambda r: r.url.endswith(f"{API}{item['id']}") and r.request.method == "DELETE") as deleted:
        dialog.get_by_role("button", name="Да").click()

    assert deleted.value.ok
    expect(row(dom, item["name"])).to_have_count(0)
    assert find_domain(dom, item["name"]) is None


@pytest.mark.writes_data
def test_remove_can_be_cancelled(dom: Page, source):
    item = create_domain(dom, source)
    reload(dom)

    row(dom, item["name"]).get_by_role("button", name="Убрать").click()
    dom.get_by_role("dialog", name=re.compile("из списка\\?")).get_by_role("button", name="Отмена").click()

    expect(row(dom, item["name"])).to_have_count(1)
    assert find_domain(dom, item["name"])


@pytest.mark.writes_data
def test_import_list(dom: Page, source):
    existing = create_domain(dom, source, days=None)
    a, b = domain_name("imp-a-"), domain_name("imp-b-")
    reload(dom)
    sources_tab(dom)
    source_card(dom, source).get_by_role("button", name="Импорт списка").click()
    form = modal(dom, f"Импорт в {source['name']}")

    form.locator("textarea").fill(f"{a} 15.03.2027 450\n{b}\n{existing['name']} 01.01.2030\nне домен")
    with dom.expect_response(lambda r: r.url.endswith(f"{API}import")) as imported:
        form.get_by_role("button", name="Импортировать").click()

    result = imported.value.json()
    assert (result["added"], result["skipped"]) == (2, 1), result
    assert result["errors"] == ["Строка 4: не понял «не домен»"]
    expect(dom.get_by_text("Строка 4: не понял «не домен»")).to_be_visible()
    assert find_domain(dom, a)["expires_on"] == "2027-03-15"
    assert find_domain(dom, b)["expires_on"] is None
    assert find_domain(dom, existing["name"])["expires_on"] is None, "импорт не должен менять заведённый домен"


@pytest.mark.writes_data
def test_search(dom: Page, source):
    item = create_domain(dom, source, note="поиск-автотеста")
    other = create_domain(dom, source)
    reload(dom)
    box = dom.locator("main").get_by_placeholder("Поиск по домену или заметке")

    for query in (item["name"], "поиск-автотеста"):
        box.fill(query)
        expect(row(dom, item["name"]), query).to_have_count(1)
        expect(row(dom, other["name"]), query).to_have_count(0)


@pytest.mark.writes_data
def test_disconnect_source_keeps_domains(dom: Page, source):
    item = create_domain(dom, source)
    reload(dom)
    sources_tab(dom)

    source_card(dom, source).get_by_role("button", name="Отключить").click()
    dialog = dom.get_by_role("dialog", name=re.compile(rf"^Отключить «{re.escape(source['name'])}»\?"))
    expect(dialog).to_contain_text("Домены останутся в списке без источника.")
    with dom.expect_response(lambda r: r.url.endswith(f"sources/{source['id']}") and r.request.method == "DELETE") as deleted:
        dialog.get_by_role("button", name="Да").click()

    assert deleted.value.ok
    expect(dom.locator("main").get_by_text(source["name"])).to_have_count(0)
    kept = find_domain(dom, item["name"])
    assert kept and kept["source_id"] is None
