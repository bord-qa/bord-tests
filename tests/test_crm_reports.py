"""Блок 29. CRM — отчёты: crm.bord.one/admin/crm/reports и /admin/crm/time-report.

Снято 29.10.2026:
- «Отчёты»: период — кнопки «Сегодня / Вчера / 7 дней / 30 дней (по
  умолчанию) / Этот месяц / Прошлый месяц» и поля «Дата начала / конца
  периода» (даты по часам браузера, в адрес не попадают); вкладки «Обзор,
  Продажи, Команда, Клиенты, Путь по этапам, Товары партнёров, Аналитика
  чатов» — в адресе ?tab=overview|sales|team|clients|flow|partners|chats;
- данные — GET /api/crm/reports/<отчёт>?date_from=…&date_to=…:
  deal-dynamics (итоги обзора), sales-funnel (воронка «Продаж»), responsible
  («Команда»), clients-distribution («Клиенты»), stage-flow (&level=stage),
  partner-products, chat-analytics (?period=7d — свой период);
- «Обзор»: «Сделок за период» (ссылка на сделки за период), «Сделок
  выиграно» + «на <сумма>» (ссылка с basis=closed&result=won), «Выручка за
  период», «Конверсия» (won / (won + lost) × 100, «100.0%»), «Средний чек»;
  суммы «3 500,00 MDL»;
- «С чего начать» (GET /onboarding/checklist): «N из 4», пункты-ссылки,
  у выполненных нет подсказки. «Скрыть» = POST /onboarding/checklist/dismiss
  без отмены — не нажимаем;
- «Отчёт по времени»: период в адресе (?from=…&to=…), GET /time-report/ →
  {total, total_minutes, crm_minutes, people_count, tasks_count, items, …};
  записи — ручное время задач (POST /tasks/<id>/time {minutes, note}),
  длительность «1ч 30м», время записи по часам браузера.

Отчёты считают всю организацию (в BORD QA — стартовые примеры на живом
сотруднике). Поэтому экран сверяется с ответом API за тот же период, а для
«Сегодня» тест сам заводит выигранную и открытую сделку на тест-учётку.
"""
import datetime as dt
import random
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import CRM, QA_PREFIX, crm_delete, crm_get, crm_me, crm_post, crm_put, open_crm

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/reports"
TIME = "/admin/crm/time-report"
TABS = {"Обзор": "overview", "Продажи": "sales", "Команда": "team", "Клиенты": "clients",
        "Путь по этапам": "flow", "Товары партнёров": "partners", "Аналитика чатов": "chats"}


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


def loose(text: str) -> re.Pattern:
    """Текст с любыми пробелами и переносами между частями: суммы на экране
    разделены неразрывными пробелами, ячейки и абзацы — табуляцией и переносами.
    Сверять с use_inner_text=True — в textContent разделителей между блоками нет."""
    return re.compile(r"[\s  ]+".join(re.escape(part) for part in text.split()))


def money(amount: float, currency: str = "MDL") -> str:
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",") + f" {currency}"


def duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    return " ".join(p for p in (f"{hours}ч" if hours else "", f"{mins}м" if mins or not hours else "") if p)


def report(page: Page, name: str, date_from: dt.date, date_to: dt.date, **params) -> dict:
    resp = crm_get(page, f"/reports/{name}", {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), **params})
    assert resp.ok, resp.text()
    return resp.json()


def browser_today(page: Page) -> dt.date:
    """«Сегодня» отчётов — по часам браузера."""
    return dt.date.fromisoformat(page.evaluate(
        "() => { const d = new Date(); return new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }"))


def main_pipeline(page: Page) -> dict:
    return next(p for p in crm_get(page, "/pipeline-statuses/pipelines").json()["items"] if p["is_main"])


def make_deal(page: Page, amount: int, won: bool = False) -> dict:
    pipeline = main_pipeline(page)
    resp = crm_post(page, "/deals/", {"title": f"{QA_PREFIX} {tag()}", "pipeline_id": pipeline["id"],
                                      "responsible_id": crm_me(page)["id"], "amount": amount})
    assert resp.status == 201, resp.text()
    deal = resp.json()
    page._qa_deals.append(deal["id"])
    if won:
        won_status = next(s for s in pipeline["statuses"] if s["is_close"] and not s["is_lost"])
        resp = crm_put(page, f"/deals/{deal['id']}/stage", {"status_id": won_status["id"], "result": "won"})
        assert resp.ok, resp.text()
    return deal


