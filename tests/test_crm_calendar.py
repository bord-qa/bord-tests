"""Блок 24. CRM — календарь: crm.bord.one/admin/calendar.

Снято 27.10.2026:
- три слоя на одной сетке: события GET /api/crm/calendar/events?start&end,
  задачи GET /tasks/?due_from&due_to&global_status=… (по сроку), напоминания
  GET /reminders/?from_dt&to_dt. Каждая запись — span[role=button] с title =
  название (у напоминания — его текст); у события перед названием время
  «10:00». Месяц — сетка 6×7 из button.group, в шапке ячейки номер дня;
- вид «Месяц / Неделя / День», слои «События / Задачи / Напоминания»,
  «Статусы задач 4/5» (по умолчанию без «Завершена»), «Все / Мои» хранятся
  на сервере: каждое переключение → PUT /user-prefs/me/calendar-filters.
  Тесты ставят стандартные настройки перед собой и возвращают прежние после;
- шапка: «Октябрь 2026» / «26 Октябрь – 1 Ноябрь 2026» / «27 Октябрь 2026, Вт»;
- «Создать» → меню «Событие / Задача / Напоминание». «Новое событие»:
  Название (без него — «Название обязательно»), Описание, Начало, Окончание
  (datetime-local, по умолчанию сегодня 10:00–11:00), Место, «Весь день» →
  POST /calendar/events, время уходит в UTC по часам браузера;
- клик по событию → «Редактировать событие» (Удалить / Отмена / Сохранить) →
  PUT /calendar/events/<id>; «Удалить» → «Удалить событие «…»?» → «Продолжить»
  → DELETE. Адрес страницы при этом не меняется;
- клик по задаче открывает её карточку из раздела «Задачи»;
- «Новое напоминание»: «Связать с» Контакт / Сделка (поиск контакта), «Когда
  напомнить», «Текст» → POST /reminders/ {entity_type, entity_id, reminder_at,
  message}. Клик по напоминанию → окно «Напоминание» (Удалить / Выполнено):
  «Выполнено» → PUT {is_completed: true}, запись уходит с сетки; «Удалить» →
  «Удалить напоминание?» → «Продолжить» → DELETE;
- клик по пустому месту дня → окно дня («В этот день ничего нет» или список)
  с «Добавить на этот день» → то же меню, что у «Создать».

Ячейка месяца показывает не больше 3 записей, остальное — под «+ ещё N» (окно
дня). В BORD QA бывают дни, занятые чужими записями (12.11: 7 броней из звонков
на 14.11 — напоминание теста уехало под «+ ещё»), поэтому тесты сетки месяца
ставят свои записи на ближайший свободный день текущего месяца (free_day), а не
на «сегодня»; «сегодня» нужно только неделе и дню.

Время событий считаем по часам браузера: в CI у него может быть UTC, дома —
Кишинёв. Свои записи «QA Autotest …» удаляем, контакты — в архив, как в блоке 21.
"""
import datetime as dt
import random
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import (CRM, QA_PREFIX, crm_delete, crm_get, crm_me, crm_post, crm_put,
                         crm_ready, open_crm)

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/calendar"
PREFS = "/user-prefs/me/calendar-filters"
DEFAULT_PREFS = {"mine": False, "layers": {"events": True, "tasks": True, "reminders": True},
                 "task_statuses": ["new", "in_progress", "on_hold", "canceled"], "view": "month"}
# Три запроса, из которых собирается сетка: ждём все, прежде чем смотреть на неё.
LOADS = ("/api/crm/calendar/events?", "/api/crm/tasks/?due_from", "/api/crm/reminders/?from_dt")
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь",
          "Октябрь", "Ноябрь", "Декабрь"]
# с 04.11 в неделе, дне и окне дня месяц в родительном падеже («2 – 8 ноября 2026», «5 ноября 2026, чт»)
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря"]
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


def title() -> str:
    return f"{QA_PREFIX} {tag()}"


def today(page: Page) -> dt.date:
    """«Сегодня» по часам браузера — от него календарь рисует сетку."""
    return dt.date.fromisoformat(page.evaluate("() => new Date().toLocaleDateString('sv-SE')"))


