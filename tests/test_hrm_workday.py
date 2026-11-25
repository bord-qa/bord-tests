"""Блок 76. HRM — плагин «Рабочий день» (с ~20.11.2026): начать / пауза / продолжить / завершить,
«Мои дни», «Команда», исправления.

Снято 25.11.2026 в видимом MCP-браузере + справка help.bord.one/hrm/workday:
- плагин — «Настройки» → «Плагины» → «Рабочий день» = /plugins {workday_timesheet}; выключен — все
  /workday/* отвечают 404 «Плагин не включён в настройках HRM», пункта меню и кнопки в шапке нет;
- кнопка button[title="Рабочий день"] стоит в шапке ЕДИНОГО ОКНА (app.bord.one/admin/one), в классическом
  виде её нет. Надпись — состояние: «Не начат», «Идёт 0:00», «Пауза 0:00», «Завершён 0:00». Клик открывает
  меню «Рабочий день: <состояние>» с кнопками «Начать день» / «Пауза» / «Продолжить» / «Завершить день»
  (+ ссылка «Как это работает»); у завершённого дня — «Продолжить день» (тот же день); после действия меню закрывается. «Завершить день» спрашивает
  [data-bord-dialogs] form[role=dialog] «Завершить рабочий день?» → «Отмена» / «Завершить».
  Повторный «Начать» после завершения продолжает тот же день;
- hrm.bord.one/admin/workday: в шапке вкладки «Мои дни» / «Команда»; «Мои дни» — даты периода, «Итого за
  период: N ч», «Исправить день», таблица «Дата · Начало · Конец · Пауза, ч · Отработано, ч · Исправить»
  (= GET /workday/report), блок «Мои исправления» (= /workday/corrections?mine=true: «Ждёт решения» /
  «Утверждено» / «Отклонено», причина, комментарий решившего);
- «Исправить день» → окно «Исправить рабочий день» («День», «Начало», «Конец», «Пауза, мин», «Причина») →
  «Отправить» → POST /workday/corrections. Свою просьбу не утверждает никто (даже HR) — решает другой:
  руководитель, табельщик отдела, HR. qaColleague (lemido4693) уже решал просьбы тест-учётки;
- «Команда» = GET /workday/team: «Итого команды за период», строки «Сотрудник · Сейчас · Сегодня, ч · Дней
  · За период, ч · Закрыто авто».
Настройки компании (автозакрытие, напоминание, лимит паузы) — только API, тест их не меняет.
Плагин после теста возвращается как был. Исправления тест отклоняет — табель не меняется.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Browser, Page, expect

from helpers.hrm import QA_PREFIX, hrm_get, hrm_json, hrm_post, hrm_put, items, open_hrm, screen_tab, screen_action, sidebar

pytestmark = pytest.mark.requires_auth

ONE = "https://app.bord.one/admin/one"
TODAY = dt.date.today()
TOUR_DONE = """(() => { try {
  const u = JSON.parse(localStorage.getItem('admin_user')), o = JSON.parse(localStorage.getItem('admin_current_org'));
  if (u && o) localStorage.setItem(`bord.tour.${o.id}.${u.id}`, '1');
} catch (e) {} })()"""
STATE_LABEL = {"not_started": "Не начат", "running": "Идёт", "paused": "Пауза", "finished": "Завершён"}


@pytest.fixture
def workday_on(auth_page: Page):
    before = hrm_json(hrm_get(auth_page, "/plugins"))
    hrm_put(auth_page, "/plugins", {**before, "workday_timesheet": True})
    yield before
    hrm_put(auth_page, "/plugins", before)


@pytest.fixture
def one(browser: Browser, auth_state, browser_context_args):
    """Тест-учётка в едином окне BORD One (кнопка «Рабочий день» есть только там), тур отмечен пройденным."""
    ctx = browser.new_context(**browser_context_args, storage_state=auth_state)
    ctx.add_cookies([{"name": "bord_view", "value": "one", "domain": ".bord.one", "path": "/",
                      "secure": True, "sameSite": "Lax"}])
    ctx.add_init_script(TOUR_DONE)
    page = ctx.new_page()
    yield page
    ctx.close()


def workday_button(page: Page):
    # шапка продублирована (десктоп / мобильная) — берём видимую
    return page.locator('button[title="Рабочий день"]:visible').first


def menu_action(page: Page, name: str):
    """Открыть меню кнопки и нажать действие; меню после действия закрывается само."""
    btn = workday_button(page)
    action = btn.locator("xpath=..").get_by_role("button", name=name, exact=True)
    if not action.is_visible():
        btn.click()
    expect(action).to_be_visible()
    with page.expect_response(lambda r: "/workday/" in r.url and r.request.method == "POST") as resp:
        action.click()
    assert resp.value.ok, resp.value.text()
    return resp.value


def status(page: Page) -> dict:
    return hrm_json(hrm_get(page, "/workday/status"))


# Кейс: Плагин выключен — /workday/* = 404, нет пункта меню; включён — «Рабочий день» в меню HRM
@pytest.mark.writes_data
def test_workday_plugin_toggle(auth_page: Page):
    before = hrm_json(hrm_get(auth_page, "/plugins"))
    assert "workday_timesheet" in before, before
    try:
        hrm_put(auth_page, "/plugins", {**before, "workday_timesheet": False})
        off = hrm_get(auth_page, "/workday/status")
        assert off.status == 404 and "Плагин не включён" in off.text(), off.text()
        open_hrm(auth_page, "/admin")
        expect(sidebar(auth_page).get_by_role("link", name="Рабочий день", exact=True)).to_have_count(0)

        hrm_put(auth_page, "/plugins", {**before, "workday_timesheet": True})
        assert status(auth_page)["state"] in STATE_LABEL
        auth_page.reload()
        link = sidebar(auth_page).get_by_role("link", name="Рабочий день", exact=True)
        expect(link).to_have_attribute("href", "/admin/workday")
    finally:
        hrm_put(auth_page, "/plugins", before)


# Кейс: Кнопка в шапке единого окна — «Начать день» → «Пауза» → «Продолжить» → «Завершить день» (с подтверждением);
# надпись кнопки и /workday/status совпадают на каждом шаге, день появляется в «Мои дни»
@pytest.mark.writes_data
def test_workday_cycle_in_shell(workday_on, one: Page):
    one.goto(ONE)
    btn = workday_button(one)
    expect(btn).to_be_visible(timeout=20_000)
    start = status(one)
    expect(btn).to_contain_text(STATE_LABEL[start["state"]])
    if start["state"] in ("running", "paused"):  # день остался открытым от прошлого прогона — закрываем
        if start["state"] == "paused":
            menu_action(one, "Продолжить")
        menu_action(one, "Завершить день")
        one.locator("[data-bord-dialogs] form[role=dialog]").get_by_role("button", name="Завершить").click()

    # не начат — «Начать день»; завершён (прошлый прогон сегодня) — «Продолжить день», тот же день, второго нет
    again = status(one)["state"] == "finished"
    menu_action(one, "Продолжить день" if again else "Начать день")
    expect(btn).to_contain_text("Идёт")
    st = status(one)
    assert (st["state"], st["work_date"]) == ("running", TODAY.isoformat()), st

    menu_action(one, "Пауза")
    expect(btn).to_contain_text("Пауза")
    assert status(one)["state"] == "paused"

    menu_action(one, "Продолжить")
    expect(btn).to_contain_text("Идёт")
    assert status(one)["state"] == "running"

    # «Завершить день» — подтверждение; «Отмена» день не закрывает
    btn.click()
    btn.locator("xpath=..").get_by_role("button", name="Завершить день", exact=True).click()
    dialog = one.locator("[data-bord-dialogs] form[role=dialog]")
    expect(dialog).to_have_attribute("aria-label", "Завершить рабочий день?")
    dialog.get_by_role("button", name="Отмена", exact=True).click()
    expect(dialog).to_have_count(0)
    assert status(one)["state"] == "running"

    btn.click()
    btn.locator("xpath=..").get_by_role("button", name="Завершить день", exact=True).click()
    with one.expect_response(lambda r: r.url.endswith("/workday/finish") and r.request.method == "POST") as fin:
        dialog.get_by_role("button", name="Завершить", exact=True).click()
    assert fin.value.ok, fin.value.text()
    expect(btn).to_contain_text("Завершён")
    st = status(one)
    assert st["state"] == "finished" and st["ended_at"], st

    report = hrm_json(hrm_get(one, "/workday/report", {"date_from": TODAY.isoformat(), "date_to": TODAY.isoformat()}))
    assert len(report["days"]) == 1, report
    one.goto("https://hrm.bord.one/admin/workday?standalone=1")
    row = one.locator("main tr").filter(has_text=TODAY.strftime("%d.%m.%Y"))
    expect(row).to_have_count(1, timeout=20_000)
    expect(row).to_contain_text(TODAY.strftime("%d.%m.%Y"))


def free_past_day(page: Page) -> dt.date:
    """Будний день 20–55 дней назад без своих исправлений (окно исправлений — 62 дня)."""
    taken = {c.get("work_date") for c in items(hrm_json(hrm_get(page, "/workday/corrections", {"mine": "true"})))}
    for back in range(20, 56):
        day = TODAY - dt.timedelta(days=back)
        if day.weekday() < 5 and day.isoformat() not in taken:
            return day
    pytest.skip("нет свободного дня для исправления в окне 20–55 дней")


# Кейс: «Исправить день» (коллега, роль «Сотрудник») → у него «Ждёт решения»; свою просьбу утвердить нельзя (403);
# тест-учётка (HR) видит её в «Команда» → «Ждут вашего решения», отклоняет с комментарием → у коллеги «Отклонено»
@pytest.mark.writes_data
def test_workday_correction_rejected(workday_on, auth_page: Page, colleague_page: Page):
    day = free_past_day(colleague_page)
    reason = f"{QA_PREFIX} исправление {dt.datetime.now():%H%M%S}"
    open_hrm(colleague_page, "/admin/workday")
    screen_action(colleague_page, "Исправить день").click()
    box = colleague_page.locator("div.fixed.inset-0:visible").filter(has_text="Исправить рабочий день").last
    box.locator("input[type=date]").fill(day.isoformat())
    times = box.locator("input[type=time]")
    times.nth(0).fill("09:00")
    times.nth(1).fill("17:30")
    box.locator("input[type=number]").fill("30")
    box.locator("textarea").fill(reason)
    with colleague_page.expect_response(lambda r: r.url.endswith("/workday/corrections") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Отправить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    corr_id = resp.value.json()["id"]
    try:
        mine = next(c for c in items(hrm_json(hrm_get(colleague_page, "/workday/corrections", {"mine": "true"})))
                    if c["id"] == corr_id)
        assert (mine["work_date"], mine["reason"], mine["status"]) == (day.isoformat(), reason, "pending"), mine
        line = colleague_page.locator("main").locator("div").filter(has_text=reason).last
        expect(line).to_contain_text("Ждёт решения")
        expect(line).to_contain_text(f"{day:%d.%m.%Y} · 09:00–17:30 · пауза 30 мин")

        own = hrm_post(colleague_page, f"/workday/corrections/{corr_id}/decide", {"approve": True})
        assert own.status == 403, f"свою просьбу утвердили сами: {own.status} {own.text()[:200]}"

        waiting = items(hrm_json(hrm_get(auth_page, "/workday/corrections")))
        assert corr_id in [c["id"] for c in waiting], waiting
        open_hrm(auth_page, "/admin/workday")
        screen_tab(auth_page, "Команда").click()
        main_ = auth_page.locator("main")
        expect(main_).to_contain_text("Ждут вашего решения")
        entry = main_.locator("div, tr").filter(has_text=reason).filter(
            has=auth_page.get_by_role("button", name=re.compile("^Отклонить"))).last
        assert entry.count(), "нет строки просьбы с «Отклонить»: " + main_.inner_text()[:1500]
        entry.get_by_role("button", name=re.compile("^Отклонить")).click()
        comment = f"{QA_PREFIX} отклонено"
        ask = auth_page.locator("[data-bord-dialogs] form[role=dialog], div.fixed.inset-0:visible").last
        field_ = ask.locator("textarea, input[type=text]").first
        if field_.count():
            field_.fill(comment)
        with auth_page.expect_response(lambda r: f"/corrections/{corr_id}/decide" in r.url) as dec:
            ask.get_by_role("button", name=re.compile("^(Отклонить|OK|Ок|Да)$")).last.click()
        assert dec.value.ok and dec.value.request.post_data_json["approve"] is False, dec.value.text()
        corr_id = None
    finally:
        if corr_id:  # тест упал до решения — не оставляем висящую просьбу
            hrm_post(auth_page, f"/workday/corrections/{corr_id}/decide", {"approve": False, "comment": f"{QA_PREFIX} уборка"})

    colleague_page.reload()
    line = colleague_page.locator("main").locator("div").filter(has_text=reason).last
    expect(line).to_contain_text("Отклонено")
    report = hrm_json(hrm_get(colleague_page, "/workday/report", {"date_from": day.isoformat(), "date_to": day.isoformat()}))
    assert report["days"] == [] or report["days"][0].get("start") != "09:00", report


# Кейс: Нельзя просить исправление будущего дня — 409 «Будущий день исправить нельзя»
@pytest.mark.writes_data
def test_workday_correction_future_rejected(workday_on, auth_page: Page):
    tomorrow = TODAY + dt.timedelta(days=1)
    resp = hrm_post(auth_page, "/workday/corrections", {"work_date": tomorrow.isoformat(), "start": "09:00",
                                                       "end": "18:00", "pause_minutes": 60, "reason": f"{QA_PREFIX} будущее"})
    assert resp.status == 409 and "Будущий день исправить нельзя" in resp.text(), f"{resp.status}: {resp.text()[:200]}"


# Кейс: «Команда» = /workday/team — каждая строка API есть в таблице с состоянием «Сейчас»
def test_workday_team(workday_on, auth_page: Page):
    open_hrm(auth_page, "/admin/workday")
    screen_tab(auth_page, "Команда").click()
    first = TODAY.replace(day=1).isoformat()
    team = hrm_json(hrm_get(auth_page, "/workday/team", {"date_from": first, "date_to": TODAY.isoformat()}))
    people = team["items"] if isinstance(team, dict) and "items" in team else team.get("people", team)
    assert people, team
    table = auth_page.locator("main table")
    expect(table).to_contain_text("Сотрудник")
    expect(auth_page.locator("main")).to_contain_text("Итого команды за период")
    for p in people:
        name = p.get("name") or p.get("user_name")
        expect(table.locator("tr").filter(has_text=re.compile(re.escape(name)))).to_have_count(1)