def make_task_with_time(page: Page, minutes: int) -> tuple[dict, dict, str]:
    resp = crm_post(page, "/tasks/", {"title": f"{QA_PREFIX} {tag()}", "responsible_id": crm_me(page)["id"]})
    assert resp.status == 201, resp.text()
    task = resp.json()
    page._qa_tasks.append(task["id"])
    note = f"{QA_PREFIX} заметка {tag()}"
    resp = crm_post(page, f"/tasks/{task['id']}/time", {"minutes": minutes, "note": note})
    assert resp.status == 201, resp.text()
    return task, resp.json(), note


@pytest.fixture
def reports(auth_page: Page):
    auth_page._qa_deals, auth_page._qa_tasks = [], []
    yield auth_page
    for deal_id in auth_page._qa_deals:
        crm_delete(auth_page, f"/deals/{deal_id}")
    for task_id in auth_page._qa_tasks:
        crm_delete(auth_page, f"/tasks/{task_id}")
        crm_delete(auth_page, f"/tasks/{task_id}/permanent")


def open_reports(page: Page, tab: str = "overview"):
    open_crm(page, f"{PAGE}?tab={tab}")
    # у «Аналитики чатов» свой период — общих полей дат там нет
    expect(tab_button(page, "Аналитика чатов")).to_be_visible()


def tab_button(page: Page, name: str):
    return page.locator("main").get_by_role("button", name=name, exact=True)


def choose_period(page: Page, button: str, date_from: dt.date, date_to: dt.date):
    """Кнопка периода и ожидание отчётов именно за этот период."""
    with page.expect_response(lambda r: "/api/crm/reports/" in r.url
                              and f"date_from={date_from.isoformat()}" in r.url
                              and f"date_to={date_to.isoformat()}" in r.url):
        tab_button(page, button).click()


def kpi(page: Page, label: str):
    """Плитка обзора (ссылка или блок): подпись — абзац в шапке плитки рядом с
    иконкой, значение — следующий абзац; плитка — ближайший предок с обоими."""
    return page.locator("main p").filter(has_text=re.compile(f"^{re.escape(label)}$")).locator(
        "xpath=ancestor::*[count(.//p) >= 2][1]")


def kpi_value(page: Page, label: str):
    return kpi(page, label).locator("p").nth(1)


def expect_kpis(page: Page, totals: dict):
    won, lost, amount = totals["won_deals"], totals["lost_deals"], totals["won_amount"]
    expect(kpi_value(page, "Сделок за период")).to_have_text(str(totals["new_deals"]))
    expect(kpi_value(page, "Сделок выиграно")).to_have_text(str(won))
    expect(kpi_value(page, "Выручка за период")).to_have_text(loose(money(amount)))
    if won:
        expect(kpi(page, "Сделок выиграно")).to_contain_text(loose(f"на {money(amount)}"))
        expect(kpi_value(page, "Средний чек")).to_have_text(loose(money(amount / won)))
    if won + lost:
        expect(kpi_value(page, "Конверсия")).to_have_text(f"{won / (won + lost) * 100:.1f}%")


# Кейс: Обзор по умолчанию — 30 дней, плитки совпадают с API за этот период
def test_overview_default_period_matches_api(reports: Page):
    open_reports(reports)
    today = browser_today(reports)
    start = today - dt.timedelta(days=29)

    expect(reports.get_by_role("textbox", name="Дата начала периода")).to_have_value(start.isoformat())
    expect(reports.get_by_role("textbox", name="Дата конца периода")).to_have_value(today.isoformat())
    totals = report(reports, "deal-dynamics", start, today, interval="week")["totals"]
    expect_kpis(reports, totals)


# Кейс: «Сегодня» — своя выигранная и открытая сделка попадают в плитки обзора, цифры = API
def test_overview_today_counts_own_deals(reports: Page):
    won_amount = random.randint(1000, 9000)
    make_deal(reports, won_amount, won=True)
    make_deal(reports, 700)
    open_reports(reports)
    today = browser_today(reports)

    choose_period(reports, "Сегодня", today, today)
    totals = report(reports, "deal-dynamics", today, today, interval="day")["totals"]
    assert totals["new_deals"] >= 2 and totals["won_deals"] >= 1 and totals["won_amount"] >= won_amount, totals
    expect_kpis(reports, totals)


