"""Блок 46. Facturi — зарплата: ведомости, сотрудники, отсутствия, премии, календарь, справочники.

Снято 02.11.2026:
- «Сотрудники» (/admin/salary/employees) = GET /api/salary/staff; «Добавить» → «Новый
  сотрудник» (ФИО, Должность, Работает с / по, статья, направление, зарплатный счёт, ИДНП,
  карта, «Начисление», Оклад, Вид net/gross (по умолчанию «на руки (net)»), Действует с).
  Удаления у сотрудника нет — выключаем (is_active=false); тесты берут выключенного
  «QA Autotest Сотрудник» и включают снова, новый заводит только тест формы;
- «Отсутствия» — форма прямо на странице («Сотрудник…» — из /salary/employees, где
  сотрудников из «Сотрудников» нет: bord-35, xfail; Отпуск / Больничный /
  Неоплачиваемое, Днями / Часами, С, По, «Добавить»), итоги месяца «Норма / Отработано /
  Отпуск …» = /salary/absences/summary; удаляются;
- «Премии по правилам» — «Правило» → «Новое правило премии»; «Попадёт в ведомость — N»
  = /salary/bonus-rules/preview; удаляются;
- «Производственный календарь» — год = /salary/calendar/year (рабочие дни / часы по
  месяцам с праздниками Молдовы), ручная правка дня — /salary/calendar/overrides (удаляется);
- «Справочники» — «Начисления (N) / Удержания (N) / Взносы (N)»; «Добавить» → «Создать тип
  начисления» открывается и владельцу, но сохранить может только оператор платформы
  («Виды начислений — общий справочник платформы…»);
- «Ведомости» (/admin/salary) — «Сформировать за месяц» сразу делает черновик за ПРОШЛЫЙ
  месяц (тот же месяц — «черновик пересобран»): строка сотрудника «полный месяц, оклад …;
  отработано N из M ч, отпуск …», премии по правилам; «Утвердить» (утверждённая ведомость
  попадает в P&L), отмена — с причиной. Ведомость НЕ удаляется никогда («Ведомость не
  удаляется — отмените её с причиной») — тестовые копятся отменёнными.
В BORD QA других сотрудников нет — ведомость состоит из тестового.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.facturi import (QA_PREFIX, field, fx_delete, fx_get, fx_json, fx_post, fx_put, maybe_confirm, modal,
                             open_fx, pick, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()
YEAR_START = TODAY.replace(month=1, day=1)
PREV_MONTH = (TODAY.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
MONTHS_RU = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]


def money(amount: float) -> str:
    whole, frac = f"{abs(amount):,.2f}".split(".")
    return r"\s".join(re.escape(p) for p in whole.split(",")) + f",{frac}"


class Payroll:
    def __init__(self, page: Page):
        self.page = page
        self.absences: list[int] = []
        self.rules: list[int] = []
        self.types: list[tuple[str, int]] = []
        self.overrides: list[str] = []
        self.statements_before = {s["id"] for s in fx_json(fx_get(page, "/salary/statements", {"limit": 200}))["items"]}
        self.staff = self._staff()

    def _staff(self) -> dict:
        """Выключенный «QA Autotest Сотрудник» прошлых прогонов — включаем; нет — заводим."""
        name = f"{QA_PREFIX} Сотрудник"
        spare = [s for s in fx_json(fx_get(self.page, "/salary/staff")) if s["full_name"] == name]
        body = {"full_name": name, "position": "Тестировщик", "start_date": str(YEAR_START), "is_active": True}
        if spare:
            st = fx_json(fx_put(self.page, f"/salary/staff/{spare[0]['id']}", {**body, "end_date": None}))
        else:
            st = fx_json(fx_post(self.page, "/salary/staff", {**body, "salary_kind": "gross", "salary_amount": 10000,
                                                              "salary_currency": "MDL", "salary_valid_from": str(YEAR_START)}))
        fx_json(fx_post(self.page, f"/salary/staff/{st['id']}/salaries", {
            "kind": "gross", "amount": 10000, "currency": "MDL", "valid_from": str(YEAR_START), "basis": "month"}))
        return next(s for s in fx_json(fx_get(self.page, "/salary/staff")) if s["id"] == st["id"])

    def cleanup(self):
        p = self.page
        for st in fx_json(fx_get(p, "/salary/statements", {"limit": 200}))["items"]:
            if st["id"] not in self.statements_before and st["status"] != "cancelled":
                fx_post(p, f"/salary/statements/{st['id']}/cancel", {"reason": "уборка автотеста"})
        for ab in self.absences:
            fx_delete(p, f"/salary/absences/{ab}")
        for rule in fx_json(fx_get(p, "/salary/bonus-rules/"))["items"]:
            if rule["name"].startswith(QA_PREFIX):
                fx_delete(p, f"/salary/bonus-rules/{rule['id']}")
        for kind, type_id in self.types:
            fx_delete(p, f"/salary/{kind}/{type_id}")
        for day in self.overrides:
            fx_delete(p, f"/salary/calendar/overrides/{day}")
        for st in fx_json(fx_get(p, "/salary/staff")):
            if st["full_name"].startswith(QA_PREFIX) and st["is_active"]:
                fx_put(p, f"/salary/staff/{st['id']}", {"is_active": False})


@pytest.fixture
def pay(fx_page: Page):
    data = Payroll(fx_page)
    yield data
    data.cleanup()


def head(page: Page):
    return page.locator("header.sticky")


# ───────────────────────── сотрудники ─────────────────────────

# Кейс: Новый сотрудник через форму — оклад gross
@pytest.mark.writes_data
def test_staff_create_via_form(fx_page: Page, pay):
    name = f"{QA_PREFIX} Сотрудник {stamp()}"
    with fx_page.expect_response(lambda r: "/api/salary/staff" in r.url and r.request.method == "GET"):
        open_fx(fx_page, "/admin/salary/employees")
    fx_page.locator("main").get_by_role("button", name="Добавить", exact=True).click()
    form = modal(fx_page, "Новый сотрудник")
    field(form, "ФИО").fill(name)
    field(form, "Должность").fill("Бухгалтер")
    field(form, "Работает с").fill(str(YEAR_START))
    field(form, "Оклад").fill("12000")
    pick(fx_page, form, "Вид", re.compile(r"gross"))
    field(form, "Действует с").fill(str(YEAR_START))
    with fx_page.expect_response(lambda r: r.url.endswith("/api/salary/staff") and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    st = resp.value.json()

    assert (st["full_name"], st["position"], st["is_active"]) == (name, "Бухгалтер", True), st
    assert (st["current_salary"]["amount"], st["current_salary"]["kind"]) == (12000, "gross"), st["current_salary"]
    row = fx_page.locator("main").locator("tr, li, div.border").filter(has_text=name).last
    expect(row).to_contain_text(re.compile(money(12000)))
    expect(row).to_contain_text("Работает")


# Кейс: Сотрудники — список = API
def test_staff_list_matches_api(fx_page: Page, pay):
    with fx_page.expect_response(lambda r: r.url.endswith("/api/salary/staff") and r.request.method == "GET") as resp:
        open_fx(fx_page, "/admin/salary/employees")
    me = next(s for s in resp.value.json() if s["id"] == pay.staff["id"])
    row = fx_page.locator("main tr").filter(has=fx_page.get_by_text(me["full_name"], exact=True))
    expect(row).to_contain_text(me["position"])
    expect(row).to_contain_text(re.compile(money(me["current_salary"]["amount"])))


# ───────────────────────── отсутствия ─────────────────────────

def workdays(month_start: dt.date, n: int = 2) -> list[dt.date]:
    return [month_start.replace(day=d) for d in range(1, 28) if month_start.replace(day=d).weekday() < 5][:n]


# Кейс: Отпуск — итоги месяца на экране = API, удаление
@pytest.mark.writes_data
def test_absence_summary(fx_page: Page, pay):
    days = workdays(TODAY.replace(day=1))
    ab = fx_json(fx_post(fx_page, "/salary/absences/", {"employee_id": pay.staff["id"], "kind": "vacation", "unit": "days",
                                                        "date_from": str(days[0]), "date_to": str(days[1]), "note": "автотест"}))
    pay.absences.append(ab["id"])
    assert (ab["work_days"], ab["norm_hours"]) == (2, 16), ab

    with fx_page.expect_response(lambda r: "/api/salary/absences/summary" in r.url) as resp:
        open_fx(fx_page, "/admin/salary/absences")
    me = next(e for e in resp.value.json()["employees"] if e["employee_id"] == pay.staff["id"])
    assert me["by_kind"]["vacation"] == {"hours": 16, "days": 2}, me
    main = fx_page.locator("main")
    row = main.locator("tr").filter(has=fx_page.get_by_text(pay.staff["full_name"], exact=True)).first
    expect(row).to_contain_text("16 ч / 2 дн.")
    expect(row).to_contain_text(str(int(me["worked_hours"])))
    expect(main).to_contain_text(f"{days[0].strftime('%d.%m.%Y')} — {days[1].strftime('%d.%m.%Y')}")

    item = main.locator("div, li").filter(has_text=days[0].strftime("%d.%m.%Y")).filter(
        has=fx_page.get_by_role("button", name="Удалить")).last
    with fx_page.expect_response(lambda r: f"/api/salary/absences/{ab['id']}" in r.url and r.request.method == "DELETE") as gone:
        item.get_by_role("button", name="Удалить").click()
        maybe_confirm(fx_page)
    assert gone.value.ok
    pay.absences.remove(ab["id"])


# Кейс: «Новое отсутствие» — сотрудник из «Сотрудников» есть в выпадашке
@pytest.mark.xfail(reason="bord-35: выпадашка берёт /salary/employees → [], сотрудников из «Сотрудников» нет", strict=True)
def test_absence_form_lists_staff(fx_page: Page, pay):
    open_fx(fx_page, "/admin/salary/absences")
    fx_page.locator("main").get_by_role("button", name="Сотрудник…").click()
    expect(fx_page.get_by_role("option").filter(has_text=pay.staff["full_name"])).to_have_count(1, timeout=5_000)


# ───────────────────────── премии ─────────────────────────

# Кейс: Правило премии «каждый месяц» — превью месяца = API
@pytest.mark.writes_data
def test_bonus_rule_preview(fx_page: Page, pay):
    rule = fx_json(fx_post(fx_page, "/salary/bonus-rules/", {
        "name": f"{QA_PREFIX} премия {stamp()}", "trigger": "monthly", "amount_kind": "fixed", "amount": 500,
        "employee_ids": [pay.staff["id"]], "is_active": True}))
    with fx_page.expect_response(lambda r: "/api/salary/bonus-rules/preview" in r.url) as resp:
        open_fx(fx_page, "/admin/salary/bonuses")
    preview = resp.value.json()
    me = next(e for e in preview["employees"] if e["employee_id"] == pay.staff["id"])
    assert me["total"] == 500, me
    main = fx_page.locator("main")
    expect(main.locator("tr").filter(has_text=rule["name"])).to_contain_text("Каждый месяц")
    expect(main).to_contain_text(re.compile(r"Попадёт в ведомость — " + money(preview["total"])))


# Кейс: Новое правило премии — окно открывается и закрывается без сохранения
def test_bonus_rule_form_cancel(fx_page: Page, pay):
    open_fx(fx_page, "/admin/salary/bonuses")
    head(fx_page).get_by_role("button", name="Правило", exact=True).click()
    form = fx_page.locator("div.fixed.inset-0").filter(has_text="Новое правило премии").last
    expect(form).to_contain_text("Когда начислять")
    expect(form.locator("label").filter(has_text="Всем сотрудникам")).to_be_visible()
    form.get_by_role("button", name="Отмена", exact=True).click()
    expect(form).to_have_count(0)


# ───────────────────────── календарь ─────────────────────────

# Кейс: Производственный календарь — год = API
def test_calendar_year_matches_api(fx_page: Page):
    with fx_page.expect_response(lambda r: "/api/salary/calendar/year" in r.url) as resp:
        open_fx(fx_page, "/admin/salary/calendar")
    year = resp.value.json()
    months = year.get("months") or year.get("items")
    main = fx_page.locator("main")
    for i, m in enumerate(months):
        cells = main.locator("tr").filter(has=fx_page.get_by_text(MONTHS_RU[i].capitalize(), exact=True)).first.locator("td")
        expect(cells.nth(1)).to_have_text(str(m["work_days"]))
        expect(cells.nth(2)).to_have_text(str(int(m["work_hours"])))
    total_days = sum(m["work_days"] for m in months)
    expect(main.locator("tr").filter(has_text=f"Итого за {TODAY.year}")).to_contain_text(str(total_days))


# Кейс: Ручная правка дня календаря — праздник уменьшает норму месяца
@pytest.mark.writes_data
def test_calendar_override(fx_page: Page, pay):
    month = fx_json(fx_get(fx_page, "/salary/calendar/month", {"year": TODAY.year, "month": TODAY.month}))
    workday = next(d for d in (TODAY.replace(day=x) for x in range(1, 28)) if d.weekday() < 5)
    resp = fx_post(fx_page, "/salary/calendar/overrides", {"day": str(workday), "kind": "holiday", "note": "автотест"})
    assert resp.ok, resp.text()
    pay.overrides.append(str(workday))

    after = fx_json(fx_get(fx_page, "/salary/calendar/month", {"year": TODAY.year, "month": TODAY.month}))
    assert after["work_days"] == month["work_days"] - 1, (month["work_days"], after["work_days"])
    open_fx(fx_page, "/admin/salary/calendar")
    expect(fx_page.locator("main")).to_contain_text(f"{after['work_days']} дн. · {int(after['work_hours'])} ч")

    assert fx_delete(fx_page, f"/salary/calendar/overrides/{workday}").ok
    pay.overrides.remove(str(workday))
    assert fx_json(fx_get(fx_page, "/salary/calendar/month", {"year": TODAY.year, "month": TODAY.month}))["work_days"] == month["work_days"]


# ───────────────────────── справочники ─────────────────────────

# Кейс: Справочники — счётчики вкладок = API; новый вид начисления — только оператор платформы
def test_directories(fx_page: Page, pay):
    with fx_page.expect_response(lambda r: "/api/salary/contribution-types" in r.url):
        open_fx(fx_page, "/admin/salary/directories")
    main = fx_page.locator("main")
    for label, path in (("Начисления", "accrual-types"), ("Удержания", "deduction-types"), ("Взносы", "contribution-types")):
        n = len(fx_json(fx_get(fx_page, f"/salary/{path}")))
        expect(main.get_by_role("button", name=f"{label} ({n})")).to_be_visible()
    expect(main.locator("tr").filter(has_text="SALARY")).to_contain_text("Оклад")

    main.get_by_role("button", name="Добавить", exact=True).click()
    form = modal(fx_page, "Создать тип начисления")
    field(form, "Название").fill(f"{QA_PREFIX} начисление {stamp()}")
    with fx_page.expect_response(lambda r: "/api/salary/accrual-types" in r.url and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status in (400, 403), resp.value.status  # если вдруг создалось — уборка ниже
    if resp.value.ok:
        pay.types.append(("accrual-types", resp.value.json()["id"]))
    expect(fx_page.get_by_text("Виды начислений — общий справочник платформы: менять его может только оператор платформы")).to_be_visible()


# ───────────────────────── ведомости ─────────────────────────

# Кейс: «Сформировать за месяц» — черновик за прошлый месяц с тестовым сотрудником
@pytest.mark.writes_data
def test_generate_statement(fx_page: Page, pay):
    with fx_page.expect_response(lambda r: "/api/salary/statements?" in r.url):
        open_fx(fx_page, "/admin/salary")
    with fx_page.expect_response(lambda r: "/api/salary/statements/generate" in r.url) as resp:
        fx_page.locator("main").get_by_role("button", name="Сформировать за месяц").click()
    assert resp.value.ok, resp.value.text()
    data = resp.value.json()
    st = data["statement"]

    assert st["period_start"] == str(PREV_MONTH) and st["status"] == "draft", st
    line = next(l for l in st["lines"] if l["employee_id"] == pay.staff["id"])
    assert (line["salary"], line["kind"], line["amount"]) == (10000, "gross", 10000), line
    main = fx_page.locator("main")
    expect(main).to_contain_text(data["message"])
    expect(main).to_contain_text(re.compile(r"Начислено\s*" + money(st["total_accrued"])))
    expect(main.locator("tr").filter(has_text=pay.staff["full_name"]).first).to_contain_text(re.compile(money(10000)))


# Кейс: Ведомость с отпуском и премией — часы, премия и сумма к выплате
@pytest.mark.writes_data
def test_statement_with_absence_and_bonus(fx_page: Page, pay):
    days = workdays(PREV_MONTH)
    ab = fx_json(fx_post(fx_page, "/salary/absences/", {"employee_id": pay.staff["id"], "kind": "vacation", "unit": "days",
                                                        "date_from": str(days[0]), "date_to": str(days[1])}))
    pay.absences.append(ab["id"])
    fx_json(fx_post(fx_page, "/salary/bonus-rules/", {"name": f"{QA_PREFIX} премия {stamp()}", "trigger": "monthly",
                                                      "amount_kind": "fixed", "amount": 500, "employee_ids": [pay.staff["id"]],
                                                      "is_active": True}))
    st = fx_json(fx_post(fx_page, "/salary/statements/generate", {"period": str(PREV_MONTH)}))["statement"]
    line = next(l for l in st["lines"] if l["employee_id"] == pay.staff["id"])

    assert (line["bonus"], line["gross"], line["payout"]) == (500, 10500, 10500), line
    assert "отпуск 16 ч" in line["rule"], line["rule"]
    norm = fx_json(fx_get(fx_page, "/salary/calendar/month", {"year": PREV_MONTH.year, "month": PREV_MONTH.month}))
    assert f"отработано {int(norm['work_hours']) - 16} из {int(norm['work_hours'])} ч" in line["rule"], line["rule"]


# Кейс: Утвердить ведомость → сводка «Начислено» растёт; отмена с причиной — назад
@pytest.mark.writes_data
def test_statement_approve_and_cancel(fx_page: Page, pay):
    before = fx_json(fx_get(fx_page, "/salary/summary"))["total_accrued"]
    st = fx_json(fx_post(fx_page, "/salary/statements/generate", {"period": str(PREV_MONTH)}))["statement"]
    with fx_page.expect_response(lambda r: "/api/salary/summary" in r.url):
        open_fx(fx_page, "/admin/salary")
    main = fx_page.locator("main")
    main.get_by_text(st["title"]).first.click()
    with fx_page.expect_response(lambda r: f"/api/salary/statements/{st['id']}/approve" in r.url) as resp:
        main.get_by_role("button", name="Утвердить", exact=True).click()
        maybe_confirm(fx_page)
    assert resp.value.ok, resp.value.text()
    assert fx_json(fx_get(fx_page, f"/salary/statements/{st['id']}"))["status"] == "approved"
    after = fx_json(fx_get(fx_page, "/salary/summary"))["total_accrued"]
    if st["period_start"][:4] == str(TODAY.year):
        assert after == before + st["total_accrued"], (before, after, st["total_accrued"])

    cancelled = fx_post(fx_page, f"/salary/statements/{st['id']}/cancel", {"reason": "автотест: отмена"})
    assert cancelled.ok, cancelled.text()
    assert fx_json(fx_get(fx_page, "/salary/summary"))["total_accrued"] == before
    gone = fx_delete(fx_page, f"/salary/statements/{st['id']}")
    assert gone.status == 400 and "отмените её с причиной" in gone.text(), gone.text()
