"""Блок 74. HRM — поздравления (/admin/greetings, пункт меню с 08.11.2026).

Снято 25.11.2026 в видимом MCP-браузере:
- в шапке «Поздравления <N>» (N — событий в календаре) и вкладки «Ближайшие 30 дней / Журнал / Настройки»,
  кнопка «Поздравить»;
- «Ближайшие 30 дней» = GET /greetings/upcoming?days=30: строка «ДД.ММ.ГГГГ · День рождения / Годовщина
  работы / Праздник · <имя> · <возраст | лет в компании>», у отказавшегося — «· просил не поздравлять»,
  у праздника — «· не выбран для поздравлений»; кнопки «Поздравить» / «Поздравить всех». Имя здесь
  «Имя Фамилия» (как в API), а не по «Порядку ФИО» организации;
- «Поздравить» — окно «Поздравить»: «Кого (можно нескольких)», «Шаблон» (День рождения / Годовщина работы /
  Праздник), «Свой текст (необязательно)», каналы «Уведомление HRM / Письмо / Telegram», предпросмотр
  шаблона на языке сотрудника, «Отправить» → POST /greetings/send → окно «Поздравлений отправлено: 1 из 1»
  (OK); журнал = GET /greetings/log: «Когда / Кому + текст / Повод · кто отправил / Каналы / Итог»;
- «Настройки»: галочки «Автоматически» (день рождения, годовщина, выбранные праздники, одобрение в журнале,
  напоминание руководителю), час отправки, язык по умолчанию, каналы, подпись — каждое изменение сразу
  PUT /greetings/settings (кнопки «Сохранить» нет, галочка управляемая — меняется после ответа);
  «Какие праздники поздравлять» (государственные = /greetings/holidays) — только при включённых праздниках;
  «Шаблоны» по поводу и языку; таблица «Сотрудники» — «Не поздравлять» / «Скрыть возраст» / «Язык» →
  PUT /greetings/employees/<id>/prefs;
- «Не поздравлять» → отправка возвращает status=skipped; «Скрыть возраст» убирает {age} из текста.
Ручная отправка идёт сразу и при включённом «одобрении» (одобрение — для автоматических поздравлений).

Свои данные: сотрудник «QA Autotest» без email на своём юрлице, только канал «Уведомление HRM» (писем и
Telegram никому); каскадное удаление сотрудника уносит и его записи журнала. Настройки компании —
возвращаем как были. «Поздравить всех» с праздником не жмём — уйдёт живым людям.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (QA_PREFIX, clean_up, create_employee, create_legal_entity, delete_employee, header_button,
                         hrm_get, hrm_json, hrm_post, hrm_put, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()
KIND = {"birthday": "День рождения", "anniversary": "Годовщина работы", "holiday": "Праздник"}


def main(page: Page):
    return page.locator("main")


def modal(page: Page, heading: str):
    return page.locator("div.fixed.inset-0:visible").filter(
        has=page.get_by_role("heading", name=heading, exact=True)).last


def alert(page: Page):
    """Окно-сообщение BORD («Поздравлений отправлено: 1 из 1» + OK)."""
    return page.locator("[data-bord-dialogs] div.fixed.inset-0:visible").last


def row(page: Page, who: str, kind: str):
    return main(page).locator("tr").filter(has_text=who).filter(has_text=kind)


def upcoming(page: Page) -> list[dict]:
    return hrm_json(hrm_get(page, "/greetings/upcoming", {"days": 30}))["items"]


def shifted(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29.02
        return day.replace(year=day.year - years, day=28)


@pytest.fixture
def settings_back(auth_page: Page):
    """Настройки поздравлений компании — вернуть как были в любом исходе."""
    before = hrm_json(hrm_get(auth_page, "/greetings/settings"))
    yield before
    keys = ("birthday", "anniversary", "anniversary_years", "holidays", "holiday_keys", "hour", "channels",
            "approval", "notify_managers_day_before", "default_lang", "signature", "templates")
    hrm_put(auth_page, "/greetings/settings", {k: before[k] for k in keys})


@pytest.fixture
def hero(auth_page: Page):
    """Свой сотрудник: день рождения через 3 дня (30 лет) и в тот же день год работы."""
    clean_up(auth_page)
    event = TODAY + dt.timedelta(days=3)
    le = create_legal_entity(auth_page)
    first = f"{QA_PREFIX} Поздр {stamp()}"
    emp = create_employee(auth_page, le["id"], first, last_name="Тестов",
                          birth_date=str(shifted(event, 30)), hire_date=str(shifted(event, 1)))
    yield {"emp": emp, "first": first, "event": event}
    delete_employee(auth_page, emp["id"])
    clean_up(auth_page)


def own_events(page: Page, emp_id: int) -> dict:
    return {i["kind"]: i for i in upcoming(page) if i.get("employee_id") == emp_id}


# ───────────────────────── календарь ─────────────────────────

# Кейс: «Ближайшие 30 дней» = /greetings/upcoming: счётчик в шапке, даты, поводы, имена; свой сотрудник —
# день рождения с возрастом и годовщина с числом лет
@pytest.mark.writes_data
def test_greetings_calendar(auth_page: Page, hero):
    open_hrm(auth_page, "/admin/greetings")
    api = upcoming(auth_page)
    expect(auth_page.locator("header:visible").first).to_contain_text(re.compile(rf"Поздравления\s*{len(api)}"))
    expect(main(auth_page).locator("tbody tr")).to_have_count(len(api))
    for ev in api:
        r = row(auth_page, ev["name"], KIND[ev["kind"]]).filter(
            has_text=dt.date.fromisoformat(ev["date"]).strftime("%d.%m.%Y"))
        expect(r, f"нет строки события {ev}").to_have_count(1)
        if ev["kind"] == "holiday":
            expect(r.get_by_role("button", name="Поздравить всех")).to_be_visible()
            if not ev.get("selected"):
                expect(r).to_contain_text("не выбран для поздравлений")
        else:
            expect(r.get_by_role("button", name="Поздравить", exact=True)).to_be_visible()

    mine = own_events(auth_page, hero["emp"]["id"])
    assert set(mine) == {"birthday", "anniversary"}, f"свой сотрудник в календаре: {mine}"
    assert mine["birthday"]["age"] == 30 and mine["anniversary"]["years"] == 1, mine
    date = hero["event"].strftime("%d.%m.%Y")
    name = mine["birthday"]["name"]
    assert name.startswith(hero["first"]), f"имя в календаре: {name}"
    expect(row(auth_page, name, "День рождения")).to_contain_text(re.compile(rf"{date}.*· 30", re.S))
    expect(row(auth_page, name, "Годовщина работы")).to_contain_text(re.compile(rf"{date}.*· 1", re.S))


# ───────────────────────── отправка и журнал ─────────────────────────

# Кейс: «Поздравить» в строке → окно с выбранным сотрудником и шаблоном на языке компании; свой текст,
# только «Уведомление HRM» → «Поздравлений отправлено: 1 из 1» → строка журнала = API
@pytest.mark.writes_data
def test_greeting_send_and_journal(auth_page: Page, hero):
    settings = hrm_json(hrm_get(auth_page, "/greetings/settings"))
    open_hrm(auth_page, "/admin/greetings")
    name = own_events(auth_page, hero["emp"]["id"])["birthday"]["name"]
    row(auth_page, name, "День рождения").get_by_role("button", name="Поздравить", exact=True).click()
    box = modal(auth_page, "Поздравить")
    expect(box).to_contain_text(name)
    lang = settings["default_lang"]
    template = settings["templates"]["birthday"][lang] or settings["default_templates"]["birthday"][lang]
    expect(box).to_contain_text(template.split("\n")[0].replace("{first_name}", hero["first"]))

    text = f"{{first_name}}, QA: с днём рождения! {stamp()}"
    box.get_by_placeholder("Пусто — текст шаблона на языке сотрудника").fill(text)
    box.get_by_label("Письмо").uncheck()
    box.get_by_label("Telegram").uncheck()
    expect(box.get_by_label("Уведомление HRM")).to_be_checked()
    with auth_page.expect_response(lambda r: "/greetings/send" in r.url) as resp:
        box.get_by_role("button", name="Отправить").click()
    sent = resp.value
    assert sent.ok, f"POST /greetings/send → {sent.status}: {sent.text()[:200]}"
    body = sent.request.post_data_json
    assert body["employee_ids"] == [hero["emp"]["id"]] and body["kind"] == "birthday", body
    assert body["channels"] == ["in_app"], f"ушло не только в HRM: {body['channels']}"
    expect(alert(auth_page)).to_contain_text("Поздравлений отправлено: 1 из 1")
    alert(auth_page).get_by_role("button", name="OK").click()

    final = text.replace("{first_name}", hero["first"])
    log = [x for x in items(hrm_json(hrm_get(auth_page, "/greetings/log", {"limit": 200})))
           if x.get("employee_id") == hero["emp"]["id"]]
    assert len(log) == 1, f"журнал по своему сотруднику: {log}"
    header_button(auth_page, "Журнал").click()
    line = main(auth_page).locator("tr").filter(has_text=final)
    expect(line).to_have_count(1)
    expect(line).to_contain_text(name)
    expect(line).to_contain_text("День рождения")
    expect(line).to_contain_text("Уведомление HRM")
    expect(line).to_contain_text("Отправлено")
    expect(line).not_to_contain_text("Письмо")


# Кейс: «Не поздравлять» и «Скрыть возраст» в «Настройках» → prefs; календарь «просил не поздравлять»;
# {age} пропадает из текста; отправка отказавшемуся — skipped
@pytest.mark.writes_data
def test_greeting_employee_prefs(auth_page: Page, hero):
    emp_id = hero["emp"]["id"]
    open_hrm(auth_page, "/admin/greetings")
    header_button(auth_page, "Настройки").click()
    line = main(auth_page).locator("tr").filter(has_text=hero["first"])
    opt_out, hide_age = line.locator("input[type=checkbox]").nth(0), line.locator("input[type=checkbox]").nth(1)
    expect(opt_out).not_to_be_checked()
    for cb, key in ((hide_age, "hide_age"), (opt_out, "opt_out")):
        with auth_page.expect_response(lambda r: f"/greetings/employees/{emp_id}/prefs" in r.url) as resp:
            cb.click()
        assert resp.value.ok and resp.value.request.post_data_json == {key: True}, resp.value.request.post_data
        expect(cb).to_be_checked()
    prefs = next(e for e in items(hrm_json(hrm_get(auth_page, "/greetings/employees"))) if e["id"] == emp_id)
    assert prefs["opt_out"] and prefs["hide_age"], prefs

    header_button(auth_page, "Ближайшие 30 дней").click()
    name = own_events(auth_page, emp_id)["birthday"]["name"]
    expect(row(auth_page, name, "День рождения")).to_contain_text("просил не поздравлять")

    probe = {"employee_ids": [emp_id], "kind": "birthday", "channels": ["in_app"], "text": "QA возраст: {age}."}
    preview = hrm_json(hrm_post(auth_page, "/greetings/preview", probe))
    assert not re.search(r"\d", preview["text"]), f"«Скрыть возраст», а в тексте возраст: {preview['text']!r}"
    result = hrm_json(hrm_post(auth_page, "/greetings/send", probe))["items"]
    assert [r["status"] for r in result] == ["skipped"], f"отказавшемуся отправили: {result}"
    assert not [x for x in items(hrm_json(hrm_get(auth_page, "/greetings/log", {"limit": 200})))
                if x.get("employee_id") == emp_id and x.get("status") == "sent"]


# Кейс: заранее отправленное поздравление говорит о дне события, а не о сегодняшнем дне: в календаре
# «30» и «1» — в тексте те же числа, а не 29 и «0 ani»
@pytest.mark.writes_data
@pytest.mark.xfail(reason="bord-661: {age}/{years} считаются на сегодня — годовщина за 3 дня "
                          "до даты: «astăzi se împlinesc 0 ani», календарь пишет 1", strict=False)
def test_greeting_ahead_uses_event_numbers(auth_page: Page, hero):
    emp_id = hero["emp"]["id"]
    for kind, key, expected in (("birthday", "{age}", 30), ("anniversary", "{years}", 1)):
        text = hrm_json(hrm_post(auth_page, "/greetings/preview", {
            "employee_ids": [emp_id], "kind": kind, "channels": ["in_app"], "text": f"QA {key}"}))["text"]
        assert re.search(rf"\b{expected}\b", text), f"{kind}: календарь {expected}, текст {text!r}"


# ───────────────────────── настройки ─────────────────────────

# Кейс: настройки сохраняются сразу (PUT без «Сохранить»); «Какие праздники поздравлять» появляется
# только при включённых праздниках и совпадает с /greetings/holidays
@pytest.mark.writes_data
def test_greeting_settings_autosave(auth_page: Page, settings_back):
    open_hrm(auth_page, "/admin/greetings")
    header_button(auth_page, "Настройки").click()
    remind = auth_page.get_by_label("Руководителю — напоминание за день")
    was = settings_back["notify_managers_day_before"]
    expect(remind).to_be_checked(checked=was)
    with auth_page.expect_response(lambda r: "/greetings/settings" in r.url and r.request.method == "PUT") as resp:
        remind.click()
    assert resp.value.ok and resp.value.request.post_data_json == {"notify_managers_day_before": not was}
    expect(remind).to_be_checked(checked=not was)
    assert hrm_json(hrm_get(auth_page, "/greetings/settings"))["notify_managers_day_before"] is (not was)

    holidays = auth_page.get_by_label("Поздравлять с выбранными праздниками")
    block = main(auth_page).get_by_text("Какие праздники поздравлять", exact=True)
    if holidays.is_checked():
        with auth_page.expect_response(lambda r: "/greetings/settings" in r.url and r.request.method == "PUT"):
            holidays.click()
    expect(block).to_have_count(0)
    with auth_page.expect_response(lambda r: "/greetings/settings" in r.url and r.request.method == "PUT") as resp:
        holidays.click()
    assert resp.value.request.post_data_json == {"holidays": True}
    expect(block).to_be_visible()
    state = [h for h in items(hrm_json(hrm_get(auth_page, "/greetings/holidays"))) if h["source"] == "state"]
    assert state, "нет государственных праздников"
    for h in state:
        expect(main(auth_page).get_by_text(h["name"], exact=True).first).to_be_visible()