def local_iso(page: Page, day: dt.date, hm: str) -> str:
    """Местное время браузера `hm` в день `day` с его смещением — экран покажет ровно hm."""
    minutes = int(page.evaluate("s => -new Date(s).getTimezoneOffset()", f"{day.isoformat()}T{hm}:00"))
    sign = "+" if minutes >= 0 else "-"
    return f"{day.isoformat()}T{hm}:00{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def busy_days(page: Page, start: dt.date, end: dt.date) -> set[dt.date]:
    """Дни, на которых в сетке уже что-то стоит: те же три запроса, что у календаря
    (события, задачи по сроку со статусами по умолчанию, невыполненные напоминания).
    Даты — по часам браузера, как их раскладывает сетка."""
    offset = dt.timedelta(minutes=int(page.evaluate("() => -new Date().getTimezoneOffset()")))

    def local(value: str) -> dt.datetime:
        return as_utc(value) + offset

    days: set[dt.date] = set()
    rng = {"start": start.isoformat(), "end": end.isoformat(), "limit": 500}
    for e in crm_get(page, "/calendar/events", rng).json()["items"]:
        first, last = local(e["start_at"]), local(e["end_at"] or e["start_at"]) - dt.timedelta(minutes=1)
        d = first.date()
        while d <= max(first.date(), last.date()):
            days.add(d)
            d += dt.timedelta(days=1)
    statuses = "".join(f"&global_status={x}" for x in DEFAULT_PREFS["task_statuses"])
    tasks = crm_get(page, f"/tasks/?due_from={start}&due_to={end}&is_archived=false{statuses}&limit=200")
    days |= {dt.date.fromisoformat(t["due_date"][:10]) for t in tasks.json()["items"] if t.get("due_date")}
    reminders = crm_get(page, "/reminders/", {"from_dt": start.isoformat(), "to_dt": end.isoformat(), "limit": 200})
    days |= {local(r["reminder_at"]).date() for r in reminders.json()["items"]}
    return days


