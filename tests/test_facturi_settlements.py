"""Блок 43. Facturi — расчёты: долги, обязательства, акты сверки, кредиты.

Снято 02.11.2026:
- «Долги по контрагентам» (/admin/debtors, ?type=payable) = GET /api/debtors?type=
  receivable|payable: плитки «Должников / Кредиторов», «Неоплаченных документов»,
  «Нам должны / Мы должны», «Просрочено» + «N док.», вкладки «Активные N / Игнор N»,
  строка «<контрагент> · N счёт · сумма · просрочка · дн. просрочки · Игнор». Считаются
  только отправленные накладные (черновик в долг не идёт). Клик по строке — карточка
  контрагента ?doc=counterparty:<id> (баланс «Нам должны / Мы должны», обороты);
- «Обязательства» = GET /api/reports/counterparty-balances: плитки «Дебиторская /
  Кредиторская / Авансы», выбранный контрагент — «Расчёт сальдо», «Счета к получению
  (дебиторские) / к оплате (кредиторские)», «Акт сверки». Фронт ещё дёргает
  /api/finance/obligations/ → 404 — решение юзера: не баг;
- «Акты сверки» = GET /api/reports/reconciliation-acts (debit / credit / closing):
  «За период начислено X, оплачено Y — по N контрагентам», колонки «Документы / Оплаты /
  Начислено / Оплачено / Сальдо на конец», ссылка «Акт» → /admin/obligations/reconciliation
  ?cp=&from=&to= в новой вкладке (акт по-румынски, = /reports/reconciliation-act); PDF —
  /reports/reconciliation-act.pdf?counterparty_ids=…;
- «Кредиты и займы» (/admin/credits): «Новый кредит» → «Новый кредит/займ» (Название *,
  Тип «Взятый кредит / Выданный займ», Валюта, Основная сумма *, Ставка, Дата начала *,
  Дата окончания *, Контрагент, Организация, Заметки); тип в API — taken / given;
  сводка /credits/summary/overview.
  Удаления у кредитов нет — только «закрыть»: тестовые копятся в «Закрытых».
Данные теста — свой контрагент (тип «Оба»), своя накладная клиенту и накладная поставщика;
после теста накладные отменяются, платежи удаляются, контрагент выключается.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.facturi import (QA_PREFIX, field, fx_delete, fx_get, fx_json, fx_post, fx_put, modal, open_fx, pick,
                             stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()


def money(amount: float) -> str:
    whole, frac = f"{abs(amount):,.2f}".split(".")
    return r"\s".join(re.escape(p) for p in whole.split(",")) + f",{frac}"


class Settle:
    """Свой контрагент, своя накладная клиенту (просрочена на 5 дней) и поставщика."""

    def __init__(self, page: Page):
        self.page = page
        tag = stamp()
        self.cp = fx_json(fx_post(page, "/counterparties/", {"name": f"{QA_PREFIX} Расчёты {tag}", "type": "both"}))
        self.acc = fx_json(fx_post(page, "/bank-accounts/", {"name": f"{QA_PREFIX} Расчёты {tag}", "type": "bank", "currency": "MDL"}))
        self.invoices: list[int] = []
        self.credits: list[int] = []

    def invoice(self, kind="receivable", amount=1000, overdue_days=5) -> dict:
        due = TODAY - dt.timedelta(days=overdue_days) if overdue_days else TODAY + dt.timedelta(days=10)
        inv = fx_json(fx_post(self.page, "/invoices/", {
            "counterparty_id": self.cp["id"], "type": kind, "issue_date": str(due - dt.timedelta(days=15)),
            "due_date": str(due), "currency": "MDL",
            "lines": [{"description": f"{QA_PREFIX} расчёты", "quantity": 1, "unit_price": amount}]}))
        self.invoices.append(inv["id"])
        fx_json(fx_post(self.page, f"/invoices/{inv['id']}/send", {}))  # черновик в долг не идёт
        return inv

    def cleanup(self):
        p = self.page
        pays = fx_json(fx_get(p, "/payments/", {"counterparty_id": self.cp["id"], "limit": 200}))
        for pay in pays["items"] if isinstance(pays, dict) else pays:
            fx_delete(p, f"/payments/{pay['id']}")
        for inv_id in self.invoices:
            fx_post(p, f"/invoices/{inv_id}/cancel", {})
        for credit_id in self.credits:
            fx_post(p, f"/credits/{credit_id}/close", {})
        fx_post(p, f"/debtors/counterparty/{self.cp['id']}/ignore", {"ignored": False})
        fx_delete(p, f"/bank-accounts/{self.acc['id']}")
        fx_put(p, f"/counterparties/{self.cp['id']}", {"is_active": False})


@pytest.fixture
def s(fx_page: Page):
    data = Settle(fx_page)
    yield data
    data.cleanup()


def head(page: Page):
    return page.locator("header.sticky")


def debtor_row(page: Page, name: str):
    """Строка должника: кнопка с именем («Открыть карточку контрагента») + суммы и «Игнор» рядом."""
    name_btn = page.get_by_role("button", name=name, exact=True)
    return page.locator("main div").filter(has=name_btn).filter(has=page.get_by_text("Игнор", exact=True)).last


def open_debtor_card(page: Page, name: str):
    page.locator("main").get_by_role("button", name=name, exact=True).click()


def open_debtors(page: Page, kind: str = "receivable") -> dict:
    with page.expect_response(lambda r: f"/api/debtors?type={kind}" in r.url) as resp:
        open_fx(page, "/admin/debtors" + ("?type=payable" if kind == "payable" else ""))
    return resp.value.json()


def mine(data: dict, cp_id: int) -> dict:
    return next(i for i in data["items"] if i["counterparty_id"] == cp_id)


# ───────────────────────── долги ─────────────────────────

# Кейс: «Нам должны» — плитки и своя строка совпадают с API
@pytest.mark.smoke
def test_debtors_receivable_match_api(fx_page: Page, s):
    s.invoice(amount=1000, overdue_days=5)
    data = open_debtors(fx_page)
    summary, me = data["summary"], mine(data, s.cp["id"])
    main = fx_page.locator("main")

    expect(main).to_contain_text(re.compile(r"Должников\s*" + str(summary["debtor_count"])))
    expect(main).to_contain_text(re.compile(r"Неоплаченных документов\s*" + str(summary["invoice_count"])))
    expect(main).to_contain_text(re.compile(r"Нам должны\s*" + money(summary["outstanding_total"])))
    expect(main).to_contain_text(re.compile(r"Просрочено\s*" + money(summary["overdue_total"]) + r"\s*" + str(summary["overdue_count"]) + r" док\."))
    expect(main.get_by_role("button", name=f"Активные {data['active_count']}")).to_be_visible()

    assert (me["invoice_count"], me["outstanding"], me["overdue"], me["max_days_overdue"]) == (1, 1000, 1000, 5), me
    row = debtor_row(fx_page, s.cp["name"])
    expect(row).to_contain_text("1 счёт")
    expect(row).to_contain_text(re.compile(money(1000)))
    expect(row).to_contain_text("5 дн. просрочки")


# Кейс: Черновик накладной в долг не идёт
def test_draft_invoice_not_a_debt(fx_page: Page, s):
    draft = fx_json(fx_post(fx_page, "/invoices/", {
        "counterparty_id": s.cp["id"], "type": "receivable", "issue_date": str(TODAY), "due_date": str(TODAY),
        "currency": "MDL", "lines": [{"description": "черновик", "quantity": 1, "unit_price": 50}]}))
    s.invoices.append(draft["id"])
    data = fx_json(fx_get(fx_page, "/debtors", {"type": "receivable"}))
    assert s.cp["id"] not in [i["counterparty_id"] for i in data["items"]], "черновик попал в долги"


# Кейс: «Мы должны» — накладная поставщика
def test_debtors_payable(fx_page: Page, s):
    s.invoice(kind="payable", amount=400, overdue_days=0)
    data = open_debtors(fx_page, "payable")
    me = mine(data, s.cp["id"])
    main = fx_page.locator("main")

    assert (me["outstanding"], me["overdue"]) == (400, 0), me
    expect(main).to_contain_text("Накладные поставщиков минус наши платежи им")
    expect(main).to_contain_text(re.compile(r"Кредиторов\s*" + str(data["summary"]["debtor_count"])))
    expect(main).to_contain_text(re.compile(r"Мы должны\s*" + money(data["summary"]["outstanding_total"])))
    expect(debtor_row(fx_page, s.cp["name"])).to_contain_text(re.compile(money(400)))


# Кейс: Частичная оплата накладной уменьшает долг
@pytest.mark.writes_data
def test_partial_payment_reduces_debt(fx_page: Page, s):
    inv = s.invoice(amount=1000, overdue_days=5)
    pay = fx_json(fx_post(fx_page, "/payments/", {"type": "income", "amount": 300, "currency": "MDL", "date": str(TODAY),
                                                  "counterparty_id": s.cp["id"], "bank_account_id": s.acc["id"],
                                                  "invoice_id": inv["id"], "description": f"{QA_PREFIX} частичная"}))
    fx_json(fx_post(fx_page, f"/payments/{pay['id']}/complete", {}))

    data = open_debtors(fx_page)
    assert mine(data, s.cp["id"])["outstanding"] == 700, mine(data, s.cp["id"])
    expect(debtor_row(fx_page, s.cp["name"])).to_contain_text(re.compile(money(700)))


# Кейс: «Игнор» — должник уходит во вкладку «Игнор» и возвращается
@pytest.mark.writes_data
def test_debtor_ignore(fx_page: Page, s):
    s.invoice()
    data = open_debtors(fx_page)
    row = debtor_row(fx_page, s.cp["name"])
    with fx_page.expect_response(lambda r: f"/api/debtors/counterparty/{s.cp['id']}/ignore" in r.url) as resp:
        row.get_by_text("Игнор", exact=True).last.click()
    assert resp.value.ok, resp.value.text()

    after = fx_json(fx_get(fx_page, "/debtors", {"type": "receivable"}))
    assert after["ignored_count"] == data["ignored_count"] + 1
    assert mine(after, s.cp["id"])["is_ignored"] is True
    main = fx_page.locator("main")
    main.get_by_role("button", name=re.compile(r"^Игнор \d+$")).click()
    expect(debtor_row(fx_page, s.cp["name"])).to_be_visible()

    fx_json(fx_post(fx_page, f"/debtors/counterparty/{s.cp['id']}/ignore", {"ignored": False}))
    assert mine(fx_json(fx_get(fx_page, "/debtors", {"type": "receivable"})), s.cp["id"])["is_ignored"] is False


# Кейс: Строка должника открывает карточку контрагента с балансом
def test_debtor_opens_counterparty(fx_page: Page, s):
    s.invoice(amount=1000)
    s.invoice(kind="payable", amount=400, overdue_days=0)
    open_debtors(fx_page)
    open_debtor_card(fx_page, s.cp["name"])

    expect(fx_page).to_have_url(re.compile(rf"doc=counterparty%3A{s.cp['id']}"))
    card = fx_page.locator("main").filter(has_text="Обороты:")
    expect(card).to_contain_text(re.compile(money(1000) + r"\s*Нам должны"))
    expect(card).to_contain_text(re.compile(money(400) + r"\s*Мы должны"))
    expect(card).to_contain_text(re.compile(r"выставлено им " + money(1000) + r" · выставлено нам " + money(400)))


# ───────────────────────── обязательства ─────────────────────────

# Кейс: «Обязательства» — плитки и расчёт сальдо своего контрагента = API
def test_obligations_match_api(fx_page: Page, s):
    s.invoice(amount=1000)
    s.invoice(kind="payable", amount=400, overdue_days=0)
    with fx_page.expect_response(lambda r: "/api/reports/counterparty-balances" in r.url) as resp:
        open_fx(fx_page, "/admin/obligations")
    items = resp.value.json()["items"]
    receivable = sum(i["receivable_open_mdl"] for i in items)
    payable = sum(i["payable_open_mdl"] for i in items)
    me = next(i for i in items if i["counterparty_id"] == s.cp["id"])
    main = fx_page.locator("main")

    expect(main).to_contain_text(re.compile(r"Дебиторская\s*" + money(receivable)))
    expect(main).to_contain_text(re.compile(r"Кредиторская\s*" + money(payable)))
    assert (me["receivable_open_mdl"], me["payable_open_mdl"], me["current_balance"]) == (1000, 400, 600), me

    main.get_by_role("button").filter(has_text=s.cp["name"]).first.click()
    expect(main).to_contain_text(re.compile(r"Расчёт сальдо\s*\+" + money(600)))
    expect(main).to_contain_text(re.compile(r"Счета к получению \(дебиторские\)\s*\+" + money(1000)))
    expect(main).to_contain_text(re.compile(r"Счета к оплате \(кредиторские\)\s*[−-]" + money(400)))
    expect(main.get_by_text("Акт сверки").first).to_be_visible()


# ───────────────────────── акты сверки ─────────────────────────

# Кейс: «Акты сверки» за год — итоги и своя строка = API
def test_reconciliation_acts_match_api(fx_page: Page, s):
    s.invoice(amount=1000)
    s.invoice(kind="payable", amount=400, overdue_days=0)
    with fx_page.expect_response(lambda r: "/api/reports/reconciliation-acts" in r.url) as resp:
        open_fx(fx_page, "/admin/reconciliation-acts")
    data = resp.value.json()
    t = data["totals"]
    main = fx_page.locator("main")

    expect(main).to_contain_text(re.compile(
        r"За период начислено " + money(t["debit"]) + r", оплачено " + money(t["credit"]) + rf" — по {data['total']} контрагентам"))
    me = next(r for r in data["rows"] if r["counterparty_id"] == s.cp["id"])
    assert (me["documents"], me["debit"], me["credit"], me["closing"]) == (2, 1000, 400, 600), me
    row = main.locator("tr").filter(has_text=s.cp["name"])
    expect(row).to_contain_text(re.compile(money(1000)))
    expect(row).to_contain_text(re.compile(money(400)))
    expect(row).to_contain_text(re.compile(money(600)))


# Кейс: Акт сверки — PDF по контрагенту
def test_reconciliation_act_pdf(fx_page: Page, s):
    s.invoice(amount=1000)
    resp = fx_get(fx_page, "/reports/reconciliation-act.pdf", {
        "counterparty_ids": s.cp["id"], "date_from": f"{TODAY.year}-01-01", "date_to": str(TODAY)})
    assert resp.ok, resp.status
    assert resp.body()[:4] == b"%PDF"


# Кейс: «Акт» в строке ведёт на акт сверки контрагента, акт = API
def test_reconciliation_act_page(fx_page: Page, s):
    inv = s.invoice(amount=1000)
    open_fx(fx_page, "/admin/reconciliation-acts")
    link = fx_page.locator("main tr").filter(has_text=s.cp["name"]).get_by_role("link", name=re.compile("^Акт"))
    href = link.get_attribute("href")
    assert href.startswith(f"/admin/obligations/reconciliation?cp={s.cp['id']}&from="), href
    expect(link).to_have_attribute("target", "_blank")

    with fx_page.expect_response(lambda r: "/api/reports/reconciliation-act?" in r.url) as resp:
        open_fx(fx_page, href)
    act = resp.value.json()
    main = fx_page.locator("main")
    assert [r["doc_number"] for r in act["rows"]] == [inv["number"]], act["rows"]
    assert act["closing"]["net"] == 1000, act["closing"]
    expect(main).to_contain_text(f"Factura {inv['number']}")
    expect(main).to_contain_text(re.compile(r"Sold final\s*" + money(1000)))
    expect(main).to_contain_text(re.compile(re.escape(s.cp["name"]) + r" datorează " + money(1000)))
    expect(main.get_by_role("button", name="Скачать PDF")).to_be_visible()


# ───────────────────────── кредиты ─────────────────────────

def open_credits(page: Page):
    with page.expect_response(lambda r: "/api/credits/summary/overview" in r.url) as resp:
        open_fx(page, "/admin/credits")
    return resp.value.json()


# Кейс: Новый кредит через форму
@pytest.mark.writes_data
def test_credit_create_via_form(fx_page: Page, s):
    name = f"{QA_PREFIX} Кредит {stamp()}"
    end = TODAY.replace(year=TODAY.year + 1)
    open_credits(fx_page)
    head(fx_page).get_by_role("button", name="Новый кредит", exact=True).click()
    form = modal(fx_page, "Новый кредит/займ")
    create = form.get_by_role("button", name="Создать", exact=True)
    expect(create).to_be_disabled()

    field(form, "Название").fill(name)
    field(form, "Основная сумма").fill("12000")
    field(form, "Ставка (% годовых)").fill("10")
    field(form, "Дата окончания").fill(str(end))
    pick(fx_page, form, "Контрагент", re.compile(re.escape(s.cp["name"])))
    with fx_page.expect_response(lambda r: r.url.endswith("/api/credits/") and r.request.method == "POST") as resp:
        create.click()
    assert resp.value.ok, resp.value.text()
    credit = resp.value.json()
    s.credits.append(credit["id"])

    assert (credit["name"], credit["type"], credit["principal_amount"], credit["interest_rate"], credit["end_date"],
            credit["counterparty_id"]) == (name, "taken", 12000, 10, str(end), s.cp["id"]), credit
    expect(fx_page.locator("main").get_by_text(name)).to_be_visible()


# Кейс: Кредит — платёж по телу и процентам, затем «закрыт»
@pytest.mark.writes_data
def test_credit_payment_and_close(fx_page: Page, s):
    before = fx_json(fx_get(fx_page, "/credits/summary/overview"))
    credit = fx_json(fx_post(fx_page, "/credits/", {
        "name": f"{QA_PREFIX} Займ {stamp()}", "type": "taken", "currency": "MDL", "principal_amount": 5000,
        "interest_rate": 12, "start_date": str(TODAY), "end_date": str(TODAY.replace(year=TODAY.year + 1)),
        "counterparty_id": s.cp["id"]}))
    s.credits.append(credit["id"])
    after = fx_json(fx_get(fx_page, "/credits/summary/overview"))
    assert after != before, "сводка кредитов не изменилась после нового кредита"

    pay = fx_post(fx_page, f"/credits/{credit['id']}/payments", {
        "payment_date": str(TODAY), "principal_amount": 1000, "interest_amount": 50, "notes": "автотест"})
    assert pay.ok, pay.text()
    card = fx_json(fx_get(fx_page, f"/credits/{credit['id']}"))
    payments = card.get("payments") or []
    assert any(p.get("principal_amount") == 1000 and p.get("interest_amount") == 50 for p in payments), payments

    closed = fx_post(fx_page, f"/credits/{credit['id']}/close", {})
    assert closed.ok, closed.text()
    assert fx_json(fx_get(fx_page, f"/credits/{credit['id']}"))["status"] == "closed"

    open_credits(fx_page)
    main = fx_page.locator("main")
    main.get_by_role("button", name="Закрытые", exact=True).click()
    expect(main.get_by_text(credit["name"])).to_be_visible()
    main.get_by_role("button", name="Активные", exact=True).click()
    expect(main.get_by_text(credit["name"])).to_have_count(0)
