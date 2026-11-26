"""Блок 87. Facturi — «Договоры» (плагин contracts, /admin/contracts).

Снято 26.11.2026 (юзер 26.11: плагин в BORD QA можно включать на время тестов):
- договоры заводят и правят в «Договорах» (dms.bord.one), Facturi их только читает:
  GET /contracts/ {contracts: [...]}, создать здесь нельзя. В BORD QA ~10 договоров
  чужих прогонов DMS, все «Удалён в «Договорах»», стороны «нет среди контрагентов»;
- экран: «Договоров: N, документов к ним: M · с нарушениями: K», таблица «Договор /
  Сторона / Документы / Начислено / Оплачено / Долг / Пеня / Нарушения»; вкладки шапки
  «Все» / «С нарушениями» (?show=warnings) / «С просрочкой» (?show=overdue) / «С пеней»
  (?show=penalty) фильтруют на клиенте, пусто — «Под отбор ничего не попало»;
- «Без основания» (?tab=without) = GET /contracts/without-basis?date_from&date_to:
  документы периода без договора-основания и без отметки «Без договора»; периоды
  «Этот месяц» … «Прошлый год». «Подобрать основания» (POST /contracts/autoassign)
  НЕ жмём — перекладывает основания по всем документам организации;
- в карточке документа блок «Договор-основание»: «Изменить» → выпадашка «Подобрать
  автоматически…» / «Без договора» → «Сохранить» = PUT /contracts/basis/<kind>/<id>;
- без плагина экран объясняет, что плагин выключен и где включить.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.facturi import fx_get, fx_json, fx_put, open_fx
from tests.test_facturi_sales import Docs, doc_panel

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/contracts"


def head(page: Page):
    return page.locator("header.sticky")


@pytest.fixture
def plugin(fx_page: Page):
    """Включить / выключить плагин на время теста и вернуть как было."""
    open_fx(fx_page, "/admin/plugins")
    was = next(p for p in fx_json(fx_get(fx_page, "/plugins/")) if p["plugin_key"] == "contracts")["is_enabled"]

    def set_state(on: bool):
        assert fx_json(fx_put(fx_page, "/plugins/contracts", {"is_enabled": on}))["is_enabled"] is on

    yield set_state
    set_state(was)


@pytest.fixture
def contracts_on(plugin):
    plugin(True)


def open_contracts(page: Page) -> list[dict]:
    with page.expect_response(lambda r: re.search(r"/api/contracts/(\?|$)", r.url) is not None) as resp:
        open_fx(page, PAGE)
    return resp.value.json()["contracts"]


def has_warnings(c: dict) -> bool:
    return any(v["severity"] == "warning" for v in c["violations"])


# Кейс: Без плагина экран говорит, что «Договоры» выключены и где включить
def test_plugin_off_explains(fx_page: Page, plugin):
    plugin(False)
    open_fx(fx_page, PAGE)
    main = fx_page.locator("main")
    expect(main).to_contain_text("Плагин «Договоры» выключен")
    expect(main).to_contain_text("Плагины")
    expect(main.locator("tbody tr")).to_have_count(0)


# Кейс: Список договоров и сводка над таблицей = API
@pytest.mark.smoke
def test_list_matches_api(fx_page: Page, contracts_on):
    api = open_contracts(fx_page)
    main = fx_page.locator("main")
    docs = sum(c["documents_count"] for c in api)
    expect(main).to_contain_text(f"Договоров: {len(api)}, документов к ним: {docs}")
    expect(main).to_contain_text(f"с нарушениями: {sum(has_warnings(c) for c in api)}")
    if not api:
        return
    rows = main.locator("tbody tr")
    expect(rows).to_have_count(len(api))
    # строки в порядке API; названия повторяются (одноимённые «Сделка под договор»), поэтому по позиции
    for i, c in enumerate(api):
        row = rows.nth(i)
        expect(row).to_contain_text(c["title"])
        expect(row).to_contain_text(c["label"])
        expect(row).to_contain_text(c["party_name"])
        expect(row).to_contain_text(c["role_label"])
        if c["deleted"]:
            expect(row).to_contain_text("Удалён в «Договорах»")
        if c["counterparty_id"] is None:
            expect(row).to_contain_text("нет среди контрагентов")


# Кейс: Вкладки «С нарушениями / С просрочкой / С пеней / Все» фильтруют список
def test_filters(fx_page: Page, contracts_on):
    api = open_contracts(fx_page)
    rows = fx_page.locator("main tbody tr")
    empty = fx_page.locator("main").get_by_text("Под отбор ничего не попало")

    head(fx_page).get_by_role("button", name="С нарушениями", exact=True).click()
    expect(fx_page).to_have_url(re.compile(r"show=warnings"))
    warned = [c for c in api if has_warnings(c)]
    if warned:
        expect(rows).to_have_count(len(warned))
        for c in warned:
            expect(rows.filter(has_text=c["title"]).first).to_be_visible()
    else:
        expect(empty).to_be_visible()

    for tab, param in (("С просрочкой", "overdue"), ("С пеней", "penalty")):
        head(fx_page).get_by_role("button", name=tab, exact=True).click()
        expect(fx_page).to_have_url(re.compile(f"show={param}"))
        # у договоров BORD QA нет документов — ни просрочки, ни пени
        if all(c["documents_count"] == 0 for c in api):
            expect(empty).to_be_visible()
            expect(rows).to_have_count(0)

    head(fx_page).get_by_role("button", name="Все", exact=True).click()
    expect(fx_page).not_to_have_url(re.compile(r"show="))
    expect(rows).to_have_count(len(api))


def without_basis(page: Page, **params) -> dict:
    return fx_json(fx_get(page, "/contracts/without-basis", params or None))


def in_without(data: dict, kind: str, doc_id: int) -> bool:
    return any(i["kind"] == kind and i["id"] == doc_id for g in data["groups"] for i in g["items"])


# Кейс: «Без основания» — документы периода = API, период «Этот месяц» уходит в запрос
def test_without_basis_matches_api(fx_page: Page, contracts_on):
    open_fx(fx_page, PAGE)
    with fx_page.expect_response(lambda r: "/api/contracts/without-basis" in r.url) as resp:
        head(fx_page).get_by_role("button", name="Без основания", exact=True).click()
    expect(fx_page).to_have_url(re.compile(r"tab=without"))
    data = resp.value.json()
    items = [i for g in data["groups"] for i in g["items"]]
    assert len(items) == data["total"] == sum(data["counts"].values()), data["counts"]
    rows = fx_page.locator("main tbody tr")
    expect(rows).to_have_count(data["total"])
    for it in items[:10]:
        if it.get("number"):
            expect(rows.filter(has_text=it["number"]).first).to_be_visible()

    first = dt.date.today().replace(day=1).isoformat()
    with fx_page.expect_response(lambda r: "/api/contracts/without-basis" in r.url) as resp:
        screen_period = fx_page.locator("header.sticky, main").get_by_role("button", name="Этот месяц", exact=True)
        screen_period.first.click()
    assert f"date_from={first}" in resp.value.url, resp.value.url
    assert resp.value.ok


@pytest.fixture
def docs(fx_page: Page):
    d = Docs(fx_page)
    yield d
    d.cleanup()


# Кейс: «Без договора» в карточке акта — основание сохраняется, акт уходит из «Без основания»
@pytest.mark.writes_data
def test_act_marked_without_contract(fx_page: Page, contracts_on, docs):
    act = docs.act()
    assert in_without(without_basis(fx_page), "act", act["id"]), "новый акт должен быть в «Без основания»"
    basis = fx_json(fx_get(fx_page, f"/contracts/basis/act/{act['id']}"))
    assert basis["plugin_enabled"] and basis["contract_id"] is None, basis
    assert any(v["code"] == "no_basis" for v in basis["violations"]), basis

    open_fx(fx_page, f"/admin/acts?doc=act%3A{act['id']}")
    panel = doc_panel(fx_page, act["number"])
    expect(panel.get_by_text("Договор-основание не проставлен.")).to_be_visible()
    section = panel.locator("xpath=.//div[contains(@class,'font-semibold')]"
                            "[starts-with(normalize-space(.),'Договор-основание')]/..").first
    section.get_by_role("button", name="Изменить").click()
    section.locator("button[aria-haspopup=listbox]").click()
    fx_page.get_by_role("option", name="Без договора", exact=True).click()
    with fx_page.expect_response(lambda r: r.url.endswith(f"/api/contracts/basis/act/{act['id']}")
                                 and r.request.method == "PUT") as resp:
        section.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()

    card = doc_panel(fx_page, act["number"])
    expect(card).to_contain_text("без договора (намеренно)")
    expect(card).not_to_contain_text("Договор-основание не проставлен.")
    after = fx_json(fx_get(fx_page, f"/contracts/basis/act/{act['id']}"))
    assert not any(v["code"] == "no_basis" for v in after["violations"]), after
    assert not in_without(without_basis(fx_page), "act", act["id"]), "акт «Без договора» остался в «Без основания»"
