"""Блок 78. HRM — плагин «Должностные инструкции» (/admin/job-instructions).

Снято 25.11.2026 в видимом MCP-браузере + справка help.bord.one/hrm/dolzhnostnye-instrukcii:
- плагин — /plugins {job_instructions}; выключен — /job-instructions* = 404 «Плагин не включён в настройках HRM»;
- в шапке: виды «Инструкции» / «Календарь» / «Сверка задач», ссылка «Органиграмма», кнопка «Должность»;
- «Должность» → окно «Новая должность» («Название», «Отделы (N)» с поиском «Отдел или компания», галочки отделов
  по компаниям); без отдела «Создать» неактивна (title «Отметьте хотя бы один отдел») → POST /org/positions
  {name, department_ids}; список слева = GET /job-instructions; карточка без людей пишет «Должность не занята —
  пункты с датами некому выполнять.»;
- «Добавить пункт» → «Новый пункт инструкции»: «Что делать *» (placeholder «Например: подстричь газон»),
  «Подробности», «Когда»: Ежедневно / Еженедельно (по умолчанию) / Ежемесячно / Ежегодно / По мере
  необходимости; дни недели — кнопки «пн»…«вс» (заглавными их делает CSS), «Будни», «Все дни», «Сбросить»;
  → POST /job-instructions/positions/<id>/duties {periodicity, weekdays: 1=пн…7=вс}; у строки пункта иконки
  с title «Выше» / «Ниже» / «Изменить» / «Удалить» («Удалить пункт?»);
- «Календарь»: «Коллизий за месяц: N» / «Замещений: N» = /job-instructions/calendar summary (conflict+vacant /
  covered); плашка пункта в клетке дня — div[title=<пункт>]; на незанятой должности каждый день пункта — vacant.
Должность, отдел и юрлицо — свои «QA Autotest», после теста удаляются; плагин возвращается как был.
"""
import calendar
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (QA_PREFIX, create_legal_entity, hrm_delete, hrm_get, hrm_json, hrm_post, hrm_put, items,
                         open_hrm, screen_action, screen_tab, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()


def main(page: Page):
    return page.locator("main")


def modal(page: Page, text: str):
    return page.locator("div.fixed.inset-0:visible").filter(has_text=text).last


def remove_own_positions(page: Page):
    for p in hrm_json(hrm_get(page, "/job-instructions")):
        if p["name"].startswith(QA_PREFIX):
            hrm_delete(page, f"/org/positions/{p['id']}", {"force": "true"})


@pytest.fixture
def ji(auth_page: Page):
    """Плагин включён, свой отдел (в своём юрлице) для должностей."""
    before = hrm_json(hrm_get(auth_page, "/plugins"))
    hrm_put(auth_page, "/plugins", {**before, "job_instructions": True})
    le = create_legal_entity(auth_page)
    dep = hrm_json(hrm_post(auth_page, "/org/departments", {"name": f"{QA_PREFIX} Отдел инструкций {stamp()}",
                                                           "legal_entity_id": le["id"]}))
    yield {"dep": dep, "le": le}
    remove_own_positions(auth_page)
    hrm_delete(auth_page, f"/org/departments/{dep['id']}")
    hrm_delete(auth_page, f"/legal-entities/{le['id']}")
    hrm_put(auth_page, "/plugins", before)


def own_position(page: Page, dep_id: int) -> dict:
    return hrm_json(hrm_post(page, "/org/positions", {"name": f"{QA_PREFIX} Должность {stamp()}",
                                                      "department_ids": [dep_id]}))


def position(page: Page, pos_id: int) -> dict:
    return hrm_json(hrm_get(page, f"/job-instructions/positions/{pos_id}"))


def pick(page: Page, name: str):
    """Выбрать должность в списке слева и дождаться её карточки."""
    with page.expect_response(lambda r: "/job-instructions/positions/" in r.url and r.request.method == "GET"):
        main(page).get_by_text(name, exact=True).first.click()


# Кейс: Плагин выключен — API 404 и экран «Плагин не включён» со ссылкой в настройки
@pytest.mark.writes_data
def test_plugin_off_screen(auth_page: Page):
    before = hrm_json(hrm_get(auth_page, "/plugins"))
    try:
        hrm_put(auth_page, "/plugins", {**before, "job_instructions": False})
        off = hrm_get(auth_page, "/job-instructions")
        assert off.status == 404 and "Плагин не включён" in off.text(), off.text()
        auth_page.goto("https://hrm.bord.one/admin/job-instructions")
        expect(main(auth_page)).to_contain_text("Плагин не включён", timeout=20_000)
        expect(main(auth_page).get_by_role("link", name=re.compile("Плагины"))).to_be_visible()
    finally:
        hrm_put(auth_page, "/plugins", before)


# Кейс: «Должность» — без отдела «Создать» неактивна; с отделом → POST /org/positions, должность в списке = API,
# карточка предупреждает «Должность не занята»
@pytest.mark.writes_data
def test_position_create(ji, auth_page: Page):
    name = f"{QA_PREFIX} Должность {stamp()}"
    open_hrm(auth_page, "/admin/job-instructions")
    for view in ("Инструкции", "Календарь", "Сверка задач"):
        expect(screen_tab(auth_page, view)).to_be_visible()
    screen_action(auth_page, "Должность", exact=True).click()
    box = modal(auth_page, "Новая должность")
    create = box.get_by_role("button", name="Создать", exact=True)
    expect(create).to_be_disabled()
    expect(create).to_have_attribute("title", "Отметьте хотя бы один отдел")
    box.locator("input").first.fill(name)
    box.get_by_placeholder("Отдел или компания").fill(ji["dep"]["name"])
    box.locator("label").filter(has_text=ji["dep"]["name"]).locator("input[type=checkbox]").check()
    with auth_page.expect_response(lambda r: r.url.endswith("/org/positions") and r.request.method == "POST") as resp:
        create.click()
    assert resp.value.ok, resp.value.text()
    pos_id = resp.value.json()["id"]

    listed = next(p for p in hrm_json(hrm_get(auth_page, "/job-instructions")) if p["id"] == pos_id)
    assert (listed["name"], [d["id"] for d in listed["departments"]], listed["holders"], listed["duty_count"]) == \
        (name, [ji["dep"]["id"]], [], 0), listed
    expect(main(auth_page)).to_contain_text("Должность не занята — пункты с датами некому выполнять.")
    expect(main(auth_page)).to_contain_text("Пунктов пока нет")


# Кейс: Пункты инструкции — еженедельный (пн, пт) и «По мере необходимости»; без дня недели не сохранить;
# «Выше» меняет порядок; «Удалить» с подтверждением «Удалить пункт?» — всё = API
@pytest.mark.writes_data
def test_duties_crud(ji, auth_page: Page):
    pos = own_position(auth_page, ji["dep"]["id"])
    weekly, adhoc = f"{QA_PREFIX} отчёт по складу", f"{QA_PREFIX} по запросу"
    open_hrm(auth_page, "/admin/job-instructions")
    pick(auth_page, pos["name"])

    main(auth_page).get_by_role("button", name="Добавить пункт", exact=True).click()
    box = modal(auth_page, "Новый пункт инструкции")
    box.get_by_placeholder(re.compile("^Например")).fill(weekly)
    expect(box).to_contain_text("Отметьте хотя бы один день")
    for day in ("пн", "пт"):
        box.locator("button").filter(has_text=re.compile(rf"^\s*{day}\s*$", re.I)).click()
    with auth_page.expect_response(lambda r: r.url.endswith(f"/positions/{pos['id']}/duties") and r.request.method == "POST") as r1:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert r1.value.ok, r1.value.text()

    main(auth_page).get_by_role("button", name="Добавить пункт", exact=True).click()
    box = modal(auth_page, "Новый пункт инструкции")
    box.get_by_placeholder(re.compile("^Например")).fill(adhoc)
    box.get_by_role("button", name="По мере необходимости", exact=True).click()
    with auth_page.expect_response(lambda r: r.url.endswith(f"/positions/{pos['id']}/duties") and r.request.method == "POST") as r2:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert r2.value.ok, r2.value.text()

    duties = position(auth_page, pos["id"])["duties"]
    assert [(d["title"], d["periodicity"], d["weekdays"]) for d in duties] == \
        [(weekly, "weekly", [1, 5]), (adhoc, "none", [])], duties

    # «Выше» у второго пункта — он становится первым
    line = main(auth_page).locator("div").filter(has_text=adhoc).filter(has=auth_page.locator('button[title="Выше"]')).last
    with auth_page.expect_response(lambda r: "/duties/order" in r.url and r.request.method == "PUT") as order:
        line.locator('button[title="Выше"]').click()
    assert order.value.ok, order.value.text()
    assert [d["title"] for d in position(auth_page, pos["id"])["duties"]] == [adhoc, weekly]

    line = main(auth_page).locator("div").filter(has_text=weekly).filter(has=auth_page.locator('button[title="Удалить"]')).last
    line.locator('button[title="Удалить"]').click()
    confirm = auth_page.locator("div.fixed.inset-0:visible").filter(has_text="Удалить пункт?").last
    with auth_page.expect_response(lambda r: "/job-instructions/duties/" in r.url and r.request.method == "DELETE") as gone:
        confirm.get_by_role("button", name=re.compile("^Удалить")).last.click()
    assert gone.value.ok, gone.value.text()
    assert [d["title"] for d in position(auth_page, pos["id"])["duties"]] == [adhoc]
    expect(main(auth_page).get_by_text(weekly, exact=True)).to_have_count(0)


# Кейс: «Календарь» — ежедневный пункт незанятой должности стоит в клетке сегодняшнего дня (будни),
# «Коллизий за месяц» = conflict + vacant из /job-instructions/calendar за месяц
@pytest.mark.writes_data
def test_calendar_vacant(ji, auth_page: Page):
    pos = own_position(auth_page, ji["dep"]["id"])
    title = f"{QA_PREFIX} касса {stamp()}"
    hrm_json(hrm_post(auth_page, f"/job-instructions/positions/{pos['id']}/duties", {"title": title, "periodicity": "daily"}))
    first = TODAY.replace(day=1)
    last = TODAY.replace(day=calendar.monthrange(TODAY.year, TODAY.month)[1])
    cal = hrm_json(hrm_get(auth_page, "/job-instructions/calendar", {"date_from": first.isoformat(), "date_to": last.isoformat()}))
    mine = [i for i in cal["items"] if i["position_id"] == pos["id"]]
    weekdays = sum(1 for d in range(1, last.day + 1) if first.replace(day=d).weekday() < 5)
    assert len(mine) == weekdays and {i["status"] for i in mine} == {"vacant"}, mine

    open_hrm(auth_page, "/admin/job-instructions")
    screen_tab(auth_page, "Календарь").click()
    collisions = cal["summary"]["conflict"] + cal["summary"]["vacant"]
    expect(main(auth_page)).to_contain_text(f"Коллизий за месяц: {collisions}")
    expect(main(auth_page)).to_contain_text(f"Замещений: {cal['summary']['covered']}")
    pills = main(auth_page).locator(f'[title="{title}"]')
    expect(pills.first).to_be_visible()
    # сетка начинается с понедельника недели 1-го числа и захватывает дни соседних месяцев
    assert pills.count() >= weekdays, f"плашек {pills.count()}, будних дней в месяце {weekdays}"
