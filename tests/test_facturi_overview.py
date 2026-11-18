"""Блок 48. Facturi — «Обзор»: дашборд, свой дашборд, инбокс, документы,
«Подтвердить накладные», юнит-экономика.

Снято 02.11.2026 (в видимом MCP-браузере):
- «Дашборд» (/admin) — вкладки дашбордов («Основной» + свои) и «Новый», «Настроить».
  Состав «Основного» = GET /api/dashboards/ (dashboards[].layout: type, w; widgets —
  палитра видов, с 12.11 их 20; default_layout — «Основной» по умолчанию). Виджеты читают свои ручки: «С чего начать» —
  /onboarding/checklist («N из M»), «Четыре ответа» — /dashboard/ (money_now,
  receivable_block, payable_block), «Требует внимания» — /inbox/, «Что сделать» (до 17.11
  «Рекомендации») — /dashboard/recommendations (headline, сумма, кнопка = links[0]),
  «Нам / Мы должны по срокам» — /reports/aging?type=receivable|payable;
- «Новый» сразу открывает режим правки: «Название», «Добавить виджет» (палитра),
  у виджета ширина «⅓ / ½ / ⅔ / Вся» (w 2/3/4/6), «Раньше / Позже / Убрать»,
  «Удалить дашборд». «Сохранить» → POST /dashboards/materialize (основной становится
  записью) + POST /dashboards/ + PUT /dashboards/<id>; удаление — окно «Удалить дашборд
  «…»?» → «Да» → DELETE;
- «Инбокс» = /inbox/ (items: title, count, amount_mdl, action, note, sample; snoozed);
  «Отложить на неделю» — POST /inbox/<kind>/snooze, «Вернуть» — DELETE того же пути;
  «Пересчитать» не жмём (как «Проверить сейчас»);
- «Документы» — загрузка любого файла: CSV-выписка узнаётся парсером без внешней модели
  («узнан без модели», kind bank_statement, «Готово к внесению»); карточка
  /admin/documents/<id> — разбор (период, валюта, «Операций»), «Внести / Это другое /
  Уже внесено — связать / Отклонить». Удаления у документов нет — тестовые
  отклоняются с причиной и копятся во вкладке «Отклонено». «Внести» не жмём (заведёт
  импорт выписки);
- «Подтвердить накладные» — e-Factura в BORD QA не настроена, накладных нет; фильтры
  scope=mine|unassigned|all × state=awaiting|decided; правила «поставщик → сотрудник»
  (ИДНО, название, сотрудник) — PUT /efactura/approvals/rules, «Снять правило» → «Да»;
- «Юнит-экономика» — период (input date в шапке, по умолчанию 1 января — сегодня),
  «С аллокацией / Без» (with_allocation в /unit-economics/by-business-line); плитки =
  /unit-economics/summary, «Топ-20 клиентов» = /by-customer. Выручка — по документам со
  статьёй P&L (Books: накладная 500 со статьёй 1100); расход платежом без документа в
  неё не попадает.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.facturi import (QA_PREFIX, Books, confirm, fx_delete, fx_get, fx_json, fx_post, open_fx, stamp,
                             unique_idno)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря"]


def money(amount: float):
    """«1 200,00» — разделитель тысяч у Facturi неразрывный пробел."""
    whole, frac = f"{abs(amount):,.2f}".split(".")
    return re.compile(r"\s".join(re.escape(p) for p in whole.split(",")) + f",{frac}")


def main(page: Page):
    return page.locator("main")


def widget(page: Page, heading: str):
    """Карточка виджета дашборда (.rounded-2xl: div, у «С чего начать» — section) с заголовком h2."""
    return main(page).locator(".rounded-2xl").filter(has=page.get_by_role("heading", name=heading, exact=True, level=2))


# ───────────────────────── дашборд ─────────────────────────

# Кейс: Дашборд «Основной» — виджеты по умолчанию на месте
def test_dashboard_default_widgets(fx_page: Page):
    with fx_page.expect_response(lambda r: r.url.endswith("/api/dashboards/")) as resp:
        open_fx(fx_page, "/admin")
    data = resp.value.json()
    assert data["dashboards"][0]["name"] == "Основной", data["dashboards"]
    # data["widgets"] — каталог типов (с 12.11 их 20: + income_categories, top_expense), а не дашборд.
    # «Основной» — dashboards[0].layout: пока он виртуальный (id=null), это default_layout
    # (на 13.11 — 18 виджетов); «Сохранить» своего дашборда материализует его в запись со
    # снимком тогдашнего состава (в BORD QA так живёт id=8 из 17 — без dso), новые виджеты
    # по умолчанию туда уже не попадают
    board = data["dashboards"][0]
    types = [w["type"] for w in board["layout"]]
    assert set(types) <= {w["type"] for w in data["widgets"]}, ", ".join(types)
    if board["virtual"]:
        assert types == [w["type"] for w in data["default_layout"]], ", ".join(types)
    expect(main(fx_page).get_by_role("button", name="Основной", exact=True)).to_be_visible()
    expect(main(fx_page).get_by_role("button", name="Новый", exact=True)).to_be_visible()
    for heading in ("С начала месяца", "Как дела у компании", "Динамика остатков", "Поступления и списания",
                    "Нам должны по срокам", "Мы должны по срокам", "Счета по статусу", "Быстрый доступ"):
        expect(main(fx_page).get_by_role("heading", name=heading, exact=True)).to_be_visible()


# Кейс: «С чего начать» — «N из M» и шаги = API
def test_dashboard_onboarding_matches_api(fx_page: Page):
    with fx_page.expect_response(lambda r: "/api/onboarding/checklist" in r.url) as resp:
        open_fx(fx_page, "/admin")
    items = resp.value.json()["items"]
    card = widget(fx_page, "С чего начать")
    expect(card).to_contain_text(re.compile(rf"{sum(i['done'] for i in items)}\s+из\s+{len(items)}"))
    for it in items:
        expect(card.locator(f"a[href='{it['href']}']")).to_contain_text(it["title"])


# Кейс: «Четыре ответа» — деньги, нам должны, мы должны = API
def test_dashboard_four_answers_match_api(fx_page: Page):
    with fx_page.expect_response(lambda r: r.url.endswith("/api/dashboard/")) as resp:
        open_fx(fx_page, "/admin")
    data = resp.value.json()
    m = main(fx_page)
    money_now = m.get_by_role("button").filter(has=fx_page.get_by_text("Деньги сейчас", exact=True))
    expect(money_now).to_contain_text(money(data["money_now"]["total_mdl"]))
    owed = m.locator("a[href='/admin/debtors?type=receivable']").filter(has_text="Нам должны")
    expect(owed).to_contain_text(money(data["receivable_block"]["total_mdl"]))
    we_owe = m.locator("div").filter(has=fx_page.get_by_text("Мы должны", exact=True)).filter(
        has_text="нажмите на строку").last
    expect(we_owe).to_contain_text(money(data["payable_block"]["total_mdl"]))


# Кейс: «Нам / Мы должны по срокам» — корзины просрочки = /reports/aging
@pytest.mark.parametrize("kind,heading", [("receivable", "Нам должны по срокам"), ("payable", "Мы должны по срокам")],
                         ids=["Нам должны", "Мы должны"])
def test_dashboard_aging_matches_api(fx_page: Page, kind, heading):
    with fx_page.expect_response(lambda r: f"/api/reports/aging?type={kind}" in r.url) as resp:
        open_fx(fx_page, "/admin")
    data = resp.value.json()
    card = widget(fx_page, heading)
    expect(card).to_contain_text(money(data["grand_total"]))
    for label, key in (("В срок", "current"), ("До 30 дн.", "30_days"), ("31–60 дн.", "60_days"), ("Больше 60 дн.", "90_plus")):
        expect(card.get_by_role("listitem").filter(has_text=label)).to_contain_text(money(data["totals"][key]))


# Кейс: «Что сделать» — рекомендации: заголовок, сумма и ссылка = API
def test_dashboard_recommendation_expands(fx_page: Page):
    """С 17.11 виджет «Рекомендации» стал «Что сделать N»: строки по группам («Деньги»,
    «Чтобы цифры были верными»), не раскрываются — у строки headline, сумма со знаком (sign)
    и одна кнопка = links[0]. В группе «Чтобы цифры были верными» — не рекомендации, а сводка
    проверок учёта, поэтому строк на экране не меньше, чем рекомендаций в API."""
    with fx_page.expect_response(lambda r: "/api/dashboard/recommendations" in r.url) as resp:
        open_fx(fx_page, "/admin")
    items = resp.value.json()["items"]
    if not items:
        pytest.skip("рекомендаций сейчас нет — сверять нечего")
    card = main(fx_page).locator(".rounded-2xl").filter(
        has=fx_page.locator("h2").filter(has_text=re.compile(r"^Что сделать\s*\d*$")))
    expect(card.locator("h2")).to_have_text(re.compile(r"^Что сделать\s*\d+$"))
    shown = int(re.search(r"\d+$", card.locator("h2").inner_text().strip()).group())
    assert shown >= len(items), f"в шапке {shown}, рекомендаций в API {len(items)}"
    for rec in items:
        row = card.get_by_role("listitem").filter(has=fx_page.get_by_text(rec["headline"], exact=True))
        expect(row).to_have_count(1)
        if rec.get("amount_mdl") is not None:
            expect(row).to_contain_text(money(rec["amount_mdl"]))
            if rec.get("sign"):
                expect(row).to_contain_text(rec["sign"])
        link = rec["links"][0]
        expect(row.get_by_role("link")).to_have_attribute("href", link["url"])
        expect(row.get_by_role("link")).to_have_text(link["title"])


def inbox_item(page: Page, title: str):
    return main(page).locator("div").filter(has=page.get_by_text(title, exact=True)).filter(
        has=page.get_by_role("link", name="Разобрать")).last


# Кейс: Инбокс — пункты, суммы и примеры = API
def test_inbox_matches_api(fx_page: Page):
    with fx_page.expect_response(lambda r: r.url.endswith("/api/inbox/")) as resp:
        open_fx(fx_page, "/admin/inbox")
    data = resp.value.json()
    # в шапке два одинаковых заголовка со счётчиком (title «Инбокс»), видимый — один; счётчик = число
    # пунктов (проблем), а count в API — сумма случаев по пунктам («1 случай» + «2 случая» = 3, в шапке 2)
    assert data["count"] == sum(it["count"] for it in data["items"]), data
    expect(fx_page.locator("header [title='Инбокс']:visible")).to_have_text(re.compile(rf"Инбокс\s*{len(data['items'])}$"))
    if data["clean"]:
        expect(main(fx_page)).to_contain_text("Инбокс пуст")
        return
    for it in data["items"]:
        item = inbox_item(fx_page, it["title"])
        expect(item).to_contain_text(it["action"])
        if it["amount_mdl"]:  # у пунктов без суммы («Документы ждут решения») сумма не выводится
            expect(item).to_contain_text(money(it["amount_mdl"]))
        expect(item.get_by_role("link", name="Разобрать")).to_have_attribute("href", it["url"])
    # «Показать примеры» есть только у пунктов с примерами (sample не пуст)
    with_samples = [it for it in data["items"] if it.get("sample")]
    if not with_samples:
        return
    first = with_samples[0]
    item = inbox_item(fx_page, first["title"])
    item.get_by_role("button", name=re.compile(r"^Показать примеры")).click()
    for s in first["sample"]:
        expect(item.locator(f"a[href='{s['url']}']")).to_contain_text(s["title"])
    expect(item.get_by_role("button", name="Свернуть примеры")).to_be_visible()


# Кейс: Инбокс — «Отложить на неделю» и «Вернуть»
@pytest.mark.writes_data
def test_inbox_snooze_and_return(fx_page: Page):
    data = fx_json(fx_get(fx_page, "/inbox/"))
    if not data["items"]:
        pytest.skip("инбокс пуст — откладывать нечего")
    it = data["items"][0]
    open_fx(fx_page, "/admin/inbox")
    try:
        with fx_page.expect_response(lambda r: f"/api/inbox/{it['kind']}/snooze" in r.url and r.request.method == "POST") as resp:
            inbox_item(fx_page, it["title"]).get_by_role("button", name="Отложить на неделю").click()
        assert resp.value.ok, resp.value.text()
        snoozed = main(fx_page).locator("div").filter(
            has=fx_page.get_by_role("heading", name=re.compile(r"^Отложено: \d+$"))).last
        expect(snoozed).to_contain_text(it["title"])
        # с 04.11 дата по-человечески «11.11.2026» (раньше ISO «2026-11-11»)
        expect(snoozed).to_contain_text(f"отложено до {(TODAY + dt.timedelta(days=7)):%d.%m.%Y}")
        assert it["kind"] in [s["kind"] for s in fx_json(fx_get(fx_page, "/inbox/"))["snoozed"]]

        with fx_page.expect_response(lambda r: f"/api/inbox/{it['kind']}/snooze" in r.url and r.request.method == "DELETE") as back:
            snoozed.get_by_role("button", name="Вернуть").click()
        assert back.value.ok
        expect(inbox_item(fx_page, it["title"]).get_by_role("button", name="Отложить на неделю")).to_be_visible()
        assert it["kind"] in [i["kind"] for i in fx_json(fx_get(fx_page, "/inbox/"))["items"]]
    finally:
        fx_delete(fx_page, f"/inbox/{it['kind']}/snooze")


# ───────────────────────── документы ─────────────────────────

STATUS_TABS = {"waiting": "Ждут решения", "processing": "В обработке", "done": "Внесено", "rejected": "Отклонено", "all": "Все"}


# Кейс: Документы — счётчики вкладок = API, почта для документов не настроена
def test_documents_counts_match_api(fx_page: Page):
    with fx_page.expect_response(lambda r: "/api/documents?" in r.url) as resp:
        open_fx(fx_page, "/admin/documents")
    counts = resp.value.json()["counts"]
    for key, label in STATUS_TABS.items():
        expect(main(fx_page).get_by_role("button", name=f"{label}{counts[key]}", exact=True)).to_be_visible()
    mailbox = fx_json(fx_get(fx_page, "/documents/mailbox"))
    if not mailbox["configured"]:
        expect(main(fx_page).get_by_role("button", name="Почта для документов не настроена")).to_be_visible()


@pytest.fixture
def statement_file(tmp_path):
    tag = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")  # без «:» — имя файла для Windows
    path = tmp_path / f"qa-autotest-{tag}.csv"
    path.write_text("Дата;Описание;Приход;Расход;Контрагент;IBAN\n"
                    f"{TODAY:%d.%m.%Y};{QA_PREFIX} документ {tag};150,00;;{QA_PREFIX} Клиент;MD24AG000225100013104168\n",
                    encoding="utf-8")
    return path


# Кейс: Документ — CSV-выписка узнаётся без модели, разбор в карточке, «Отклонить» с причиной
@pytest.mark.writes_data
def test_document_upload_and_reject(fx_page: Page, statement_file):
    open_fx(fx_page, "/admin/documents")
    with fx_page.expect_response(lambda r: "/api/documents/upload" in r.url) as resp:
        main(fx_page).locator("input[type=file]").set_input_files(statement_file)
    assert resp.value.ok, resp.value.text()
    try:
        doc_id = next(d["id"] for d in fx_json(fx_get(fx_page, "/documents", {"filter": "all", "limit": 100}))["items"]
                      if d["filename"] == statement_file.name)
        # экран после загрузки перечитывает список один раз и больше не опрашивает (bord-42):
        # если разбор не успел — строка так и висит «в очереди». Ждём разбор по API и
        # перечитываем страницу, как сделал бы человек
        for _ in range(30):
            doc = fx_json(fx_get(fx_page, f"/documents/{doc_id}"))
            if doc["status"] not in ("queued", "processing"):
                break
            fx_page.wait_for_timeout(1_000)
        assert (doc["kind"], doc["kind_source"], doc["status"]) == ("bank_statement", "parser", "proposed"), doc
        fx_page.reload()
        row = main(fx_page).locator("tbody tr").filter(has_text=statement_file.name)
        expect(row).to_contain_text("Готово к внесению")
        expect(row).to_contain_text("Выписка банка")

        row.get_by_text(statement_file.name, exact=True).click()
        expect(fx_page).to_have_url(re.compile(rf"/admin/documents/{doc_id}$"))
        expect(fx_page).to_have_title(f"{statement_file.name} — Facturi")
        card = main(fx_page)
        expect(card).to_contain_text("узнан без модели")
        expect(card.get_by_role("row").filter(has_text="Период")).to_contain_text(f"{TODAY:%d.%m.%Y} – {TODAY:%d.%m.%Y}")
        expect(card.get_by_role("row").filter(has_text="Валюта")).to_contain_text("MDL")
        expect(card.get_by_role("row").filter(has_text="Операций")).to_contain_text("1")

        card.get_by_role("button", name="Отклонить", exact=True).click()
        box = fx_page.locator("div.fixed").filter(has_text="Отклонить документ").last
        expect(box).to_contain_text("Причина обязательна")
        submit = box.get_by_role("button", name="Отклонить", exact=True)
        expect(submit).to_be_disabled()
        reason = f"{QA_PREFIX} — проверка отклонения"
        box.get_by_placeholder("Например: не наш документ").fill(reason)
        with fx_page.expect_response(lambda r: f"/api/documents/{doc_id}/reject" in r.url) as rej:
            submit.click()
        assert rej.value.ok, rej.value.text()
        expect(card).to_contain_text(f"Причина решения: {reason}")
        expect(card.get_by_role("button", name="Внести")).to_have_count(0)
        got = fx_json(fx_get(fx_page, f"/documents/{doc_id}"))
        assert (got["status"], got["decision_reason"], got["can_record"]) == ("rejected", reason, False), got
    finally:  # удаления нет — свой файл, если тест упал до «Отклонить», отклоняем по имени
        for d in fx_json(fx_get(fx_page, "/documents", {"filter": "all", "limit": 100}))["items"]:
            if d["filename"] == statement_file.name and d["status"] != "rejected":
                fx_post(fx_page, f"/documents/{d['id']}/reject", {"reason": f"{QA_PREFIX} — уборка"})


# ───────────────────────── подтвердить накладные ─────────────────────────

# Кейс: «Подтвердить накладные» — фильтры «Мои / Все» × «Ждут решения / Решённые» = API
def test_efactura_approvals_filters(fx_page: Page):
    with fx_page.expect_response(lambda r: "/api/efactura/approvals?" in r.url) as first:
        open_fx(fx_page, "/admin/efactura-approvals")
    assert "scope=mine&state=awaiting" in first.value.url
    m = main(fx_page)
    for button, query in (("Все", "scope=all&state=awaiting"), ("Решённые", "scope=all&state=decided"),
                          ("Не распределено", "scope=unassigned&state=decided")):
        with fx_page.expect_response(lambda r: "/api/efactura/approvals?" in r.url) as resp:
            m.get_by_role("button", name=button, exact=True).click()
        assert query in resp.value.url, resp.value.url
        assert resp.value.ok
        if not resp.value.json()["items"]:
            # «Решённых накладных пока нет.» / «Здесь пусто — все ждущие накладные распределены.»
            expect(m).to_contain_text(re.compile("пока нет|Здесь пусто"))


@pytest.fixture
def approval_rules(fx_page: Page):
    yield
    for r in fx_json(fx_get(fx_page, "/efactura/approvals/rules"))["items"]:
        if (r["supplier_name"] or "").startswith(QA_PREFIX):
            fx_delete(fx_page, f"/efactura/approvals/rules/{r['id']}")


# Кейс: Правило «поставщик → сотрудник» — добавить и снять
@pytest.mark.writes_data
def test_efactura_approval_rule(fx_page: Page, approval_rules):
    me = next(x for x in fx_json(fx_get(fx_page, "/efactura/approvals/members"))["items"] if x["id"] == 1717)
    open_fx(fx_page, "/admin/efactura-approvals")
    m = main(fx_page)
    idno, name = unique_idno(), f"{QA_PREFIX} Поставщик {stamp()}"
    m.get_by_label("ИДНО поставщика").fill(idno)
    m.get_by_label("Название", exact=True).fill(name)
    m.locator("button[aria-haspopup=listbox]").last.click()
    fx_page.get_by_role("option", name=me["name"], exact=True).click()
    with fx_page.expect_response(lambda r: "/api/efactura/approvals/rules" in r.url and r.request.method == "PUT") as resp:
        m.get_by_role("button", name="Добавить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    expect(m).to_contain_text("Правило сохранено")
    row = m.get_by_role("row").filter(has_text=f"ИДНО {idno}")
    expect(row).to_contain_text(name)
    expect(row).to_contain_text(me["name"])
    rule = next(r for r in fx_json(fx_get(fx_page, "/efactura/approvals/rules"))["items"] if r["supplier_idno"] == idno)
    assert (rule["supplier_name"], rule["responsible_id"]) == (name, 1717), rule

    row.get_by_title("Снять правило").click()
    with fx_page.expect_response(lambda r: f"/api/efactura/approvals/rules/{rule['id']}" in r.url and r.request.method == "DELETE") as gone:
        text = confirm(fx_page, "Да")
    assert "Снять правило?" in text
    assert gone.value.ok
    expect(row).to_have_count(0)
    assert idno not in [r["supplier_idno"] for r in fx_json(fx_get(fx_page, "/efactura/approvals/rules"))["items"]]


# ───────────────────────── юнит-экономика ─────────────────────────

@pytest.fixture
def books(fx_page: Page):
    data = Books(fx_page)
    yield data
    data.cleanup()


def open_unit_economics(page: Page) -> dict:
    with page.expect_response(lambda r: "/api/unit-economics/summary" in r.url) as resp:
        open_fx(page, "/admin/unit-economics")
    return resp.value.json()


def tile(page: Page, label: str):
    """Плитка сводки: подпись — первое вхождение текста (ниже те же слова в легендах графиков)."""
    return main(page).get_by_text(label, exact=True).first.locator("xpath=..")


# Кейс: Юнит-экономика — плитки, свой клиент и своя бизнес-линия = API
@pytest.mark.writes_data
def test_unit_economics_matches_api(fx_page: Page, books):
    summary = open_unit_economics(fx_page)
    assert summary["period"] == {"from": f"{TODAY.year}-01-01", "to": str(TODAY)}, summary["period"]
    for label, key in (("Выручка", "total_revenue"), ("Валовая прибыль", "gross_profit"), ("Чистая прибыль", "net_profit"),
                       ("ARPC", "arpc")):
        expect(tile(fx_page, label)).to_contain_text(money(summary[key]))
    expect(tile(fx_page, "Активные клиенты")).to_contain_text(str(summary["active_customers"]))

    customers = fx_json(fx_get(fx_page, "/unit-economics/by-customer",
                               {"date_from": f"{TODAY.year}-01-01", "date_to": str(TODAY), "limit": 20}))["customers"]
    own = next(c for c in customers if c["counterparty_id"] == books.cp["id"])
    assert (own["revenue"], own["invoice_count"]) == (500, 1), own  # расход 120 платежом без документа — не здесь
    row = main(fx_page).get_by_role("row").filter(has_text=books.cp["name"])
    expect(row).to_contain_text(money(500))
    line_row = main(fx_page).get_by_role("row").filter(has_text=books.line["name"])
    expect(line_row).to_contain_text(money(500))
    expect(main(fx_page)).to_contain_text("С распределением overhead")


# Кейс: Юнит-экономика — «Без» аллокации и период до прошлого месяца
@pytest.mark.writes_data
def test_unit_economics_toggle_and_period(fx_page: Page, books):
    open_unit_economics(fx_page)
    # первый запрос линий (with_allocation=true) может ещё лететь — ждём именно ответ на «Без»
    with fx_page.expect_response(lambda r: "/api/unit-economics/by-business-line" in r.url
                                 and "with_allocation=false" in r.url) as resp:
        fx_page.locator("header").get_by_role("button", name="Без", exact=True).click()
    assert resp.value.ok
    expect(main(fx_page)).to_contain_text("Без распределения overhead")
    expect(main(fx_page).get_by_role("columnheader", name=re.compile("Распред. overhead", re.I))).to_have_count(0)

    last_month_end = TODAY.replace(day=1) - dt.timedelta(days=1)
    with fx_page.expect_response(lambda r: "/api/unit-economics/by-customer" in r.url
                                 and f"date_to={last_month_end}" in r.url) as resp:
        fx_page.locator("header input[type=date]").last.fill(str(last_month_end))
    assert books.cp["id"] not in [c["counterparty_id"] for c in resp.value.json()["customers"]]
    expect(main(fx_page).get_by_role("row").filter(has_text=books.cp["name"])).to_have_count(0)


# Кейс: Юнит-экономика — «Структура выручки» в валюте организации (MDL), а не в долларах
@pytest.mark.writes_data
@pytest.mark.xfail(strict=True, reason="bord-41: «Структура каждых $100 выручки», «$100.00 из $100» при учёте в MDL")
def test_unit_economics_structure_currency(fx_page: Page, books):
    open_unit_economics(fx_page)
    block = main(fx_page).locator("div").filter(has=fx_page.get_by_text(re.compile(r"^Структура каждых"))).last
    expect(block).to_be_visible()
    expect(block).not_to_contain_text("$", timeout=2_000)