# Кейс: Кнопки периода ставят даты в поля и запрашивают отчёты за этот период
def test_period_presets(reports: Page):
    open_reports(reports)
    today = browser_today(reports)
    first = today.replace(day=1)
    prev_last = first - dt.timedelta(days=1)
    presets = {
        "Сегодня": (today, today),
        "Вчера": (today - dt.timedelta(days=1), today - dt.timedelta(days=1)),
        "7 дней": (today - dt.timedelta(days=6), today),
        "Этот месяц": (first, today),
        "Прошлый месяц": (prev_last.replace(day=1), prev_last),
        "30 дней": (today - dt.timedelta(days=29), today),
    }
    for button, (date_from, date_to) in presets.items():
        choose_period(reports, button, date_from, date_to)
        expect(reports.get_by_role("textbox", name="Дата начала периода")).to_have_value(date_from.isoformat())
        expect(reports.get_by_role("textbox", name="Дата конца периода")).to_have_value(date_to.isoformat())


# Кейс: Свои даты в полях периода — отчёты за этот период, плитки = API
def test_custom_period(reports: Page):
    open_reports(reports)
    today = browser_today(reports)
    date_from, date_to = today - dt.timedelta(days=90), today - dt.timedelta(days=10)

    with reports.expect_response(lambda r: "/api/crm/reports/deal-dynamics" in r.url
                                 and f"date_from={date_from.isoformat()}" in r.url
                                 and f"date_to={date_to.isoformat()}" in r.url):
        reports.get_by_role("textbox", name="Дата начала периода").fill(date_from.isoformat())
        reports.get_by_role("textbox", name="Дата конца периода").fill(date_to.isoformat())
    expect_kpis(reports, report(reports, "deal-dynamics", date_from, date_to, interval="week")["totals"])


# Кейс: Плитки «Сделок за период» и «Сделок выиграно» ведут на сделки за этот период
def test_kpi_links_to_deals(reports: Page):
    open_reports(reports)
    today = browser_today(reports)
    period = f"period=custom&date_from={(today - dt.timedelta(days=29)).isoformat()}&date_to={today.isoformat()}"

    expect(kpi(reports, "Сделок за период")).to_have_attribute("href", f"/admin/crm/deals?{period}")
    expect(kpi(reports, "Сделок выиграно")).to_have_attribute(
        "href", f"/admin/crm/deals?{period}&basis=closed&result=won")
    kpi(reports, "Сделок за период").click()
    expect(reports).to_have_url(f"{CRM}/admin/crm/deals?{period}")


# Кейс: Вкладки отчётов открываются, вкладка — в адресе, по адресу открывается нужная
@pytest.mark.parametrize("tab", list(TABS))
def test_tabs_open(reports: Page, tab: str):
    errors = []
    # 429 — лимит всплеска запросов CRM (не ошибка экрана, решение юзера 27.10)
    reports.on("response", lambda r: "/api/crm/reports/" in r.url and r.status >= 400 and r.status != 429
               and errors.append(f"{r.status} {r.url}"))
    open_reports(reports)

    tab_button(reports, tab).click()
    expect(reports).to_have_url(re.compile(rf"[?&]tab={TABS[tab]}(&|$)"))
    reports.reload()
    # активная вкладка подчёркнута border-brand-500, остальные — border-transparent
    expect(tab_button(reports, tab)).to_have_class(re.compile(r"\bborder-brand-500\b"))
    if tab != "Обзор":
        expect(tab_button(reports, "Обзор")).to_have_class(re.compile(r"\bborder-transparent\b"))
    assert not errors, errors


