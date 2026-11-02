"""Блок 45. Facturi — бюджеты: /admin/budgets.

Снято 02.11.2026:
- список = GET /api/budgets/ ({items, can_create}): «Название / Вид / Период / Разрез / План
  доходов / План расходов / Статус»; «Статусы», «Новый бюджет»;
- «Новый бюджет» — окно: Название, «Вид бюджета» (галочки «БДДС — движение денег» /
  «БДР — доходы и расходы»), «С месяца / По месяц» (input month), «Разбивка» (По дням …
  По годам), Направление, Проект, Счета, «Контролировать лимиты», Комментарий. В API
  kind = cashflow (БДДС) | pnl (БДР), control = off | warn;
- бюджет — /admin/budgets?id=<id>: плитки «Доходы / Расходы / Чистый поток — N из M по
  плану», сетка «Статья × период» (поля плана), «Сохранить план» → PUT /budgets/<id>/lines
  {lines: [{category_id, period: 'YYYY-MM-01', amount}]}; отчёт /budgets/<id>/report
  (rows: plan / fact / plan_total / fact_total / execution_pct); у БДДС факт — из денег,
  у БДР — из начислений; кнопки «На согласование», «Утвердить», «Заполнить», «БДР к этому
  бюджету» (смена статуса — окно с «Комментарием»; ячейка плана сохраняется по «Сохранить
  план», которая оживает после ухода фокуса с ячейки); «История»;
- удалить можно только черновик или закрытый бюджет (409 «Удалить можно черновик или
  закрытый бюджет») — уборка возвращает бюджет в черновик и удаляет.
Свои данные — статьи дохода и расхода (с «Типом»), банковский счёт, платёж; после теста
всё удаляется / выключается.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.facturi import (QA_PREFIX, field, fx_delete, fx_get, fx_json, fx_post, fx_put, modal, open_fx,
                             stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()
MONTH_START = TODAY.replace(day=1)
NEXT_MONTH = (MONTH_START + dt.timedelta(days=32)).replace(day=1)
P1, P2 = MONTH_START.isoformat(), NEXT_MONTH.isoformat()


def money(amount: float) -> str:
    whole, frac = f"{abs(amount):,.2f}".split(".")
    return r"\s".join(re.escape(p) for p in whole.split(",")) + f",{frac}"


class Budgets:
    def __init__(self, page: Page):
        self.page = page
        tag = stamp()
        self.tag = tag
        self.expense = fx_json(fx_post(page, "/categories/", {"name": f"{QA_PREFIX} Бюджет расход {tag}", "type": "expense", "pls_type": 2200}))
        self.income = fx_json(fx_post(page, "/categories/", {"name": f"{QA_PREFIX} Бюджет доход {tag}", "type": "income", "pls_type": 1100}))
        self.acc = fx_json(fx_post(page, "/bank-accounts/", {"name": f"{QA_PREFIX} Бюджет {tag}", "type": "bank", "currency": "MDL"}))
        self.budgets: list[int] = []
        self.payments: list[int] = []

    def budget(self, kind="cashflow", **data) -> dict:
        body = {"name": f"{QA_PREFIX} Бюджет {stamp()}", "kind": kind, "period_type": "month",
                "date_from": P1, "date_to": P2, "control": "warn", **data}
        bud = fx_json(fx_post(self.page, "/budgets/", body))
        self.budgets.append(bud["id"])
        return bud

    def plan(self, bud: dict, amount: float, cat=None, period=P1):
        cat = cat or self.expense
        return fx_json(self.page.request.put(f"https://app.facturi.md/api/budgets/{bud['id']}/lines",
                                             data={"lines": [{"category_id": cat["id"], "period": period, "amount": amount}]}))

    def expense_payment(self, amount: float) -> dict:
        pay = fx_json(fx_post(self.page, "/payments/", {
            "type": "expense", "amount": amount, "currency": "MDL", "date": str(TODAY), "bank_account_id": self.acc["id"],
            "category_id": self.expense["id"], "description": f"{QA_PREFIX} бюджет {self.tag}"}))
        fx_json(fx_post(self.page, f"/payments/{pay['id']}/complete", {}))
        self.payments.append(pay["id"])
        return pay

    def cleanup(self):
        p = self.page
        mine = [b["id"] for b in fx_json(fx_get(p, "/budgets/"))["items"] if b["name"].startswith(QA_PREFIX)]
        for bud_id in set(self.budgets) | set(mine):
            fx_post(p, f"/budgets/{bud_id}/status", {"status": "draft", "comment": "уборка автотеста"})
            fx_delete(p, f"/budgets/{bud_id}")
        for pay_id in self.payments:
            fx_delete(p, f"/payments/{pay_id}")
        fx_delete(p, f"/bank-accounts/{self.acc['id']}")
        for cat in (self.expense, self.income):
            fx_put(p, f"/categories/{cat['id']}", {"is_active": False})


@pytest.fixture
def b(fx_page: Page):
    data = Budgets(fx_page)
    yield data
    data.cleanup()


def head(page: Page):
    return page.locator("header.sticky")


def open_list(page: Page) -> list[dict]:
    with page.expect_response(lambda r: r.url.endswith("/api/budgets/") and r.request.method == "GET") as resp:
        open_fx(page, "/admin/budgets")
    return resp.value.json()["items"]


def open_budget(page: Page, bud: dict) -> dict:
    with page.expect_response(lambda r: f"/api/budgets/{bud['id']}/report" in r.url) as resp:
        open_fx(page, f"/admin/budgets?id={bud['id']}")
    return resp.value.json()


def new_budget(page: Page, name: str, kind_label: str):
    head(page).get_by_role("button", name="Новый бюджет", exact=True).click()
    form = modal(page, "Новый бюджет")
    field(form, "Название").fill(name)
    form.locator("label").filter(has_text=kind_label).locator("input").check()
    field(form, "С месяца").fill(MONTH_START.strftime("%Y-%m"))
    field(form, "По месяц").fill(NEXT_MONTH.strftime("%Y-%m"))
    form.get_by_role("button", name="По месяцам", exact=True).click()
    with page.expect_response(lambda r: r.url.endswith("/api/budgets/") and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    return resp.value.json()


# Кейс: Новый БДДС через форму
@pytest.mark.writes_data
def test_create_cashflow_budget(fx_page: Page, b):
    name = f"{QA_PREFIX} БДДС {stamp()}"
    open_list(fx_page)
    bud = new_budget(fx_page, name, "БДДС")
    b.budgets.append(bud["id"])

    assert (bud["name"], bud["kind"], bud["period_type"], bud["date_from"], bud["date_to"], bud["status"]) == (
        name, "cashflow", "month", P1, P2, "draft"), bud
    rows = open_list(fx_page)
    assert bud["id"] in [r["id"] for r in rows]
    row = fx_page.locator("main tr").filter(has_text=name)
    expect(row).to_contain_text("БДДС")
    expect(row).to_contain_text("Черновик")
    expect(row).to_contain_text("Вся компания")


# Кейс: Новый БДР через форму
@pytest.mark.writes_data
def test_create_pnl_budget(fx_page: Page, b):
    name = f"{QA_PREFIX} БДР {stamp()}"
    open_list(fx_page)
    bud = new_budget(fx_page, name, "БДР")
    b.budgets.append(bud["id"])
    assert bud["kind"] == "pnl", bud
    open_list(fx_page)
    expect(fx_page.locator("main tr").filter(has_text=name)).to_contain_text("БДР")


# Кейс: Список бюджетов — план доходов и расходов = API
def test_list_matches_api(fx_page: Page, b):
    bud = b.budget()
    b.plan(bud, 300)
    b.plan(bud, 1000, cat=b.income)
    rows = open_list(fx_page)
    me = next(r for r in rows if r["id"] == bud["id"])
    row = fx_page.locator("main tr").filter(has_text=bud["name"])
    for key in ("plan_income", "plan_expense"):
        if isinstance(me.get(key), (int, float)) and me[key]:
            expect(row).to_contain_text(re.compile(money(me[key])))
    expect(row).to_contain_text(re.compile(money(300)))
    expect(row).to_contain_text(re.compile(money(1000)))


# Кейс: План через сетку бюджета — «Сохранить план»
@pytest.mark.writes_data
def test_plan_via_grid(fx_page: Page, b):
    bud = b.budget()
    open_budget(fx_page, bud)
    row = fx_page.locator("main tr").filter(has=fx_page.get_by_text(b.expense["name"], exact=True))
    cell = row.locator("input").first
    cell.fill("450")
    cell.press("Tab")  # «Сохранить план» оживает, когда ячейка теряет фокус
    expect(fx_page.locator("main").get_by_role("button", name="Сохранить план (1)", exact=True)).to_be_enabled()
    with fx_page.expect_response(lambda r: f"/api/budgets/{bud['id']}/lines" in r.url and r.request.method == "PUT") as resp:
        fx_page.locator("main").get_by_role("button", name="Сохранить план (1)", exact=True).click()  # счётчик правок
    assert resp.value.ok, resp.value.text()

    report = fx_json(fx_get(fx_page, f"/budgets/{bud['id']}/report"))
    me = next(r for r in report["rows"] if r["category_id"] == b.expense["id"])
    assert me["plan"][P1] == 450 and me["plan_total"] == 450, me
    expect(fx_page.locator("main")).to_contain_text(re.compile(r"Расходы\s*[\d\s,]+\s*из\s*" + money(450) + r"\s*по плану"))


# Кейс: БДДС — факт из платежа, исполнение = API
def test_plan_vs_fact(fx_page: Page, b):
    bud = b.budget()
    b.plan(bud, 300)
    b.expense_payment(120)
    report = open_budget(fx_page, bud)
    me = next(r for r in report["rows"] if r["category_id"] == b.expense["id"])

    assert (me["plan_total"], me["fact_total"]) == (300, 120), me
    assert round(me["execution_pct"]) == 40, me
    main = fx_page.locator("main")
    expect(main).to_contain_text(re.compile(r"Расходы\s*" + money(report["totals"]["expense"]["fact_total"]) +
                                            r"\s*из\s*" + money(report["totals"]["expense"]["plan_total"]) + r"\s*по плану"))
    expect(main.locator("tr").filter(has=fx_page.get_by_text(b.expense["name"], exact=True))).to_contain_text("40")


# Кейс: Статусы — «На согласование» → «Утвердить», история
@pytest.mark.writes_data
def test_status_flow(fx_page: Page, b):
    bud = b.budget()
    open_budget(fx_page, bud)
    main = fx_page.locator("main")
    expect(main).to_contain_text("Черновик")

    for button, status, label in (("На согласование", "review", "На согласовании"), ("Утвердить", "approved", "Утверждён")):
        main.get_by_role("button", name=button, exact=True).click()
        # окно смены статуса: «Комментарий» и кнопка с тем же названием
        dialog = fx_page.locator("div.fixed.inset-0").filter(has=fx_page.get_by_text("Комментарий", exact=True)).last
        dialog.locator("textarea").fill(f"автотест: {label}")
        with fx_page.expect_response(lambda r: f"/api/budgets/{bud['id']}/status" in r.url) as resp:
            dialog.get_by_role("button", name=button, exact=True).click()
        assert resp.value.ok, resp.value.text()
        assert resp.value.request.post_data_json.get("comment") == f"автотест: {label}"
        assert fx_json(fx_get(fx_page, f"/budgets/{bud['id']}"))["status"] == status
        expect(main).to_contain_text(label)
    expect(main.locator("div, li").filter(has_text="История").last).to_contain_text("Утверждён")


# Кейс: Утверждённый бюджет не удаляется, черновик — удаляется
@pytest.mark.writes_data
def test_delete_rules(fx_page: Page, b):
    bud = b.budget()
    fx_json(fx_post(fx_page, f"/budgets/{bud['id']}/status", {"status": "approved", "comment": "автотест"}))
    resp = fx_delete(fx_page, f"/budgets/{bud['id']}")
    assert resp.status == 409, resp.status
    assert "Удалить можно черновик или закрытый бюджет" in resp.text()

    fx_json(fx_post(fx_page, f"/budgets/{bud['id']}/status", {"status": "draft", "comment": "автотест"}))
    assert fx_delete(fx_page, f"/budgets/{bud['id']}").status == 204
    b.budgets.remove(bud["id"])
    assert bud["id"] not in [x["id"] for x in fx_json(fx_get(fx_page, "/budgets/"))["items"]]


# Кейс: Утверждённый БДДС с контролем — проверка лимита по статье
def test_limit_check(fx_page: Page, b):
    bud = b.budget(control="warn")
    b.plan(bud, 300)
    fx_json(fx_post(fx_page, f"/budgets/{bud['id']}/status", {"status": "approved", "comment": "автотест"}))

    over = fx_json(fx_get(fx_page, "/budgets/check", {"category_id": b.expense["id"], "amount": 500, "date": str(TODAY), "currency": "MDL"}))
    within = fx_json(fx_get(fx_page, "/budgets/check", {"category_id": b.expense["id"], "amount": 100, "date": str(TODAY), "currency": "MDL"}))
    assert over["items"], f"превышение 500 при лимите 300 не замечено: {over}"
    assert len(within["items"]) <= len(over["items"]), (within, over)
