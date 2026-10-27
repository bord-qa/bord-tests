"""Блок 15. Финансы (только чтение): /admin/finance, /invoices, /debtors.

Снято 22.10.2026. Данные — из Facturi через /api/finance/embed/v1/*:
- «Обзор»: GET …/home → money (Деньги сейчас), receivable (Нам должны),
  payable (Мы должны), month (Месяц: пришло · ушло), day («на ДД.ММ.ГГГГ»),
  app_url («Открыть facturi»); «Кто должен больше всех» — …/debtors?side=
  receivable&limit=5, пусто — «Должников нет.»;
- «Документы»: …/invoices?side=&state=&limit=25&offset=0 (+q), в шапке
  «Нам должны / Мы должны», «Открытые / Просроченные / Оплаченные / Все»,
  поиск «Номер документа или контрагент» (по Enter, &q=), пусто — «Документов нет.»;
- «Долги»: …/debtors?side=&limit=50, в шапке те же «Нам/Мы должны» и сумма,
  пусто — «Никто не должен.» / «Компания никому не должна.»;
- главное меню страницы рисуется после загрузки — ждём по тексту, не по main.

В BORD QA финансовых данных нет (всё 0,00) — тесты сверяют экран с API и
работают и на пустых, и на заполненных данных. Писать в Facturi не будем.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_get
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

API = "/api/finance/embed/v1/"
FACTURI = "https://app.facturi.md/admin"


def lei(amount: float) -> re.Pattern:
    """«1 234,50 лей» с любым пробелом между тысячами (Intl ставит неразрывный)."""
    whole, frac = f"{amount:,.2f}".split(".")
    return re.compile(r"\s".join(re.escape(part) for part in whole.split(",")) + f",{frac}\\s+лей")


def header(page: Page):
    return page.locator("header").filter(has=page.get_by_role("link", name="Открыть в facturi"))


def goto(page: Page, path: str, api: str):
    with page.expect_response(lambda r: f"{API}{api}" in r.url) as resp:
        page.goto(path)
    settle(page)
    assert resp.value.ok, f"{api} → {resp.value.status}"
    return resp.value.json()


# Кейс: Обзор финансов совпадает с API
def test_overview_matches_api(auth_page: Page):
    home = goto(auth_page, "/admin/finance", "home")
    main = auth_page.locator("main")

    card = lambda title: main.locator("div, a").filter(has=auth_page.get_by_text(title, exact=True)).filter(has_text="лей").last
    expect(card("Деньги сейчас")).to_contain_text(lei(home["money"]["total_mdl"]))
    expect(card("Нам должны")).to_contain_text(lei(home["receivable"]["total_mdl"]))
    expect(card("Мы должны")).to_contain_text(lei(home["payable"]["total_mdl"]))
    month = card("Месяц")
    expect(month).to_contain_text(lei(home["month"]["net"]))
    expect(month).to_contain_text(re.compile(rf"пришло {lei(home['month']['cash_in']).pattern} · ушло {lei(home['month']['cash_out']).pattern}"))
    expect(main.get_by_role("link", name=re.compile("^Нам должны"))).to_have_attribute("href", "/admin/finance/debtors")

    day = dt.date.fromisoformat(home["day"])
    expect(auth_page.get_by_text(f"на {day:%d.%m.%Y}")).to_be_visible()
    expect(auth_page.get_by_role("link", name="Открыть facturi")).to_have_attribute("href", home["app_url"])


# Кейс: Крупнейшие должники в обзоре
def test_overview_top_debtors(auth_page: Page):
    goto(auth_page, "/admin/finance", "home")
    debtors = api_get(auth_page, f"{API}debtors?side=receivable&limit=5").json()
    block = auth_page.locator("main").get_by_role("heading", name="Кто должен больше всех").locator("xpath=../..")

    if debtors["total"] == 0:
        expect(block.get_by_text("Должников нет.")).to_be_visible()
    else:
        for item in debtors["items"]:
            expect(block).to_contain_text(item.get("name") or item.get("counterparty_name") or "")
    expect(block.get_by_role("link", name="Все долги")).to_have_attribute("href", "/admin/finance/debtors")
    expect(auth_page.locator("main").get_by_role("link", name="Документы и оплаты")).to_have_attribute("href", "/admin/finance/invoices")


# Кейс: Счета открываются на «Нам должны»
@pytest.mark.smoke
def test_invoices_open_receivable_by_default(auth_page: Page):
    with auth_page.expect_request(lambda r: f"{API}invoices" in r.url) as first:
        auth_page.goto("/admin/finance/invoices")
    params = first.value.url.split("?", 1)[1]
    assert "side=receivable" in params and "state=open" in params
    data = first.value.response().json()

    main = auth_page.locator("main")
    if not data["items"]:
        expect(main.get_by_text("Документов нет.")).to_be_visible()
    else:
        expect(main.locator("tbody tr, li")).to_have_count(len(data["items"]))
    expect(header(auth_page).get_by_role("link", name="Открыть в facturi")).to_have_attribute("href", f"{FACTURI}/invoices")
    expect(header(auth_page).get_by_role("link", name="К обзору финансов")).to_have_attribute("href", "/admin/finance")


# Кейс: Фильтры счетов уходят в API
def test_invoices_filters_hit_api(auth_page: Page):
    goto(auth_page, "/admin/finance/invoices", "invoices")
    bar = header(auth_page)
    seen = {}

    for label in ("Просроченные", "Оплаченные", "Все", "Открытые"):
        with auth_page.expect_response(lambda r: f"{API}invoices" in r.url) as resp:
            bar.get_by_role("button", name=label, exact=True).click()
        assert resp.value.ok, f"{label}: {resp.value.status}"
        seen[label] = re.search(r"state=(\w+)", resp.value.url).group(1) if "state=" in resp.value.url else "—"
    assert seen["Открытые"] == "open"
    assert len(set(seen.values())) == 4, f"у фильтров совпадают состояния: {seen}"

    with auth_page.expect_response(lambda r: f"{API}invoices" in r.url and "side=payable" in r.url) as payable:
        bar.get_by_role("button", name="Мы должны", exact=True).click()
    assert payable.value.ok

    with auth_page.expect_response(lambda r: f"{API}invoices" in r.url and "q=" in r.url) as searched:
        # поиск — форма, уходит по Enter
        bar.get_by_placeholder("Номер документа или контрагент").fill("QA-нет-такого")
        bar.get_by_placeholder("Номер документа или контрагент").press("Enter")
    assert searched.value.ok and searched.value.json()["items"] == []
    expect(auth_page.locator("main").get_by_text("Документов нет.")).to_be_visible()


# Кейс: Должники по обе стороны совпадают с API
@pytest.mark.parametrize(
    ("label", "side", "other", "empty"),
    [("Нам должны", "receivable", "Мы должны", "Никто не должен."),
     ("Мы должны", "payable", "Нам должны", "Компания никому не должна.")],
    ids=["нам должны", "мы должны"],
)
def test_debtors_sides_match_api(auth_page: Page, label, side, other, empty):
    goto(auth_page, "/admin/finance/debtors", "debtors")
    bar = header(auth_page)
    if side == "receivable":  # открыта по умолчанию — сначала уходим на другую сторону
        bar.get_by_role("button", name=other, exact=True).click()

    with auth_page.expect_response(lambda r: f"{API}debtors" in r.url and f"side={side}" in r.url) as resp:
        bar.get_by_role("button", name=label, exact=True).click()

    data = resp.value.json()
    assert data["side"] == side
    if data["total"] == 0:
        expect(auth_page.locator("main").get_by_text(empty)).to_be_visible()
    else:
        expect(auth_page.locator("main").locator("tbody tr, li")).to_have_count(len(data["items"]))
    home = api_get(auth_page, f"{API}home").json()
    expect(bar).to_contain_text(lei(home[side]["total_mdl"]))
    expect(bar.get_by_role("link", name="Открыть в facturi")).to_have_attribute("href", f"{FACTURI}/debtors")