# Кейс: «Продажи» — воронка сделок совпадает с API: стадии, «сейчас», дошли, суммы, конверсия, «ИТОГО»
def test_sales_funnel_matches_api(reports: Page):
    open_reports(reports, "sales")
    today = browser_today(reports)
    funnel = report(reports, "sales-funnel", today - dt.timedelta(days=29), today)
    dynamics = report(reports, "deal-dynamics", today - dt.timedelta(days=29), today, interval="week")["totals"]
    main = reports.locator("main")
    expect(main).to_contain_text("Воронка сделок")

    # с 15.11 воронки без сделок за период (ни «сейчас», ни «дошли») прячутся одной строкой
    by_pipeline: dict[int, list] = {}
    for stage in funnel["deal_stages"]:
        by_pipeline.setdefault(stage["pipeline_id"], []).append(stage)
    empty = [pid for pid, st in by_pipeline.items() if not any(s["count"] or s["reached"] for s in st)]
    if empty:
        expect(main).to_contain_text(f"Скрыто воронок без сделок за период: {len(empty)}.")
    else:
        expect(main).not_to_contain_text("Скрыто воронок без сделок")

    for stage in (s for s in funnel["deal_stages"] if s["pipeline_id"] not in empty):
        conversion = "—" if stage["conversion_from_prev"] is None else f"{stage['conversion_from_prev']:.1f}%"
        expect(main).to_contain_text(loose(
            f"{stage['name']} сейчас {stage['count']} {stage['reached']} {money(stage['amount'])} {conversion}"),
            use_inner_text=True)
    expect(main).to_contain_text(loose(f"ИТОГО {dynamics['new_deals']}"), use_inner_text=True)
    expect(main).to_contain_text(loose(money(dynamics["new_amount"])), use_inner_text=True)


# Кейс: «Команда» — сводка по сделкам совпадает с API ответственных; своя выигранная сделка — в строке тест-учётки
def test_team_tab_matches_api(reports: Page):
    won_amount = random.randint(1000, 9000)
    make_deal(reports, won_amount, won=True)
    open_reports(reports, "team")
    today = browser_today(reports)
    choose_period(reports, "Сегодня", today, today)

    rows = report(reports, "responsible", today, today)["responsible"]
    mine = next(r for r in rows if r["responsible_id"] == crm_me(reports)["id"])
    assert mine["won_deals"] >= 1 and mine["total_won_amount"] >= won_amount, mine
    table = reports.locator("main table").filter(has_text="ВЫРУЧКА")
    expect(table.locator("tbody tr")).to_have_count(len(rows))
    for row in rows:
        line = table.locator("tbody tr").filter(has_text=row["name"])
        expect(line).to_contain_text(loose(f"{row['name']} {row['deal_count']} {row['won_deals']} "
                                           f"{money(row['total_won_amount'])}"), use_inner_text=True)


# Кейс: «Клиенты» — всего контактов и с продажами = API
def test_clients_tab_matches_api(reports: Page):
    open_reports(reports, "clients")
    data = crm_get(reports, "/reports/clients-distribution").json()

    expect(kpi_value(reports, "Всего контактов")).to_have_text(str(data["total_active_contacts"]))
    expect(kpi_value(reports, "С продажами")).to_have_text(str(data["total_clients"]))


# Кейс: «Путь по этапам» — сделок за период, продано / проиграно / в работе = API
def test_flow_tab_matches_api(reports: Page):
    open_reports(reports, "flow")
    today = browser_today(reports)
    flow = report(reports, "stage-flow", today - dt.timedelta(days=29), today, level="stage")
    main = reports.locator("main")

    expect(main).to_contain_text(loose(f"Сделок за период {flow['total']}"), use_inner_text=True)
    for label, count in (("Продано", flow["won"]), ("Проиграно", flow["lost"]), ("Ещё в работе", flow["open"])):
        expect(main).to_contain_text(re.compile(rf"{label}\s+[\d,.]+%\s+{count} шт\."), use_inner_text=True)
    for node in flow["stages"][0]["nodes"]:
        expect(main).to_contain_text(node["name"])


# Кейс: «Товары партнёров» — группы сделок совпадают с API
def test_partners_tab_matches_api(reports: Page):
    open_reports(reports, "partners")
    today = browser_today(reports)
    groups = report(reports, "partner-products", today - dt.timedelta(days=29), today)["groups"]
    table = reports.locator("main table").filter(has_text="До выигрыша")

    for g in groups:
        row = table.locator("tr").filter(has_text=g["label"])
        expect(row).to_contain_text(loose(f"{g['label']} {g['deals']} {g['won']} {g['lost']} {g['open']}"),
                                    use_inner_text=True)
        expect(row).to_contain_text(loose(money(g["won_amount"])), use_inner_text=True)


