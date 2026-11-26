"""Блок 41. Facturi — продажи: оферты, счета на оплату, акты, накладные, каталог, «Сделки».

Снято 02.11.2026:
- одна сущность: с 04.11 везде «Офферы» (меню, заголовок экрана, кнопка «Создать
  оффер»), в окне «Оффер · создание», на «Сделках» этап «Оффер» (раньше в меню
  «Оферты», на экране «Оценки» / «Создать оценку»);
- документы создаются в окне без заголовка — сверху подпись «<Документ> · создание»;
  позиции — строки «Название и описание / Кол-во / Ед. / Цена / [Скидка] / [НДС %] /
  Сумма» (колонки «Скидка» и «НДС %» включаются галочками), внизу «Без НДС / НДС /
  Итого:». Клиент — выпадашка «Контрагент *» (у счёта и накладной — «Клиент *»);
- карточка документа — окно справа: оферта ?doc=estimate:<id> («Отметить
  отправленным» → окно «Письмо клиенту при этом НЕ уходит» → «Принять» / «Отклонить»,
  «Конвертировать в счёт» — открывает заполненную форму счёта на оплату); счёт на
  оплату («Создать на основе» → Накладная / Акт / Оффер / Оплата, «Отменить»,
  «Удалить», PDF собирается сам); акт ?doc=act:<id> («Подписать», «PDF», «Удалить»);
  накладная ?id=<id> («Отправить», «Отменить» с причиной) — открываем кликом по строке:
  перезагрузка или ссылка ?id= открывают карточку дважды (bord-32);
- «Отправить письмо» не жмём; контрагенты тестов без email, чтобы ничего не ушло.
  e-Factura в BORD QA не настроена — «Отправить» у накладной только меняет статус;
- удалить можно акты, счета на оплату, услуги каталога. Оферты не удаляются (отправить →
  отклонить), накладные — только «Отменить»: тестовые копятся в «Оценках», «Сделках» и
  «Накладных»;
- известные баги: bord-30 — POST /sales/{id}/act (API, есть в MCP BORD) начисляет НДС
  дважды и берёт номер не из ряда; bord-31 — в акте «Подитого:», дата ISO, суммы без копеек.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.facturi import (screen, QA_PREFIX, confirm, maybe_confirm, field, fx_delete, fx_get, fx_json, fx_post, fx_put, open_fx, pick,
                             stamp, tick)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today().isoformat()
DUE = (dt.date.today() + dt.timedelta(days=14)).isoformat()


def money(amount: float) -> str:
    whole, frac = f"{amount:,.2f}".split(".")
    return r"\s".join(re.escape(p) for p in whole.split(",")) + f",{frac}"


# ───────────────────────── данные теста и уборка ─────────────────────────

class Docs:
    """Документы теста: уборка по типу — что удаляется, удаляем, остальное гасим."""

    def __init__(self, page: Page):
        self.page = page
        self.cp = fx_json(fx_post(page, "/counterparties/", {"name": f"{QA_PREFIX} Покупатель {stamp()}", "type": "customer"}))
        self.estimates: list[int] = []
        self.pis: list[int] = []
        self.acts: list[int] = []
        self.invoices: list[int] = []
        self.items: list[int] = []

    def estimate(self, lines=None, **data) -> dict:
        body = {"counterparty_id": self.cp["id"], "estimate_date": TODAY, "currency": "MDL",
                "lines": lines or [{"description": "Услуга автотеста", "quantity": 1, "unit_price": 1000, "tax_rate": 20}], **data}
        est = fx_json(fx_post(self.page, "/estimates/", body))
        self.estimates.append(est["id"])
        return est

    def pi(self, lines=None, **data) -> dict:
        body = {"counterparty_id": self.cp["id"], "type": "receivable", "issue_date": TODAY, "due_date": DUE, "currency": "MDL",
                "lines": lines or [{"description": "Услуга автотеста", "quantity": 1, "unit_price": 1000, "tax_rate": 20}], **data}
        pi = fx_json(fx_post(self.page, "/payment-invoices/", body))
        self.pis.append(pi["id"])
        return pi

    def act(self, **data) -> dict:
        body = {"counterparty_id": self.cp["id"], "act_date": TODAY, "currency": "MDL",
                "lines": [{"description": "Работы автотеста", "quantity": 1, "unit_price": 300}], **data}
        act = fx_json(fx_post(self.page, "/acts/", body))
        self.acts.append(act["id"])
        return act

    def invoice(self, **data) -> dict:
        body = {"counterparty_id": self.cp["id"], "type": "receivable", "issue_date": TODAY, "due_date": DUE, "currency": "MDL",
                "lines": [{"description": "Поставка автотеста", "quantity": 1, "unit_price": 200}], **data}
        inv = fx_json(fx_post(self.page, "/invoices/", body))
        self.invoices.append(inv["id"])
        return inv

    def cleanup(self):
        p = self.page
        cid = self.cp["id"]
        # подбираем и то, что тест создал через форму
        acts = set(self.acts) | {a["id"] for a in fx_json(fx_get(p, "/acts/", {"counterparty_id": cid, "limit": 100}))["items"]}
        for act_id in acts:
            fx_delete(p, f"/acts/{act_id}")
        pis = set(self.pis) | {x["id"] for x in fx_json(fx_get(p, "/payment-invoices/", {"counterparty_id": cid, "page": 0, "page_size": 100}))["items"]}
        for pi_id in pis:
            fx_delete(p, f"/payment-invoices/{pi_id}")
        for inv in fx_json(fx_get(p, "/invoices/", {"counterparty_id": cid, "limit": 100}))["items"]:
            if inv["status"] != "cancelled":
                fx_post(p, f"/invoices/{inv['id']}/cancel", {})
        for est in fx_json(fx_get(p, "/estimates/", {"counterparty_id": cid, "limit": 100}))["items"]:
            if est["status"] == "draft":
                fx_post(p, f"/estimates/{est['id']}/send", {})
            if est["status"] in ("draft", "sent"):
                fx_post(p, f"/estimates/{est['id']}/decline", {})
        for item_id in self.items:
            fx_delete(p, f"/catalog-items/{item_id}")
        fx_put(p, f"/counterparties/{cid}", {"is_active": False})


@pytest.fixture
def docs(fx_page: Page):
    d = Docs(fx_page)
    yield d
    d.cleanup()


def head(page: Page):
    return page.locator("header.sticky")


def doc_form(page: Page, title: str):
    """Окно создания документа — без заголовка, сверху подпись «Оффер · создание»."""
    return page.locator("div.fixed.inset-0").filter(has=page.get_by_text(title, exact=True)).last


def doc_panel(page: Page, number: str):
    """Карточка документа справа — окно, где виден его номер."""
    return page.locator("div.fixed.inset-0").filter(has=page.get_by_text(number, exact=True)).last


def line(form, i: int = 0):
    """Строка позиций: от поля «Название и описание» вверх до блока со всеми полями строки."""
    name = form.get_by_role("textbox", name="Название и описание").nth(i)
    return name, name.locator("xpath=ancestor::div[count(.//input) >= 3][1]")


def fill_line(form, i: int, title: str, qty, price, tax=None):
    """Заполнить строку: название, кол-во, цена и (если колонка «НДС %» включена) ставка."""
    name, row = line(form, i)
    name.fill(title)
    inputs = row.locator("input")
    inputs.nth(0).fill(str(qty))
    row.get_by_placeholder("0,00").fill(str(price))
    if tax is not None:
        inputs.last.fill(str(tax))


# ───────────────────────── оферты ─────────────────────────

def open_estimates(page: Page) -> dict:
    with page.expect_response(lambda r: "/api/estimates/counts" in r.url) as resp:
        open_fx(page, "/admin/estimates")
    return resp.value.json()


# Кейс: Новая оферта через форму — итоги считаются по строкам
@pytest.mark.writes_data
def test_estimate_create_via_form(fx_page: Page, docs):
    open_estimates(fx_page)
    head(fx_page).get_by_role("button", name="Создать оффер", exact=True).click()
    form = doc_form(fx_page, "Оффер · создание")
    create = form.get_by_role("button", name="Создать", exact=True)
    expect(create).to_be_disabled()

    pick(fx_page, form, "Контрагент", re.compile(re.escape(docs.cp["name"])))
    tick(form, "Налоги", True)
    fill_line(form, 0, "Разработка", 2, 100, tax=20)
    form.get_by_role("button", name="Добавить строку", exact=True).click()
    fill_line(form, 1, "Поддержка", 1, 50, tax=0)

    text = form.inner_text()
    assert re.search(r"Без НДС\s*" + money(250), text), text[-300:]
    assert re.search(r"НДС\s*" + money(40), text), text[-300:]
    assert re.search(r"Итого:\s*" + money(290) + r"\s*MDL", text), text[-300:]
    with fx_page.expect_response(lambda r: r.url.endswith("/api/estimates/") and r.request.method == "POST") as resp:
        create.click()
    assert resp.value.status == 201, resp.value.text()
    est = resp.value.json()
    docs.estimates.append(est["id"])

    assert (est["subtotal"], est["tax_total"], est["total"], est["status"]) == (250, 40, 290, "draft"), est
    assert [(ln["description"], ln["quantity"], ln["unit_price"], ln["tax_rate"]) for ln in est["lines"]] == [
        ("Разработка", 2, 100, 20), ("Поддержка", 1, 50, 0)]
    assert est["number"].startswith("EST-")
    row = fx_page.locator("main tbody tr").filter(has_text=est["number"])
    expect(row).to_contain_text(docs.cp["name"])
    expect(row).to_contain_text(re.compile(money(290) + r"\s*MDL"))
    expect(row).to_contain_text("Черновик")


# Кейс: Оферты — вкладки статусов совпадают с API
def test_estimate_counts_match_api(fx_page: Page, docs):
    docs.estimate()
    counts = open_estimates(fx_page)
    by_status = counts.get("counts", counts)

    main = fx_page.locator("main")
    expect(screen(main).get_by_role("button", name=f"Все ({by_status['all']})")).to_be_visible()
    for label, key in (("Черновики", "draft"), ("Отправлено", "sent"), ("Принято", "accepted"),
                       ("Отклонено", "declined"), ("Конвертирован", "converted")):
        expect(screen(main).get_by_role("button", name=f"{label} ({by_status.get(key, 0)})")).to_be_visible()


def open_estimate(page: Page, est: dict):
    open_fx(page, f"/admin/estimates?doc=estimate%3A{est['id']}")
    panel = doc_panel(page, est["number"])
    expect(panel).to_be_visible()
    return panel


# Кейс: Оферта — «Отметить отправленным» без письма, затем «Отклонить»
@pytest.mark.writes_data
def test_estimate_mark_sent_and_decline(fx_page: Page, docs):
    est = docs.estimate()
    panel = open_estimate(fx_page, est)
    expect(panel).to_contain_text("Черновик")
    expect(panel.get_by_role("button", name="Принять")).to_have_count(0)

    panel.get_by_role("button", name="Отметить отправленным", exact=True).click()
    with fx_page.expect_response(lambda r: f"/api/estimates/{est['id']}/send" in r.url) as sent:
        text = confirm(fx_page, "Да")
    assert "Письмо клиенту при этом НЕ уходит" in text, text
    assert sent.value.ok
    expect(panel).to_contain_text("Отправлено")
    assert fx_json(fx_get(fx_page, f"/estimates/{est['id']}/emails"))["total"] == 0

    with fx_page.expect_response(lambda r: f"/api/estimates/{est['id']}/decline" in r.url) as declined:
        panel.get_by_role("button", name="Отклонить", exact=True).click()
        maybe_confirm(fx_page)
    assert declined.value.ok
    assert fx_json(fx_get(fx_page, f"/estimates/{est['id']}"))["status"] == "declined"


# Кейс: Оферта — «Принять»
@pytest.mark.writes_data
def test_estimate_accept(fx_page: Page, docs):
    est = docs.estimate()
    fx_json(fx_post(fx_page, f"/estimates/{est['id']}/send", {}))
    panel = open_estimate(fx_page, est)

    with fx_page.expect_response(lambda r: f"/api/estimates/{est['id']}/accept" in r.url) as accepted:
        panel.get_by_role("button", name="Принять", exact=True).click()
        maybe_confirm(fx_page)
    assert accepted.value.ok
    assert fx_json(fx_get(fx_page, f"/estimates/{est['id']}"))["status"] == "accepted"


# Кейс: «Конвертировать в счёт» — форма счёта заполнена по оферте, суммы те же
@pytest.mark.writes_data
def test_estimate_convert_to_payment_invoice(fx_page: Page, docs):
    est = docs.estimate()
    panel = open_estimate(fx_page, est)
    panel.get_by_role("button", name="Конвертировать в счёт", exact=True).click()
    form = doc_form(fx_page, "Счёт · создание")
    expect(form).to_contain_text(docs.cp["name"])
    name, _ = line(form)
    expect(name).to_have_value("Услуга автотеста")
    expect(form).to_contain_text(re.compile(r"Итого:\s*" + money(1200)))

    field(form, "Срок оплаты").fill(DUE)
    with fx_page.expect_response(lambda r: r.url.endswith("/api/payment-invoices/") and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    pi = resp.value.json()
    docs.pis.append(pi["id"])

    assert (pi["subtotal"], pi["tax_amount"], pi["total"], pi["estimate_id"]) == (1000, 200, 1200, est["id"]), pi
    assert fx_json(fx_get(fx_page, f"/estimates/{est['id']}"))["status"] == "converted"
    pi_panel = doc_panel(fx_page, pi["number"])
    expect(pi_panel.get_by_role("button", name="Офферы1")).to_be_visible()


# ───────────────────────── счета на оплату ─────────────────────────

def open_pis(page: Page) -> dict:
    with page.expect_response(lambda r: "/api/payment-invoices/counts" in r.url) as resp:
        open_fx(page, "/admin/payment-invoices")
    return resp.value.json()


# Кейс: Новый счёт на оплату через форму
@pytest.mark.writes_data
def test_payment_invoice_create_via_form(fx_page: Page, docs):
    open_pis(fx_page)
    head(fx_page).get_by_role("button", name="Создать счёт", exact=True).click()
    form = doc_form(fx_page, "Счёт · создание")
    create = form.get_by_role("button", name="Создать", exact=True)
    expect(create).to_be_disabled()

    pick(fx_page, form, "Клиент", re.compile(re.escape(docs.cp["name"])))
    field(form, "Срок оплаты").fill(DUE)
    tick(form, "Налоги", True)
    fill_line(form, 0, "Абонентское обслуживание", 3, 500, tax=20)
    expect(form).to_contain_text(re.compile(r"Итого:\s*" + money(1800) + r"\s*MDL"))
    with fx_page.expect_response(lambda r: r.url.endswith("/api/payment-invoices/") and r.request.method == "POST") as resp:
        create.click()
    assert resp.value.status == 201, resp.value.text()
    pi = resp.value.json()
    docs.pis.append(pi["id"])

    assert (pi["type"], pi["status"], pi["subtotal"], pi["tax_amount"], pi["total"], pi["due_date"]) == (
        "receivable", "draft", 1500, 300, 1800, DUE), pi
    panel = doc_panel(fx_page, pi["number"])
    expect(panel).to_contain_text("Черновик")
    expect(panel).to_contain_text(re.compile(r"Итого\s*" + money(1800) + r"\s*MDL"))


# Кейс: Счета на оплату — вкладки статусов совпадают с API
def test_payment_invoice_counts_match_api(fx_page: Page, docs):
    docs.pi()
    counts = open_pis(fx_page)
    by_status = counts.get("counts", counts)
    main = fx_page.locator("main")
    for label, key in (("Черновик", "draft"), ("Отправлен", "sent"), ("Оплачен", "paid"),
                       ("Просрочен", "overdue"), ("Отменён", "cancelled")):
        expect(screen(main).get_by_role("button", name=f"{label} ({by_status.get(key, 0)})")).to_be_visible()


# Кейс: Счёт на оплату — PDF собирается
def test_payment_invoice_pdf(fx_page: Page, docs):
    pi = docs.pi()
    resp = fx_get(fx_page, f"/payment-invoices/{pi['id']}/preview-pdf")
    assert resp.ok, resp.status
    assert resp.headers.get("content-type", "").startswith("application/pdf"), resp.headers
    assert resp.body()[:4] == b"%PDF"


# Кейс: Счёт на оплату → «Создать на основе» → «Акт» с теми же суммами
@pytest.mark.writes_data
def test_act_from_payment_invoice(fx_page: Page, docs):
    pi = docs.pi()
    open_fx(fx_page, "/admin/payment-invoices")
    fx_page.locator("main tbody tr").filter(has_text=pi["number"]).click()
    panel = doc_panel(fx_page, pi["number"])
    panel.get_by_role("button", name="Создать на основе", exact=True).click()
    fx_page.get_by_role("menuitem", name="Акт", exact=True).click()

    form = doc_form(fx_page, "Акт · создание")
    expect(form).to_contain_text(pi["number"])
    expect(form).to_contain_text(re.compile(r"Итого:\s*" + money(1200)))
    with fx_page.expect_response(lambda r: r.url.endswith("/api/acts/") and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    act = resp.value.json()
    docs.acts.append(act["id"])

    assert (act["subtotal"], act["tax_amount"], act["total"], act["payment_invoice_id"]) == (1000, 200, 1200, pi["id"]), act
    assert act["number"].startswith("ACT-")


# Кейс: Удаление счёта на оплату
@pytest.mark.writes_data
def test_payment_invoice_delete(fx_page: Page, docs):
    pi = docs.pi()
    open_fx(fx_page, "/admin/payment-invoices")
    fx_page.locator("main tbody tr").filter(has_text=pi["number"]).click()
    panel = doc_panel(fx_page, pi["number"])
    with fx_page.expect_response(lambda r: f"/api/payment-invoices/{pi['id']}" in r.url and r.request.method == "DELETE") as resp:
        panel.get_by_role("button", name="Удалить", exact=True).click()
        maybe_confirm(fx_page)
    assert resp.value.ok, resp.value.status
    assert fx_get(fx_page, f"/payment-invoices/{pi['id']}").status == 404
    docs.pis.remove(pi["id"])


# ───────────────────────── акты ─────────────────────────

# Кейс: Акт — «Подписать»
@pytest.mark.writes_data
def test_act_sign(fx_page: Page, docs):
    act = docs.act()
    open_fx(fx_page, f"/admin/acts?doc=act%3A{act['id']}")
    panel = doc_panel(fx_page, act["number"])
    expect(panel).to_contain_text("Черновик")
    with fx_page.expect_response(lambda r: f"/api/acts/{act['id']}/sign" in r.url) as resp:
        panel.get_by_role("button", name="Подписать", exact=True).click()
        maybe_confirm(fx_page)
    assert resp.value.ok, resp.value.text()
    assert fx_json(fx_get(fx_page, f"/acts/{act['id']}"))["status"] in ("signed", "awaiting_signature"), \
        fx_json(fx_get(fx_page, f"/acts/{act['id']}"))["status"]


# Кейс: Акт — PDF собирается
def test_act_pdf(fx_page: Page, docs):
    act = docs.act()
    resp = fx_get(fx_page, f"/acts/{act['id']}/pdf")
    assert resp.ok, resp.status
    assert resp.body()[:4] == b"%PDF"


# Кейс: Карточка акта — подписи и форматы как у остальных документов
def test_act_panel_formats(fx_page: Page, docs):
    act = docs.act()
    open_fx(fx_page, f"/admin/acts?doc=act%3A{act['id']}")
    panel = doc_panel(fx_page, act["number"])
    expect(panel).to_contain_text("Подытог")
    expect(panel).to_contain_text(dt.date.today().strftime("%d.%m.%Y"))
    expect(panel).to_contain_text(re.compile(r"Итого:\s*300,00\s*MDL"))


# ───────────────────────── накладные ─────────────────────────

# Кейс: Новая накладная через форму
@pytest.mark.writes_data
def test_invoice_create_via_form(fx_page: Page, docs):
    open_fx(fx_page, "/admin/invoices")
    head(fx_page).get_by_role("button", name="Создать накладную", exact=True).click()
    form = doc_form(fx_page, "Накладная · создание")
    create = form.get_by_role("button", name="Создать", exact=True)
    expect(create).to_be_disabled()

    pick(fx_page, form, "Клиент", re.compile(re.escape(docs.cp["name"])))
    field(form, "Срок оплаты").fill(DUE)
    fill_line(form, 0, "Поставка через форму", 4, 25)
    with fx_page.expect_response(lambda r: r.url.endswith("/api/invoices/") and r.request.method == "POST") as resp:
        create.click()
    assert resp.value.status == 201, resp.value.text()
    inv = resp.value.json()
    docs.invoices.append(inv["id"])

    assert (inv["type"], inv["status"], inv["total"], inv["source"]) == ("receivable", "draft", 100, "manual"), inv
    assert inv["number"].startswith("INV-")


def open_invoice(page: Page, inv: dict):
    """Карточка накладной — кликом по строке (ссылка ?id= открывает её дважды, bord-32)."""
    open_fx(page, "/admin/invoices")
    page.locator("main tbody tr").filter(has_text=inv["number"]).click()
    panel = doc_panel(page, inv["number"])
    expect(panel).to_be_visible()
    expect(page.locator("div.fixed.inset-0")).to_have_count(1)
    return panel


# Кейс: Ссылка на накладную открывает одну карточку
def test_invoice_link_opens_one_card(fx_page: Page, docs):
    inv = docs.invoice()
    open_fx(fx_page, f"/admin/invoices?id={inv['id']}")
    expect(doc_panel(fx_page, inv["number"])).to_be_visible()
    fx_page.wait_for_timeout(1_500)
    expect(fx_page.locator("div.fixed.inset-0")).to_have_count(1)


# Кейс: Накладная — «Отправить» меняет статус, «Отменить» с причиной
@pytest.mark.writes_data
def test_invoice_send_and_cancel(fx_page: Page, docs):
    inv = docs.invoice()
    panel = open_invoice(fx_page, inv)
    expect(panel).to_contain_text("Черновик")

    with fx_page.expect_response(lambda r: f"/api/invoices/{inv['id']}/send" in r.url) as sent:
        panel.get_by_role("button", name="Отправить", exact=True).click()
        maybe_confirm(fx_page)
    assert sent.value.ok, sent.value.text()
    assert fx_json(fx_get(fx_page, f"/invoices/{inv['id']}"))["status"] == "sent"

    panel.get_by_role("button", name="Отменить", exact=True).click()
    dialog = fx_page.locator("div.fixed.inset-0, [data-bord-dialogs]").filter(has_text="Клиент отказался").last
    expect(dialog).to_be_visible()
    dialog.get_by_text("Клиент отказался").click()
    with fx_page.expect_response(lambda r: f"/api/invoices/{inv['id']}/cancel" in r.url and r.request.method == "POST") as cancelled:
        dialog.get_by_role("button", name=re.compile("^Отменить")).last.click()
    assert cancelled.value.ok, cancelled.value.text()
    assert fx_json(fx_get(fx_page, f"/invoices/{inv['id']}"))["status"] == "cancelled"


# ───────────────────────── каталог услуг ─────────────────────────

# Кейс: Каталог услуг — добавить, изменить, удалить
@pytest.mark.writes_data
def test_catalog_crud(fx_page: Page, docs):
    name = f"{QA_PREFIX} Услуга {stamp()}"
    with fx_page.expect_response(lambda r: "/api/catalog-items/" in r.url and r.request.method == "GET"):
        open_fx(fx_page, "/admin/catalog")
    head(fx_page).get_by_role("button", name="Добавить услугу", exact=True).click()
    form = fx_page.locator("div.fixed.inset-0").filter(has=fx_page.get_by_role("heading", name="Новая услуга")).last
    save = form.get_by_role("button", name="Сохранить", exact=True)
    expect(save).to_be_disabled()
    field(form, "Название в каталоге").fill(name)
    field(form, "Ед.").fill("час")
    field(form, "Цена без НДС").fill("750")
    field(form, "НДС %").fill("20")
    with fx_page.expect_response(lambda r: r.url.endswith("/api/catalog-items/") and r.request.method == "POST") as resp:
        save.click()
    assert resp.value.ok, resp.value.text()
    item = resp.value.json()
    docs.items.append(item["id"])
    assert (item["name"], item["unit"], item["price"], item["tax_rate"]) == (name, "час", 750, 20), item

    row = fx_page.locator("main tr").filter(has_text=name)
    expect(row).to_contain_text("750")
    row.get_by_role("button", name="Изменить").click()
    edit = fx_page.locator("div.fixed.inset-0").last
    field(edit, "Цена без НДС").fill("800")
    with fx_page.expect_response(lambda r: f"/api/catalog-items/{item['id']}" in r.url and r.request.method == "PUT") as upd:
        edit.get_by_role("button", name="Сохранить", exact=True).click()
    assert upd.value.ok, upd.value.text()
    expect(row).to_contain_text("800")

    with fx_page.expect_response(lambda r: f"/api/catalog-items/{item['id']}" in r.url and r.request.method == "DELETE") as gone:
        row.get_by_role("button", name="Удалить").click()
        maybe_confirm(fx_page)
    assert gone.value.ok
    expect(row).to_have_count(0)
    docs.items.remove(item["id"])


# ───────────────────────── сделки ─────────────────────────

# Кейс: «Сделки» — продажа с оффером и счётом на 2 из 5 этапов
def test_sale_stages(fx_page: Page, docs):
    est = docs.estimate()
    pi = docs.pi(estimate_id=est["id"])
    link = fx_json(fx_post(fx_page, f"/sales/est:{est['id']}/link", {"stage": "account", "target_id": pi["id"]}))
    anchor = link["anchor"]
    sale = fx_json(fx_get(fx_page, f"/sales/{anchor}"))
    assert sale["stages"]["offer"]["status"] == "present" and sale["stages"]["account"]["status"] == "present", sale["stages"]

    with fx_page.expect_response(lambda r: "/api/sales/" in r.url):
        open_fx(fx_page, "/admin/sales")
    row = fx_page.locator("main tbody tr").filter(has_text=pi["number"])
    expect(row).to_contain_text("2/5")
    expect(row).to_contain_text(docs.cp["name"])
    row.click()
    main = fx_page.locator("main")
    expect(main).to_contain_text(est["number"])
    expect(screen(main).get_by_role("button", name="Конвертировать счёт в накладную")).to_be_visible()
    expect(main).to_contain_text(re.compile(r"0,00\s*/\s*" + money(1200) + r"\s*MDL"))


# Кейс: Акт по продаже через API — суммы как в счёте
def test_sale_act_api_totals(fx_page: Page, docs):
    est = docs.estimate()
    pi = docs.pi(estimate_id=est["id"])
    fx_json(fx_post(fx_page, f"/sales/est:{est['id']}/link", {"stage": "account", "target_id": pi["id"]}))
    act = fx_json(fx_post(fx_page, f"/sales/{est['id']}/act", {}))
    docs.acts.append(act["id"])

    assert (act["subtotal"], act["tax_amount"], act["total"]) == (pi["subtotal"], pi["tax_amount"], pi["total"]), act