def free_day(page: Page, span: int = 1, after: int = 0) -> dt.date:
    """Первый день текущего месяца (не раньше «сегодня + after»), с которого `span` дней подряд
    пусты: в ячейке месяца видно лишь 3 записи, лишние прячутся под «+ ещё N».
    Свободного окна до конца месяца нет — «сегодня + after», как раньше."""
    start = today(page) + dt.timedelta(days=after)
    month_end = (start.replace(day=1) + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    busy = busy_days(page, start, month_end + dt.timedelta(days=1))
    day = start
    while day + dt.timedelta(days=span - 1) <= month_end:
        if not any(day + dt.timedelta(days=i) in busy for i in range(span)):
            return day
        day += dt.timedelta(days=1)
    return start


def as_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def create_event(page: Page, day: dt.date, hm: str = "12:00", **fields) -> dict:
    end = f"{int(hm[:2]) + 1:02d}{hm[2:]}"
    data = {"title": title(), "start_at": local_iso(page, day, hm), "end_at": local_iso(page, day, end), **fields}
    resp = crm_post(page, "/calendar/events", data)
    assert resp.status in (200, 201), resp.text()
    page._qa["events"].append(resp.json()["id"])
    return {**data, **resp.json()}


def create_task(page: Page, day: dt.date, **fields) -> dict:
    data = {"title": title(), "responsible_id": crm_me(page)["id"], "due_date": day.isoformat(), **fields}
    resp = crm_post(page, "/tasks/", data)
    assert resp.status == 201, resp.text()
    page._qa["tasks"].append(resp.json()["id"])
    return resp.json()


def create_contact(page: Page) -> dict:
    data = {"first_name": title(), "phone": "+3736" + "".join(random.choices("0123456789", k=7))}
    resp = crm_post(page, "/contacts/", data)
    assert resp.status == 201, resp.text()
    page._qa["contacts"].append(resp.json()["id"])
    return resp.json()


def create_reminder(page: Page, day: dt.date, hm: str = "16:00") -> dict:
    contact = create_contact(page)
    data = {"entity_type": "contact", "entity_id": contact["id"],
            "reminder_at": local_iso(page, day, hm), "message": title()}
    resp = crm_post(page, "/reminders/", data)
    assert resp.status in (200, 201), resp.text()
    return resp.json()


def cleanup(page: Page):
    day = dt.date.today()
    resp = crm_get(page, "/calendar/events", {"start": (day - dt.timedelta(days=60)).isoformat(),
                                              "end": (day + dt.timedelta(days=120)).isoformat(), "limit": 500})
    ids = set(page._qa["events"])
    ids |= {e["id"] for e in resp.json()["items"] if e["title"].startswith(QA_PREFIX)} if resp.ok else set()
    for event_id in ids:
        crm_delete(page, f"/calendar/events/{event_id}")
    for task_id in page._qa["tasks"]:
        crm_delete(page, f"/tasks/{task_id}")
        crm_delete(page, f"/tasks/{task_id}/permanent")
    for contact_id in page._qa["contacts"]:
        for done in ("false", "true"):  # без is_completed список отдаёт только невыполненные
            resp = crm_get(page, "/reminders/", {"entity_type": "contact", "entity_id": contact_id,
                                                 "is_completed": done})
            for reminder in resp.json()["items"] if resp.ok else []:
                crm_delete(page, f"/reminders/{reminder['id']}")
    if page._qa["contacts"]:
        crm_post(page, "/bulk/contacts/delete", {"contact_ids": page._qa["contacts"]})


def prefs(page: Page) -> dict:
    return crm_get(page, PREFS).json()["filters"]


@pytest.fixture
def calendar(auth_page: Page):
    auth_page._qa = {"events": [], "tasks": [], "contacts": []}
    saved = prefs(auth_page)
    assert crm_put(auth_page, PREFS, DEFAULT_PREFS).ok
    open_crm(auth_page, PAGE)
    yield auth_page
    cleanup(auth_page)
    crm_put(auth_page, PREFS, saved)


def reload_calendar(page: Page):
    """Перезагрузить и дождаться всех трёх слоёв.

    API CRM отвечает 429 на всплеск параллельных запросов, и слой, получивший
    отказ, остаётся пустым до следующей загрузки — поэтому до трёх попыток."""
    for _ in range(3):
        seen = set()

        def on_response(r):
            seen.update(key for key in LOADS if key in r.url and r.ok)

        page.on("response", on_response)
        page.reload()
        crm_ready(page)
        for _ in range(60):
            if len(seen) == len(LOADS):
                break
            page.wait_for_timeout(250)
        page.remove_listener("response", on_response)
        if len(seen) == len(LOADS):
            return
        page.wait_for_timeout(2000)
    raise AssertionError(f"календарь не загрузил слои за 3 попытки: есть только {sorted(seen)}")


def chip(page: Page, name: str):
    """Событие, задача или напоминание на сетке — по title (название или текст)."""
    return page.locator(f"main [role=button][title='{name}']")


def day_number(item):
    """Номер дня в шапке ячейки месяца, в которой стоит запись."""
    return item.locator("xpath=ancestor::button[contains(@class,'group')][1]/div[1]/span[1]")


def overlay(page: Page, text: str):
    """Окна CRM — div.fixed.inset-0 (не role=dialog)."""
    return page.locator("div.fixed.inset-0").filter(has_text=text).last


def expect_period(page: Page, text: str):
    """Шапка периода («Ноябрь 2026», «26 октября – 1 ноября 2026»). Регистр сверяем без учёта:
    до 04.11 месяц был строчным в DOM и заглавным через CSS capitalize, теперь класса нет."""
    period = page.locator("main div.font-semibold.whitespace-nowrap")
    expect(period).to_have_text(re.compile(rf"^{re.escape(text)}$", re.IGNORECASE))


def month_title(day: dt.date) -> str:
    return f"{MONTHS[day.month - 1]} {day.year}"


def view_button(page: Page, name: str):
    return page.locator("main").get_by_role("button", name=name, exact=True)


def create_menu(page: Page, item: str):
    page.locator("main").get_by_role("button", name="Создать", exact=True).click()
    page.get_by_role("button", name=item, exact=True).click()


def event_form(page: Page):
    create_menu(page, "Событие")
    form = overlay(page, "Новое событие")
    expect(form.get_by_role("button", name="Создать", exact=True)).to_be_visible()
    return form


def dt_inputs(form):
    return form.locator("input[type=datetime-local]")


def open_event(page: Page, event: dict):
    chip(page, event["title"]).first.click()
    card = overlay(page, "Редактировать событие")
    expect(card.get_by_placeholder("Встреча с клиентом")).to_have_value(event["title"])
    return card


def prefs_saved(page: Page):
    return page.expect_response(lambda r: PREFS in r.url and r.request.method == "PUT" and r.ok)


def is_api(path: str, method: str):
    return lambda r: path in r.url and r.request.method == method


# Кейс: Месяц совпадает с API — текущий месяц в шапке, событие, задача и напоминание стоят на своих днях
def test_month_matches_api(calendar: Page):
    day = free_day(calendar, span=3)
    event = create_event(calendar, day, "12:00")
    task = create_task(calendar, day + dt.timedelta(days=1))
    reminder = create_reminder(calendar, day + dt.timedelta(days=2), "16:00")
    reload_calendar(calendar)

    expect_period(calendar, month_title(day))
    for name, on_day, text in ((event["title"], day, "12:00"),
                               (task["title"], day + dt.timedelta(days=1), task["title"]),
                               (reminder["message"], day + dt.timedelta(days=2), reminder["message"])):
        item = chip(calendar, name)
        expect(item).to_have_count(1)
        expect(item).to_contain_text(text)
        expect(day_number(item)).to_have_text(str(on_day.day))


# Кейс: Новое событие через форму — название, описание, место, время; встаёт на свой день
def test_create_event_via_form(calendar: Page):
    day = free_day(calendar, after=1)
    name = title()
    form = event_form(calendar)
    form.get_by_placeholder("Встреча с клиентом").fill(name)
    form.get_by_placeholder("Детали события").fill("Описание от автотеста")
    form.get_by_placeholder("Офис / Zoom-ссылка / адрес").fill("Офис QA")
    dt_inputs(form).nth(0).fill(f"{day.isoformat()}T15:00")
    dt_inputs(form).nth(1).fill(f"{day.isoformat()}T16:00")
    with calendar.expect_response(is_api("/api/crm/calendar/events", "POST")) as created:
        form.get_by_role("button", name="Создать", exact=True).click()

    assert created.value.status in (200, 201), created.value.text()
    event_id = created.value.json()["id"]
    calendar._qa["events"].append(event_id)
    expect(form).to_be_hidden()
    item = chip(calendar, name)
    expect(item).to_contain_text("15:00")
    expect(day_number(item)).to_have_text(str(day.day))

    saved = crm_get(calendar, f"/calendar/events/{event_id}").json()
    assert (saved["title"], saved["description"], saved["location"], saved["all_day"]) == \
        (name, "Описание от автотеста", "Офис QA", False), saved
    assert as_utc(saved["start_at"]) == as_utc(local_iso(calendar, day, "15:00")), saved["start_at"]
    assert as_utc(saved["end_at"]) == as_utc(local_iso(calendar, day, "16:00")), saved["end_at"]


# Кейс: Событие без названия не создаётся — «Название обязательно», запрос не уходит
def test_event_title_required(calendar: Page):
    posts = []
    calendar.on("request", lambda r: posts.append(r.url) if "/calendar/events" in r.url and r.method == "POST" else None)
    form = event_form(calendar)
    form.get_by_role("button", name="Создать", exact=True).click()

    expect(form.get_by_text("Название обязательно")).to_be_visible()
    expect(form.get_by_role("button", name="Создать", exact=True)).to_be_visible()
    assert not posts, posts


# Кейс: «Отмена» в форме события закрывает её без сохранения
def test_create_event_cancel(calendar: Page):
    posts = []
    calendar.on("request", lambda r: posts.append(r.url) if "/calendar/events" in r.url and r.method == "POST" else None)
    name = title()
    form = event_form(calendar)
    form.get_by_placeholder("Встреча с клиентом").fill(name)
    form.get_by_role("button", name="Отмена").click()

    expect(form).to_be_hidden()
    assert not posts, posts
    expect(chip(calendar, name)).to_have_count(0)


# Кейс: Правка события — новое название, место и время видны на сетке и в API
def test_edit_event(calendar: Page):
    day = free_day(calendar)
    event = create_event(calendar, day, "12:00", location="Офис QA")
    reload_calendar(calendar)
    card = open_event(calendar, event)
    expect(card.get_by_placeholder("Офис / Zoom-ссылка / адрес")).to_have_value("Офис QA")
    expect(dt_inputs(card).nth(0)).to_have_value(f"{day.isoformat()}T12:00")

    new_name = title()
    card.get_by_placeholder("Встреча с клиентом").fill(new_name)
    card.get_by_placeholder("Офис / Zoom-ссылка / адрес").fill("Zoom")
    dt_inputs(card).nth(0).fill(f"{day.isoformat()}T15:30")
    dt_inputs(card).nth(1).fill(f"{day.isoformat()}T16:30")
    with calendar.expect_response(is_api(f"/api/crm/calendar/events/{event['id']}", "PUT")) as updated:
        card.get_by_role("button", name="Сохранить").click()

    assert updated.value.ok, updated.value.text()
    expect(card).to_be_hidden()
    expect(chip(calendar, new_name)).to_contain_text("15:30")
    expect(chip(calendar, event["title"])).to_have_count(0)
    saved = crm_get(calendar, f"/calendar/events/{event['id']}").json()
    assert (saved["title"], saved["location"]) == (new_name, "Zoom"), saved
    assert as_utc(saved["start_at"]) == as_utc(local_iso(calendar, day, "15:30")), saved["start_at"]


# Кейс: Удаление события — подтверждение «Удалить событие «…»?» → «Продолжить», событие пропадает
def test_delete_event(calendar: Page):
    event = create_event(calendar, free_day(calendar))
    reload_calendar(calendar)
    card = open_event(calendar, event)
    card.get_by_role("button", name="Удалить").click()
    confirm = overlay(calendar, f"Удалить событие «{event['title']}»?")
    expect(confirm).to_be_visible()
    with calendar.expect_response(is_api(f"/api/crm/calendar/events/{event['id']}", "DELETE")) as deleted:
        confirm.get_by_role("button", name="Продолжить").click()

    assert deleted.value.ok, deleted.value.text()
    expect(chip(calendar, event["title"])).to_have_count(0)
    assert crm_get(calendar, f"/calendar/events/{event['id']}").status == 404


# Кейс: Отмена удаления события — событие остаётся
def test_delete_event_cancel(calendar: Page):
    event = create_event(calendar, free_day(calendar))
    reload_calendar(calendar)
    card = open_event(calendar, event)
    card.get_by_role("button", name="Удалить").click()
    confirm = overlay(calendar, f"Удалить событие «{event['title']}»?")
    confirm.get_by_role("button", name="Отмена").click()

    expect(confirm).to_be_hidden()
    assert crm_get(calendar, f"/calendar/events/{event['id']}").ok
    reload_calendar(calendar)
    expect(chip(calendar, event["title"])).to_have_count(1)


# Кейс: «Добавить на этот день» — форма события открывается с датой выбранного дня
def test_add_on_day(calendar: Page):
    day = free_day(calendar, after=1)
    task = create_task(calendar, day)
    reload_calendar(calendar)
    day_number(chip(calendar, task["title"])).click()
    popup = overlay(calendar, "Добавить на этот день")
    expect(popup).to_contain_text(f"{day.day} {MONTHS_GEN[day.month - 1]} {day.year}")
    expect(popup).to_contain_text(task["title"])

    popup.get_by_role("button", name="Добавить на этот день").click()
    calendar.get_by_role("button", name="Событие", exact=True).click()
    form = overlay(calendar, "Новое событие")
    expect(dt_inputs(form).nth(0)).to_have_value(re.compile(rf"^{day.isoformat()}T"))


# Кейс: Неделя и день — шапка с периодом, событие со временем; выбранный вид держится после перезагрузки
def test_week_and_day_views(calendar: Page):
    day = today(calendar)
    event = create_event(calendar, day, "12:00")
    monday = day - dt.timedelta(days=day.weekday())
    sunday = monday + dt.timedelta(days=6)

    with prefs_saved(calendar):
        view_button(calendar, "Неделя").click()
    if monday.month == sunday.month:  # неделя внутри месяца — месяц пишется один раз
        week = f"{monday.day} – {sunday.day} {MONTHS_GEN[sunday.month - 1]} {sunday.year}"
    else:
        week = f"{monday.day} {MONTHS_GEN[monday.month - 1]} – {sunday.day} {MONTHS_GEN[sunday.month - 1]} {sunday.year}"
    expect_period(calendar, week)
    expect(chip(calendar, event["title"])).to_contain_text("12:00")
    assert prefs(calendar)["view"] == "week"
    reload_calendar(calendar)
    expect_period(calendar, week)

    view_button(calendar, "День").click()
    expect_period(calendar, f"{day.day} {MONTHS_GEN[day.month - 1]} {day.year}, {WEEKDAYS[day.weekday()]}")
    expect(chip(calendar, event["title"])).to_contain_text("12:00")

    view_button(calendar, "Месяц").click()
    expect_period(calendar, month_title(day))


# Кейс: Листание месяцев — вперёд, назад, «Сегодня»; событие следующего месяца видно только там
def test_navigation(calendar: Page):
    day = today(calendar)
    next_month = (day.replace(day=1) + dt.timedelta(days=32)).replace(day=15)
    prev_month = (day.replace(day=1) - dt.timedelta(days=1))
    event = create_event(calendar, next_month)
    reload_calendar(calendar)
    expect(chip(calendar, event["title"])).to_have_count(0)

    today_button = calendar.locator("main").get_by_role("button", name="Сегодня", exact=True)
    today_button.locator("xpath=following-sibling::button[1]").click()
    expect_period(calendar, month_title(next_month))
    expect(day_number(chip(calendar, event["title"]))).to_have_text("15")

    today_button.locator("xpath=preceding-sibling::button[1]").click()
    today_button.locator("xpath=preceding-sibling::button[1]").click()
    expect_period(calendar, month_title(prev_month))

    today_button.click()
    expect_period(calendar, month_title(day))


# Кейс: Задача в календаре открывает свою карточку
def test_task_opens_card(calendar: Page):
    task = create_task(calendar, free_day(calendar))
    reload_calendar(calendar)
    chip(calendar, task["title"]).click()

    card = overlay(calendar, "Завершить задачу")
    expect(card).to_contain_text(task["title"])
    expect(card.get_by_role("button", name="Инфо")).to_be_visible()


# Кейс: «Статусы задач» — завершённая скрыта по умолчанию, видна после включения «Завершена»
def test_task_status_filter(calendar: Page):
    task = create_task(calendar, free_day(calendar), global_status="done")
    reload_calendar(calendar)
    statuses = calendar.locator("main").get_by_title("Какие задачи показывать по статусу")
    expect(statuses).to_contain_text("4/5")
    expect(chip(calendar, task["title"])).to_have_count(0)

    statuses.click()
    with prefs_saved(calendar):
        calendar.locator("main").get_by_role("button", name="Завершена", exact=True).click()
    expect(statuses).to_contain_text("5/5")
    expect(chip(calendar, task["title"])).to_have_count(1)
    assert "done" in prefs(calendar)["task_statuses"]


# Кейс: «Мои» прячет чужие и неназначенные задачи, «Все» возвращает
def test_mine_and_all(calendar: Page):
    task = create_task(calendar, free_day(calendar), responsible_id=None)
    reload_calendar(calendar)
    expect(chip(calendar, task["title"])).to_have_count(1)

    with prefs_saved(calendar), calendar.expect_response(
            lambda r: "/api/crm/tasks/?due_from" in r.url and f"responsible_id={crm_me(calendar)['id']}" in r.url):
        view_button(calendar, "Мои").click()
    expect(chip(calendar, task["title"])).to_have_count(0)
    assert prefs(calendar)["mine"] is True

    view_button(calendar, "Все").click()
    expect(chip(calendar, task["title"])).to_have_count(1)


# Кейс: Слои «События / Задачи / Напоминания» прячут и возвращают свои записи
def test_layers(calendar: Page):
    day = free_day(calendar)
    items = {"События": create_event(calendar, day)["title"],
             "Задачи": create_task(calendar, day)["title"],
             "Напоминания": create_reminder(calendar, day)["message"]}
    reload_calendar(calendar)
    for layer, name in items.items():
        expect(chip(calendar, name)).to_have_count(1)
        view_button(calendar, layer).click()
        expect(chip(calendar, name)).to_have_count(0)
        others = [n for l, n in items.items() if l != layer]
        for other in others:
            expect(chip(calendar, other)).to_have_count(1)
        view_button(calendar, layer).click()
        expect(chip(calendar, name)).to_have_count(1)


# Кейс: Новое напоминание через форму — по контакту, на свой день и время
def test_create_reminder_via_form(calendar: Page):
    day = free_day(calendar, after=1)
    contact = create_contact(calendar)
    text = title()
    create_menu(calendar, "Напоминание")
    form = overlay(calendar, "Новое напоминание")
    form.get_by_placeholder("Найти контакт (имя / телефон)…").fill(contact["first_name"])
    form.get_by_role("button", name=re.compile(re.escape(contact["first_name"]))).click()
    form.locator("input[type=datetime-local]").fill(f"{day.isoformat()}T11:00")
    form.get_by_placeholder("Перезвонить клиенту…").fill(text)
    with calendar.expect_response(is_api("/api/crm/reminders/", "POST")) as created:
        form.get_by_role("button", name="Создать", exact=True).click()

    assert created.value.status in (200, 201), created.value.text()
    saved = created.value.json()
    assert (saved["entity_type"], saved["entity_id"], saved["message"], saved["is_completed"]) == \
        ("contact", contact["id"], text, False), saved
    assert as_utc(saved["reminder_at"]) == as_utc(local_iso(calendar, day, "11:00")), saved["reminder_at"]
    expect(form).to_be_hidden()
    expect(day_number(chip(calendar, text))).to_have_text(str(day.day))


# Кейс: «Выполнено» у напоминания — оно уходит с сетки, в API is_completed
def test_reminder_done(calendar: Page):
    reminder = create_reminder(calendar, free_day(calendar), "16:00")
    reload_calendar(calendar)
    chip(calendar, reminder["message"]).click()
    card = overlay(calendar, "Выполнено")
    expect(card).to_contain_text(reminder["message"])
    expect(card).to_contain_text("16:00")
    with calendar.expect_response(is_api(f"/api/crm/reminders/{reminder['id']}", "PUT")) as updated:
        card.get_by_role("button", name="Выполнено").click()

    assert updated.value.ok, updated.value.text()
    expect(chip(calendar, reminder["message"])).to_have_count(0)
    rows = crm_get(calendar, "/reminders/", {"entity_type": "contact", "entity_id": reminder["entity_id"],
                                             "is_completed": "true"}).json()["items"]
    assert [r["is_completed"] for r in rows if r["id"] == reminder["id"]] == [True], rows


# Кейс: Удаление напоминания — подтверждение «Удалить напоминание?» → «Продолжить»
def test_reminder_delete(calendar: Page):
    reminder = create_reminder(calendar, free_day(calendar), "16:00")
    reload_calendar(calendar)
    chip(calendar, reminder["message"]).click()
    overlay(calendar, "Выполнено").get_by_role("button", name="Удалить").click()
    confirm = overlay(calendar, "Удалить напоминание?")
    with calendar.expect_response(is_api(f"/api/crm/reminders/{reminder['id']}", "DELETE")) as deleted:
        confirm.get_by_role("button", name="Продолжить").click()

    assert deleted.value.ok, deleted.value.text()
    expect(chip(calendar, reminder["message"])).to_have_count(0)
    rows = crm_get(calendar, "/reminders/", {"entity_type": "contact", "entity_id": reminder["entity_id"]}).json()["items"]
    assert reminder["id"] not in [r["id"] for r in rows], rows
