"""Блок 44. Facturi — отчёты: /admin/reports?report=<код> (10 отчётов).

Снято 02.11.2026 — каждый отчёт строится одним запросом:
- pnl «Прибыли и убытки» — /reports/pnl (rows: label, values по месяцам, saldo), баннер
  «„Без статьи“ N тыс. MDL — не в маржинальности» (статья без «Типа» — раздела ПиУ);
  P&L строится по документам: платёж без накладной в нём не виден (он в «Расходе без
  документа» после 30 дней ожидания);
- pnl_simple «Упрощённый P&L» — /reports/pnl/simple (lines по направлениям, totals,
  grand_total: income / expense / profit), в ячейке «доходы / расходы / прибыль»;
- breakeven «Точка безубыточности» — /reports/breakeven (status, message);
- cashflow «Денежный поток» — /reports/cashflow (кассовый метод: завершённые платежи);
- cash_plan «План денег» — /reports/cash-plan?weeks=8 (scenarios as_is / … по неделям);
- balance_dynamics «Динамика остатков» — /reports/balance-dynamics (accounts, periods);
- allocation «Распределение» — /reports/cash-allocation (allocated / unallocated);
- taxes «Налоги» — /reports/taxes?month= (regime, revenue, vat);
- business_lines «По направлениям» — /reports/by-business-line;
- expenses_no_doc «Расход без документа» — /reports/expenses-without-document.
Свои данные теста — статьи дохода и расхода, банковский счёт, бизнес-линия, контрагент,
отправленная накладная со статьёй и направлением, завершённые приход и расход; после
теста всё отменяется / удаляется / выключается. Чужие данные BORD QA (накладная
«QA Buyer RU», поручение «Trezoreria») в отчётах есть — сверяем экран с API, а свои
строки — со своими суммами.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, Response, expect

from helpers.facturi import Books, open_fx

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()
YEAR_FROM, YEAR_TO = f"{TODAY.year}-01-01", f"{TODAY.year}-12-31"
MONTH = TODAY.strftime("%Y-%m")

REPORTS = {
    "pnl": ("Прибыли и убытки", "/api/reports/pnl?"),
    "pnl_simple": ("Упрощённый P&L", "/api/reports/pnl/simple"),
    "breakeven": ("Точка безубыточности", "/api/reports/breakeven"),
    "cashflow": ("Денежный поток", "/api/reports/cashflow"),
    "cash_plan": ("План денег", "/api/reports/cash-plan"),
    "balance_dynamics": ("Динамика остатков", "/api/reports/balance-dynamics"),
    "allocation": ("Распределение", "/api/reports/cash-allocation"),
    "taxes": ("Налоги", "/api/reports/taxes"),
    "business_lines": ("По направлениям", "/api/reports/by-business-line"),
    "expenses_no_doc": ("Расход без документа", "/api/reports/expenses-without-document"),
}


def money(amount: float) -> str:
    whole, frac = f"{abs(amount):,.2f}".split(".")
    return r"\s".join(re.escape(p) for p in whole.split(",")) + f",{frac}"


@pytest.fixture
def books(fx_page: Page):
    data = Books(fx_page)
    yield data
    data.cleanup()


def open_report(page: Page, code: str, query: str = "") -> dict:
    title, api = REPORTS[code]
    with page.expect_response(lambda r: api in r.url and r.request.method == "GET") as resp:
        open_fx(page, f"/admin/reports?report={code}{query}")
    assert resp.value.ok, f"{api} → {resp.value.status}"
    expect(page).to_have_title(f"{title} — Facturi")
    return resp.value.json()


# Кейс: Отчёт открывается и строится без ошибок
@pytest.mark.parametrize("code", list(REPORTS), ids=[v[0] for v in REPORTS.values()])
def test_report_opens(fx_page: Page, code):
    errors: list[str] = []

    def on_response(resp: Response):
        if "/api/" in resp.url and resp.status >= 500:
            errors.append(f"{resp.status} {resp.url}")

    fx_page.on("response", on_response)
    fx_page.on("pageerror", lambda e: errors.append(f"JS: {e}"))
    open_report(fx_page, code)
    fx_page.wait_for_timeout(1_000)
    assert not errors, errors
    expect(fx_page.locator("main").get_by_text("Application error")).to_have_count(0)


# Кейс: Прибыли и убытки — своя статья выручки с суммой накладной
def test_pnl_own_category(fx_page: Page, books):
    data = open_report(fx_page, "pnl")
    # статья с «Типом» 1100 встаёт строкой в «Выручку»; без типа ушла бы в «Без статьи»
    row = next((r for r in data["rows"] if r.get("category_id") == books.income_cat["id"]), None)
    assert row, [r["label"] for r in data["rows"]]
    assert row["parent"] == "100_1100", row
    assert row["saldo"] == 500, row
    # значение месяца — по направлениям: {<id линии>: …, "null": …, "total": …}
    assert row["values"][MONTH]["total"] == 500, row["values"][MONTH]
    assert row["values"][MONTH][str(books.line["id"])] == 500, row["values"][MONTH]

    line = fx_page.locator("main tr").filter(has=fx_page.get_by_text(books.income_cat["name"], exact=True)).first
    expect(line).to_contain_text(re.compile(money(500)))


# Кейс: Упрощённый P&L — итоги месяца и года = API
def test_pnl_simple_matches_api(fx_page: Page, books):
    data = open_report(fx_page, "pnl_simple")
    month = data["totals"][MONTH]
    assert month["income"] >= 500, month
    total_row = fx_page.locator("main tr").filter(has=fx_page.get_by_text("Итого", exact=True)).last
    grand = data["grand_total"]
    expect(total_row).to_contain_text(re.compile(money(grand["income"])))
    if grand["profit"]:
        expect(total_row).to_contain_text(re.compile(money(grand["profit"])))
    line = next(l for l in data["lines"] if l.get("business_line_id") == books.line["id"])
    assert line["total"]["income"] == 500, line
    expect(fx_page.locator("main tr").filter(has_text=books.line["name"])).to_contain_text(re.compile(money(500)))


# Кейс: Денежный поток — свои приход и расход по статьям, итоги = API
def test_cashflow_matches_api(fx_page: Page, books):
    data = open_report(fx_page, "cashflow", f"&date_from={YEAR_FROM}&date_to={TODAY}")
    names_in = {i["category_name"]: i for i in data["income"]["items"]}
    names_out = {i["category_name"]: i for i in data["expense"]["items"]}
    assert books.income_cat["name"] in names_in, list(names_in)
    assert books.expense_cat["name"] in names_out, list(names_out)
    assert names_in[books.income_cat["name"]]["values"].get(MONTH) == 500

    main = fx_page.locator("main")
    expect(main.locator("tr").filter(has_text=books.income_cat["name"]).first).to_contain_text(re.compile(money(500)))
    expect(main.locator("tr").filter(has_text=books.expense_cat["name"]).first).to_contain_text(re.compile(money(120)))
    inc_total = sum(v for v in data["income"]["subtotal"].values() if isinstance(v, (int, float)))
    expect(main.locator("tr").filter(has_text="Итого доходы")).to_contain_text(re.compile(money(inc_total)))


# Кейс: Налоги — режим и выручка месяца = API
def test_taxes_match_api(fx_page: Page, books):
    data = open_report(fx_page, "taxes")
    main = fx_page.locator("main")
    expect(main).to_contain_text(f"Режим: {data['regime']['country_name']} · {data['regime']['title']}")
    expect(main).to_contain_text(re.compile(r"Выручка месяца без НДС:\s*" + money(data["revenue"]) + r"\s*MDL"))
    assert data["revenue"] >= 500, data["revenue"]
    if data["vat"]["applicable"]:
        expect(main).to_contain_text(re.compile(r"Исходящий НДС \(продажи\)\s*" + money(data["vat"]["output_vat"])))


# Кейс: План денег — недели «Как есть» = API
def test_cash_plan_matches_api(fx_page: Page):
    data = open_report(fx_page, "cash_plan")
    weeks = data["scenarios"]["as_is"]["weeks"]
    rows = fx_page.locator("main tbody tr").filter(has_text=re.compile(r"^\s*\d+ \S+ — \d+ \S+"))  # «2 нояб. — 8 нояб.»; \w в JS — только латиница
    expect(rows).to_have_count(len(weeks))
    for i, w in enumerate(weeks):
        end = w.get("end_mdl", w.get("end", w.get("closing")))
        if isinstance(end, (int, float)):
            expect(rows.nth(i)).to_contain_text(re.compile(money(end)))
    main = fx_page.locator("main")
    expect(main).to_contain_text(re.compile(r"Старт — подтверждённые остатки\s*" + money(data["start"]["confirmed_mdl"])))
    main.get_by_role("button", name="Без новых продаж", exact=True).click()
    expect(rows).to_have_count(len(data["scenarios"].get("no_new_sales", data["scenarios"]["as_is"])["weeks"]))


# Кейс: Динамика остатков — свой счёт с остатком = API
def test_balance_dynamics_own_account(fx_page: Page, books):
    data = open_report(fx_page, "balance_dynamics")
    me = next(a for a in data["accounts"] if a["id"] == books.acc["id"])
    assert me["balance"] == 500 - 120, me
    total = sum(a["balance_base"] for a in data["accounts"] if a.get("selected", True))
    expect(fx_page.locator("main")).to_contain_text(re.compile(r"Сейчас\s*" + money(total) + r"\s*MDL"))


# Кейс: Распределение — привязано / не привязано = API
def test_allocation_matches_api(fx_page: Page, books):
    data = open_report(fx_page, "allocation", f"&date_from={YEAR_FROM}&date_to={TODAY}")
    main = fx_page.locator("main")
    expect(main).to_contain_text(re.compile(r"Привязано к контрагентам\s*" + money(data["allocated_total"])))
    expect(main).to_contain_text(re.compile(r"Не привязано к контрагентам\s*" + money(data["unallocated_total"])))
    assert data["totals"]["income"] >= 500 and data["totals"]["expense"] >= 120, data["totals"]


# Кейс: Точка безубыточности — сообщение и статус = API
def test_breakeven_matches_api(fx_page: Page):
    data = open_report(fx_page, "breakeven")
    if data.get("message"):
        expect(fx_page.locator("main")).to_contain_text(data["message"][:60])
    assert data["status"], data


# Кейс: Расход без документа — итоги = API
def test_expenses_without_document_matches_api(fx_page: Page):
    data = open_report(fx_page, "expenses_no_doc")
    main = fx_page.locator("main")
    expect(main).to_contain_text(re.compile(r"Всего без документа\s*" + money(data["total_mdl"])))
    expect(main).to_contain_text(f"{data['count']} платеж")
    expect(main).to_contain_text(re.compile(r"Уже в отчёте с пометкой\s*" + money(data["posted_mdl"])))
    expect(main).to_contain_text(re.compile(r"Ещё не в отчёте\s*" + money(data["unposted_mdl"])))


# Кейс: По направлениям — своя бизнес-линия с доходом и расходом
def test_business_lines_own_line(fx_page: Page, books):
    data = open_report(fx_page, "business_lines", f"&date_from={YEAR_FROM}&date_to={TODAY}")
    me = next((i for i in data["items"] if i["business_line_id"] == books.line["id"]), None)
    assert me, [i["name"] for i in data["items"]]
    assert me["income"] > 0 or me["expense"] > 0, me
    expect(fx_page.locator("main").get_by_text(books.line["name"]).first).to_be_visible()