# Кейс: «Аналитика чатов» — свой период «Последние 7 дней», всего чатов = API
def test_chats_tab_matches_api(reports: Page):
    open_reports(reports, "chats")
    data = crm_get(reports, "/reports/chat-analytics", {"period": "7d"}).json()
    start = dt.date.fromisoformat(data["period"]["date_from"])
    end = dt.date.fromisoformat(data["period"]["date_to"])
    main = reports.locator("main")

    expect(main).to_contain_text(loose(f"{start:%d.%m.%Y} — {end:%d.%m.%Y}"), use_inner_text=True)
    expect(main).to_contain_text(loose(f"Всего чатов {data['totals']['chats']}"), use_inner_text=True)


# Кейс: «С чего начать» совпадает с API: счётчик выполненных, пункты-ссылки, подсказки только у невыполненных
def test_onboarding_checklist_matches_api(reports: Page):
    open_reports(reports)
    checklist = crm_get(reports, "/onboarding/checklist").json()
    if checklist["dismissed"]:
        pytest.skip("«С чего начать» скрыт в организации")
    items = checklist["items"]
    done = sum(i["done"] for i in items)
    if done == len(items):
        # с 04.11 пройденный целиком чек-лист не рисуется (до этого висел «С чего начать 4 из 4»)
        expect(reports.locator("main li a")).to_have_count(0)
        expect(reports.locator("main")).not_to_contain_text(loose("С чего начать"), use_inner_text=True)
        return

    expect(reports.locator("main")).to_contain_text(
        loose(f"С чего начать {done} из {len(items)}"), use_inner_text=True)
    links = reports.locator("main li a")
    expect(links).to_have_count(len(items))
    for link, item in zip(links.all(), items):
        expect(link).to_have_attribute("href", item["href"])
        expect(link).to_contain_text(item["title"])
        if item["done"]:
            expect(link).not_to_contain_text(item["hint"])
        else:
            expect(link).to_contain_text(item["hint"])


# Кейс: Отчёт по времени — ручное время своей задачи: итоги = API, строка записи, сотрудник, ссылка на задачу
def test_time_report_matches_api(reports: Page):
    task, entry, note = make_task_with_time(reports, 90)
    today = browser_today(reports)
    first = today.replace(day=1)
    open_crm(reports, f"{TIME}?from={first.isoformat()}&to={today.isoformat()}")
    data = crm_get(reports, "/time-report/", {"from": first.isoformat(), "to": today.isoformat(), "limit": 50}).json()
    main = reports.locator("main")

    for label, value in (("Всего", duration(data["total_minutes"])), ("Записей", data["total"]),
                         ("Сотрудников", data["people_count"]), ("Задач", data["tasks_count"])):
        expect(main).to_contain_text(loose(f"{label} {value}"), use_inner_text=True)
    row = main.locator("table tbody tr").filter(has_text=note)
    started = dt.datetime.fromisoformat(entry["started_at"]).astimezone(
        dt.timezone(dt.timedelta(minutes=-reports.evaluate("new Date().getTimezoneOffset()"))))
    expect(row).to_contain_text(f"{started:%d.%m.%Y, %H:%M}")
    expect(row).to_contain_text("CRM")
    expect(row).to_contain_text(entry["user_name"])
    expect(row).to_contain_text("1ч 30м")
    expect(row.get_by_role("link", name=task["title"])).to_have_attribute("href", f"/admin/crm/tasks?task={task['id']}")


# Кейс: Отчёт по времени — «Сегодня» ставит период в адрес, поиск по заметке оставляет только свою запись
def test_time_report_period_and_search(reports: Page):
    task, _, note = make_task_with_time(reports, 45)
    open_crm(reports, TIME)
    today = browser_today(reports)
    main = reports.locator("main")

    main.get_by_role("button", name="Сегодня", exact=True).click()
    expect(reports).to_have_url(re.compile(rf"from={today.isoformat()}&to={today.isoformat()}"))
    with reports.expect_response(lambda r: "/api/crm/time-report/" in r.url and "q=" in r.url):
        main.get_by_placeholder("Задача, заметка…").fill(note)
    rows = main.locator("table tbody tr")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text(task["title"])
    expect(rows.first).to_contain_text("45м")
