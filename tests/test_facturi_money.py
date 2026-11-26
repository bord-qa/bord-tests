"""Блок 42. Facturi — деньги: платежи, оплата счёта, импорт выписки, регулярные, платёжный календарь.

Снято 02.11.2026:
- «Деньги» (/admin/payments) = GET /api/payments/ + /payments/status-counts: вкладки
  «Все / Черновик / Ожидает / Завершён / Отменён», «Создать платёж» → окно «Платёж ·
  создание» (Название, Сумма *, Тип * Приход/Расход, Валюта, Дата, Проведение,
  Контрагент, Банковский счёт, Категория, Бизнес-линия, «Счёт на оплату (основание)», …);
  платёж из API создаётся черновиком, «complete» → завершён. Удаляются любые платежи;
- оплата счёта на оплату: в карточке счёта «Создать на основе» → «Оплата» открывает
  ту же форму, уже заполненную (сумма, контрагент, основание). Счёт становится
  «Оплачен», когда сумма платежей (и черновиков) дошла до итога; у контрагента такие
  деньги идут авансом — учётный документ здесь накладная, не счёт;
- выписка файлом (/admin/bank-integrations?tab=statements): CSV «Дата;Описание;Приход;
  Расход;Контрагент;IBAN» разбирается в предпросмотр /bank-integrations/statements/<id>
  («Приход / Расход», «Счёт в системе», «Новые / Дубли», «Загрузить операции (N)»);
  без остатков в файле — предупреждение «сверить полноту … невозможно». Отмена —
  DELETE /statement-imports/<id>, но проведённую выписку отменить нельзя («Выписка уже
  загружена — отменить её нельзя») — проводит только один тест, его выписки копятся
  в списке. Тот же файл второй раз — «Этот файл уже загружали» (duplicate=true, та же
  выписка). «Распознавание ИИ» не используем;
- «Регулярные» (/admin/recurring) — «Новый повторяющийся платёж», удаляется;
- «Платёжный календарь» — только сверка с API: в BORD QA висит чужое поручение
  «Trezoreria de Stat (impozite)», его не трогаем, своих поручений в банк не отправляем.
Тесты работают только со своим банковским счётом (чужой «QA MDL» не трогаем) и своим
контрагентом; платежи, счета, выписки, регулярные и сам счёт удаляются после теста.
"""
import datetime as dt
import os
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.facturi import (screen, QA_PREFIX, confirm, maybe_confirm, field, fx_delete, fx_get, fx_json, fx_post, fx_put, modal,
                             open_fx, pick, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today().isoformat()
DUE = (dt.date.today() + dt.timedelta(days=14)).isoformat()


def money(amount: float) -> str:
    whole, frac = f"{amount:,.2f}".split(".")
    return r"\s".join(re.escape(p) for p in whole.split(",")) + f",{frac}"


class Money:
    """Свой банковский счёт и контрагент теста; уборка — всё, что на них повисло."""

    def __init__(self, page: Page):
        self.page = page
        tag = stamp()
        self.cp = fx_json(fx_post(page, "/counterparties/", {"name": f"{QA_PREFIX} Плательщик {tag}", "type": "customer"}))
        self.acc = fx_json(fx_post(page, "/bank-accounts/", {"name": f"{QA_PREFIX} Деньги {tag}", "type": "bank", "currency": "MDL"}))
        self.pis: list[int] = []
        self.imports: list[int] = []
        self.recurring: list[int] = []

    def pi(self, amount=1000, tax=20) -> dict:
        pi = fx_json(fx_post(self.page, "/payment-invoices/", {
            "counterparty_id": self.cp["id"], "type": "receivable", "issue_date": TODAY, "due_date": DUE, "currency": "MDL",
            "lines": [{"description": "Услуга автотеста", "quantity": 1, "unit_price": amount, "tax_rate": tax}]}))
        self.pis.append(pi["id"])
        return pi

    def payment(self, amount, **data) -> dict:
        body = {"type": "income", "amount": amount, "currency": "MDL", "date": TODAY, "counterparty_id": self.cp["id"],
                "bank_account_id": self.acc["id"], "description": f"{QA_PREFIX} платёж", **data}
        return fx_json(fx_post(self.page, "/payments/", body))

    def payments(self) -> list[dict]:
        data = fx_json(fx_get(self.page, "/payments/", {"bank_account_id": self.acc["id"], "limit": 200}))
        items = data["items"] if isinstance(data, dict) else data
        more = fx_json(fx_get(self.page, "/payments/", {"counterparty_id": self.cp["id"], "limit": 200}))
        items += more["items"] if isinstance(more, dict) else more
        return list({p["id"]: p for p in items}.values())

    def cleanup(self):
        p = self.page
        for pay in self.payments():
            if not fx_delete(p, f"/payments/{pay['id']}").ok:
                fx_delete(p, f"/payments/{pay['id']}?force=true")
        for pi_id in self.pis:
            fx_delete(p, f"/payment-invoices/{pi_id}?delete_payments=true")
        for imp_id in self.imports:
            fx_delete(p, f"/statement-imports/{imp_id}")
        for rp_id in self.recurring:
            fx_delete(p, f"/recurring-payments/{rp_id}")
        fx_delete(p, f"/bank-accounts/{self.acc['id']}")
        fx_put(p, f"/counterparties/{self.cp['id']}", {"is_active": False})


@pytest.fixture
def m(fx_page: Page):
    data = Money(fx_page)
    yield data
    data.cleanup()


def head(page: Page):
    return page.locator("header.sticky")


def doc_form(page: Page, title: str):
    return page.locator("div.fixed.inset-0").filter(has=page.get_by_text(title, exact=True)).last


def doc_panel(page: Page, number: str):
    return page.locator("div.fixed.inset-0").filter(has=page.get_by_text(number, exact=True)).last


def open_pi(page: Page, pi: dict):
    open_fx(page, "/admin/payment-invoices")
    page.locator("main tbody tr").filter(has_text=pi["number"]).click()
    panel = doc_panel(page, pi["number"])
    expect(panel).to_be_visible()
    return panel


# ───────────────────────── платежи ─────────────────────────

# Кейс: «Деньги» — вкладки статусов совпадают с API
def test_payment_status_counts_match_api(fx_page: Page, m):
    m.payment(100)
    with fx_page.expect_response(lambda r: "/api/payments/status-counts" in r.url) as resp:
        open_fx(fx_page, "/admin/payments")
    counts = resp.value.json()
    by = counts.get("counts", counts)
    main = fx_page.locator("main")
    for label, key in (("Черновик", "draft"), ("Ожидает", "pending"), ("Завершён", "completed"), ("Отменён", "cancelled")):
        expect(screen(main).get_by_role("button", name=f"{label} ({by.get(key, 0)})")).to_be_visible()
    total = by.get("all", by.get("total", sum(v for k, v in by.items() if k in ("draft", "pending", "completed", "cancelled"))))
    expect(screen(main).get_by_role("button", name=f"Все ({total})")).to_be_visible()


# Кейс: Новый платёж через форму
@pytest.mark.writes_data
def test_payment_create_via_form(fx_page: Page, m):
    name = f"{QA_PREFIX} приход {stamp()}"
    open_fx(fx_page, "/admin/payments")
    head(fx_page).get_by_role("button", name="Создать платёж", exact=True).click()
    form = doc_form(fx_page, "Платёж · создание")
    create = form.get_by_role("button", name="Создать", exact=True)
    expect(create).to_be_disabled()

    field(form, "Название").fill(name)
    field(form, "Сумма").fill("250.75")
    pick(fx_page, form, "Контрагент", re.compile(re.escape(m.cp["name"])))
    pick(fx_page, form, "Банковский счёт", re.compile(re.escape(m.acc["name"])))
    field(form, "Описание").fill("платёж через форму")
    with fx_page.expect_response(lambda r: "/api/payments/" in r.url and r.request.method == "POST") as resp:
        create.click()
    assert resp.value.status == 201, resp.value.text()
    pay = resp.value.json()

    assert (pay["name"], pay["amount"], pay["type"], pay["counterparty_id"], pay["bank_account_id"], pay["date"]) == (
        name, 250.75, "income", m.cp["id"], m.acc["id"], TODAY), pay
    row = fx_page.locator("main").locator("tr, a, div[role=row]").filter(has_text=name).first
    expect(row).to_be_visible()
    expect(row).to_contain_text(money(250.75))


# Кейс: Расход — тип «Расход» через форму
@pytest.mark.writes_data
def test_payment_expense_via_form(fx_page: Page, m):
    name = f"{QA_PREFIX} расход {stamp()}"
    open_fx(fx_page, "/admin/payments")
    head(fx_page).get_by_role("button", name="Создать платёж", exact=True).click()
    form = doc_form(fx_page, "Платёж · создание")
    field(form, "Название").fill(name)
    field(form, "Сумма").fill("40")
    pick(fx_page, form, "Тип", re.compile("^Расход"))
    pick(fx_page, form, "Банковский счёт", re.compile(re.escape(m.acc["name"])))
    with fx_page.expect_response(lambda r: "/api/payments/" in r.url and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    assert (resp.value.json()["type"], resp.value.json()["amount"]) == ("expense", 40)


# Кейс: Завершённый платёж — в остатке счёта, отменённый — нет
@pytest.mark.writes_data
def test_payment_complete_and_cancel_balance(fx_page: Page, m):
    balance = lambda: fx_json(fx_get(fx_page, f"/bank-accounts/{m.acc['id']}"))["current_balance"]  # noqa: E731
    before = balance()
    pay = m.payment(300)
    assert fx_json(fx_post(fx_page, f"/payments/{pay['id']}/complete", {}))["status"] == "completed"
    assert balance() == before + 300

    with fx_page.expect_response(lambda r: r.url.endswith("/api/bank-accounts/") and r.request.method == "GET"):
        open_fx(fx_page, "/admin/bank-accounts")
    row = fx_page.locator("main tbody tr").filter(has_text=m.acc["name"])
    expect(row).to_contain_text(re.compile(money(before + 300) + r"\s*MDL"))

    assert fx_json(fx_post(fx_page, f"/payments/{pay['id']}/cancel", {}))["status"] == "cancelled"
    assert balance() == before


# ───────────────────────── оплата счёта ─────────────────────────

# Кейс: Счёт → «Создать на основе» → «Оплата» — форма заполнена по счёту
@pytest.mark.writes_data
def test_payment_from_invoice_form(fx_page: Page, m):
    pi = m.pi()
    panel = open_pi(fx_page, pi)
    panel.get_by_role("button", name="Создать на основе", exact=True).click()
    fx_page.get_by_role("menuitem", name="Оплата", exact=True).click()

    form = doc_form(fx_page, "Платёж · создание")
    expect(field(form, "Название")).to_have_value(f"Оплата по счёту {pi['number']}")
    expect(field(form, "Сумма")).to_have_value("1200")
    expect(form).to_contain_text(m.cp["name"])
    expect(form).to_contain_text(re.compile(re.escape(pi["number"]) + r".*" + money(1200)))

    pick(fx_page, form, "Банковский счёт", re.compile(re.escape(m.acc["name"])))
    with fx_page.expect_response(lambda r: "/api/payments/" in r.url and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    pay = resp.value.json()
    assert (pay["payment_invoice_id"], pay["amount"], pay["counterparty_id"]) == (pi["id"], 1200, m.cp["id"]), pay

    api = fx_json(fx_get(fx_page, f"/payment-invoices/{pi['id']}"))
    assert (api["status"], api["paid_amount"]) == ("paid", 1200), api


# Кейс: Частичная оплата — в счёте «Оплачено 500» и «Остаток 700»
@pytest.mark.writes_data
def test_partial_payment(fx_page: Page, m):
    pi = m.pi()
    fx_post(fx_page, f"/payments/{m.payment(500, payment_invoice_id=pi['id'])['id']}/complete", {})

    api = fx_json(fx_get(fx_page, f"/payment-invoices/{pi['id']}/payments"))
    assert (api["settled"], api["outstanding"]) == (500, 700), api
    assert fx_json(fx_get(fx_page, f"/payment-invoices/{pi['id']}"))["status"] != "paid"

    panel = open_pi(fx_page, pi)
    expect(panel).to_contain_text(re.compile(r"Оплачено\s*" + money(500), re.I))
    expect(panel).to_contain_text(re.compile(r"Остаток\s*" + money(700), re.I))


# Кейс: Две оплаты закрывают счёт — статус «Оплачен»
@pytest.mark.writes_data
def test_two_payments_close_invoice(fx_page: Page, m):
    pi = m.pi()
    for amount in (500, 700):
        fx_post(fx_page, f"/payments/{m.payment(amount, payment_invoice_id=pi['id'])['id']}/complete", {})

    api = fx_json(fx_get(fx_page, f"/payment-invoices/{pi['id']}"))
    assert (api["status"], api["paid_amount"]) == ("paid", 1200), api
    items = fx_json(fx_get(fx_page, f"/payment-invoices/{pi['id']}/payments"))["items"]
    assert sorted(i["amount"] for i in items) == [500, 700]

    panel = open_pi(fx_page, pi)
    expect(panel).to_contain_text("Оплачен")
    expect(panel).to_contain_text(re.compile(r"Остаток\s*0,00", re.I))


# Кейс: Удаление оплаты возвращает счёту долг
@pytest.mark.writes_data
def test_delete_payment_reopens_invoice(fx_page: Page, m):
    pi = m.pi()
    pay = m.payment(1200, payment_invoice_id=pi["id"])
    fx_post(fx_page, f"/payments/{pay['id']}/complete", {})
    assert fx_json(fx_get(fx_page, f"/payment-invoices/{pi['id']}"))["status"] == "paid"

    assert fx_delete(fx_page, f"/payments/{pay['id']}").ok
    api = fx_json(fx_get(fx_page, f"/payment-invoices/{pi['id']}"))
    assert api["paid_amount"] == 0 and api["status"] != "paid", api


# ───────────────────────── выписка файлом ─────────────────────────

def statement_csv(tmp_path, m, income=1500, fee=12.5, tag="") -> str:
    """CSV-выписка «Дата;Описание;Приход;Расход;Контрагент;IBAN» — две операции."""
    d1 = (dt.date.today() - dt.timedelta(days=1)).strftime("%d.%m.%Y")
    d2 = dt.date.today().strftime("%d.%m.%Y")
    num = lambda x: f"{x:.2f}".replace(".", ",")  # noqa: E731 — суммы с запятой, как в банковских выписках
    rows = ["Дата;Описание;Приход;Расход;Контрагент;IBAN",
            f"{d1};Оплата QA {tag};{num(income)};;{m.cp['name']};MD00QA000000000000000777",
            f"{d2};Комиссия банка QA {tag};;{num(fee)};;"]
    path = os.path.join(tmp_path, "statement-" + re.sub(r"\W", "_", tag or "qa") + ".csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")
    return path


def upload_statement(page: Page, m, path: str) -> dict:
    open_fx(page, "/admin/bank-integrations?tab=statements")
    with page.expect_response(lambda r: "/statement-imports/upload" in r.url) as up:
        page.locator("main input[type=file]").set_input_files(path)
    assert up.value.ok, up.value.text()
    imp = up.value.json()
    m.imports.append(imp["id"])
    page.wait_for_url(re.compile(rf"/bank-integrations/statements/{imp['id']}"), timeout=20_000)
    return imp


# Кейс: Выписка CSV — предпросмотр с итогами и предупреждением
@pytest.mark.writes_data
def test_statement_preview(fx_page: Page, m, tmp_path):
    imp = upload_statement(fx_page, m, statement_csv(str(tmp_path), m, tag=stamp()))
    main = fx_page.locator("main")

    assert (imp["status"], imp["row_count"], imp["new_count"]) == ("parsed", 2, 2), imp
    expect(main).to_contain_text("2 операций")
    expect(main).to_contain_text("сверить полноту выписки автоматически невозможно")
    expect(main).to_contain_text(re.compile(r"Приход\s*" + money(1500)))
    expect(main).to_contain_text(re.compile(r"Расход\s*" + money(12.5)))
    expect(main).to_contain_text(m.cp["name"])
    expect(screen(main).get_by_role("button", name="Новые 2")).to_be_visible()
    expect(screen(main).get_by_role("button", name="Выберите счёт…")).to_be_visible()


# Кейс: Выписка CSV — загрузка операций на свой счёт
@pytest.mark.writes_data
def test_statement_import_creates_payments(fx_page: Page, m, tmp_path):
    tag = stamp()
    upload_statement(fx_page, m, statement_csv(str(tmp_path), m, tag=tag))
    main = fx_page.locator("main")
    screen(main).get_by_role("button", name="Выберите счёт…").click()
    fx_page.get_by_role("option").filter(has_text=m.acc["name"]).first.click()
    load = screen(main).get_by_role("button", name=re.compile(r"^Загрузить операции \(2\)"))
    expect(load).to_be_enabled()
    with fx_page.expect_response(lambda r: "/statement-imports/" in r.url and r.url.endswith("/confirm")) as resp:
        load.click()
        maybe_confirm(fx_page)
    assert resp.value.ok, resp.value.text()

    # платежи по подтверждённой выписке иногда появляются через секунду-две после ответа /confirm
    # (ночной прогон 26.11 прочитал []), поэтому ждём их, а не читаем один раз
    for _ in range(10):
        pays = [p for p in m.payments() if tag in (p.get("description") or "") or tag in (p.get("name") or "")]
        if len(pays) >= 2:
            break
        fx_page.wait_for_timeout(1_000)
    assert sorted((p["type"], p["amount"]) for p in pays) == [("expense", 12.5), ("income", 1500)], pays
    assert all(p["bank_account_id"] == m.acc["id"] for p in pays)


# Кейс: Тот же файл выписки второй раз — «Этот файл уже загружали»
@pytest.mark.writes_data
def test_statement_same_file_twice(fx_page: Page, m, tmp_path):
    path = statement_csv(str(tmp_path), m, tag=stamp())
    first = upload_statement(fx_page, m, path)

    open_fx(fx_page, "/admin/bank-integrations?tab=statements")
    with fx_page.expect_response(lambda r: "/statement-imports/upload" in r.url) as up:
        fx_page.locator("main input[type=file]").set_input_files(path)
    again = up.value.json()

    assert again["duplicate"] is True and again["id"] == first["id"], again
    expect(fx_page.get_by_text("Этот файл уже загружали")).to_be_visible()
    expect(fx_page).to_have_url(re.compile(r"tab=statements"))


# ───────────────────────── регулярные ─────────────────────────

# Кейс: Регулярный платёж — создать, приостановить, удалить
@pytest.mark.writes_data
def test_recurring_payment(fx_page: Page, m):
    name = f"{QA_PREFIX} подписка {stamp()}"
    with fx_page.expect_response(lambda r: "/api/recurring-payments/" in r.url):
        open_fx(fx_page, "/admin/recurring")
    screen(fx_page).get_by_role("button", name="Создать", exact=True).click()
    form = modal(fx_page, "Новый повторяющийся платёж")
    field(form, "Название").fill(name)
    field(form, "Сумма").fill("99")
    pick(fx_page, form, "Банковский счёт", re.compile(re.escape(m.acc["name"])))
    field(form, "Описание").fill("подписка автотеста")
    with fx_page.expect_response(lambda r: r.url.endswith("/api/recurring-payments/") and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    rp = resp.value.json()
    m.recurring.append(rp["id"])
    assert (rp["name"], rp["amount"], rp["type"], rp["repeat_mode"], rp["is_active"]) == (
        name, 99, "expense", rp["repeat_mode"], True), rp

    group = screen(fx_page).get_by_role("button").filter(has_text="Без категории").first
    row = fx_page.locator("main tr").filter(has_text=name)
    if not row.count():
        group.click()  # группы статей свёрнуты
    expect(row).to_contain_text("99,00 MDL")
    expect(row).to_contain_text("Ежемесячно")
    expect(row).to_contain_text("Активен")

    with fx_page.expect_response(lambda r: f"/api/recurring-payments/{rp['id']}/pause" in r.url) as paused:
        row.get_by_role("button", name="Приостановить").click()
        maybe_confirm(fx_page)
    assert paused.value.ok
    assert fx_json(fx_get(fx_page, f"/recurring-payments/{rp['id']}"))["is_active"] is False

    with fx_page.expect_response(lambda r: f"/api/recurring-payments/{rp['id']}" in r.url and r.request.method == "DELETE") as gone:
        fx_page.locator("main tr").filter(has_text=name).get_by_role("button", name="Удалить").click()
        maybe_confirm(fx_page)
    assert gone.value.ok
    m.recurring.remove(rp["id"])
    expect(fx_page.locator("main tr").filter(has_text=name)).to_have_count(0)


# ───────────────────────── платёжный календарь ─────────────────────────

# Кейс: Платёжный календарь — сводка совпадает с API
def test_payment_calendar_summary(fx_page: Page):
    with fx_page.expect_response(lambda r: "/api/payment-orders/summary" in r.url) as resp:
        open_fx(fx_page, "/admin/payment-calendar")
    summary = resp.value.json()
    orders = fx_json(fx_get(fx_page, "/payment-orders", {"state": "open", "scope": "all"}))
    items = orders["items"] if isinstance(orders, dict) else orders

    main = fx_page.locator("main")
    for title in ("Просрочено", "Заплатить за 7 дней", "Одобрено, не отправлено", "На счетах"):
        expect(main).to_contain_text(title)
    expect(head(fx_page)).to_contain_text(f"Платёжный календарь{len(items)}")
    assert isinstance(summary, dict) and summary, summary
