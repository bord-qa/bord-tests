"""Блок 64. HRM — скрытые экраны (их нет в меню, открываются по адресу): заявки на кадры (/admin/requisitions),
эффективность (/admin/performance), вовлечённость (/admin/engagement).

Снято 03.11.2026 в видимом MCP-браузере. Экраны похожи на недоделанные — найденное не заводили, а
собрали вопросами (README «Замечания по HRM»):
- «Заявки на кадры»: плитки «В работе / Одобрено / Среднее время (дн.) / Рефералов принято» (=
  /reports/requisitions-summary), вкладки «Заявки / Каналы / Рефералы», фильтр «Все / Черновик / Одобрена /
  В работе / Закрыта / Отменена», «Все приоритеты»; в шапке «Новая заявка» → «Новая заявка на кадры»:
  «Название», «Отдел», «Должность», «Кол-во вакансий», «Приоритет» (Обычная / Низкая / Высокая / Срочная),
  «Зарплата от / до», «Целевая дата», «Обоснование» → POST /requisitions/ (без названия молча не уходит);
  новая заявка получает статус «new» (в списке сырым «new»), которого нет ни в одной вкладке фильтра;
  в карточке только «Редактировать» — сменить статус на экране нельзя; удаления нет — уборка: /cancel
  (отменённые с префиксом копятся);
- «Эффективность»: вкладки «Цели / OKR / Циклы оценки / 1-on-1 / Обратная связь»; «Создать цель» →
  «Новая цель»: «Название *», «Описание», «Тип цели», «Фреймворк» (OKR), «Ответственный», даты,
  «Ключевые результаты» («KR 1» + «Цель») → POST /performance/goals (цель) + POST с parent_id (KR);
  карточка «Активна · medium (сырой) · OKR · <ответственный>», KR «0%»; «Обновить прогресс» (ползунок +
  комментарий) → POST …/checkin — но цель с отметкой потом не удаляется (500), а на экране удаления нет,
  поэтому тест прогресс не трогает и удаляет цель через API; «Запланировать 1-on-1» → «Сотрудник *»,
  «Дата и время *», «Длительность (мин.)» 30, «Повестка» → POST /performance/one-on-ones (удаления нет —
  уходит каскадом со своим сотрудником);
- «Вовлечённость»: «Опросы / События», «Создать опрос» — опросы не удаляются, а запущенный рассылается
  всем, поэтому только сверка списков с API.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (screen_action, screen_tab, QA_PREFIX, clean_up, create_employee, create_legal_entity, date_field, delete_employee,
                         dropdown, field, header_button, hrm_delete, hrm_get, hrm_json, hrm_post, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth

TOMORROW = dt.date.today() + dt.timedelta(days=1)


def main(page: Page):
    return page.locator("main")


def tab(page: Page, name: str):
    screen_tab(page, name).click()


def modal(page: Page, heading: str):
    return page.locator("div.fixed.inset-0:visible").filter(
        has=page.get_by_role("heading", name=heading, exact=True)).last


@pytest.fixture
def person(auth_page: Page):
    le = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Скрытые {stamp()}")
    yield emp
    for g in items(hrm_json(hrm_get(auth_page, "/performance/goals", {"owner_id": emp["id"]}))):
        for kr in g.get("key_results") or []:
            hrm_delete(auth_page, f"/performance/goals/{kr['id']}")
        hrm_delete(auth_page, f"/performance/goals/{g['id']}")
    delete_employee(auth_page, emp["id"])
    clean_up(auth_page)


# Кейс: Заявка на кадры — форма = API, строка «Высокая · 0/2 чел.», карточка с вилкой и заявителем; плитки = сводке
@pytest.mark.writes_data
def test_requisition_form(auth_page: Page):
    title = f"{QA_PREFIX} Заявка {stamp()}"
    open_hrm(auth_page, "/admin/requisitions")
    summary = hrm_json(hrm_get(auth_page, "/reports/requisitions-summary"))
    for tile in ("В работе", "Одобрено", "Среднее время (дн.)", "Рефералов принято"):
        expect(main(auth_page)).to_contain_text(tile)
    header_button(auth_page, "Новая заявка").click()
    box = modal(auth_page, "Новая заявка на кадры")
    req_id = None
    try:
        field(box, "Название").fill(title)
        field(box, "Кол-во вакансий").fill("2")
        dropdown(auth_page, box, "Приоритет", "Высокая")
        field(box, "Зарплата от").fill("15000")
        field(box, "Зарплата до").fill("20000")
        field(box, "Обоснование").fill("QA: рост отдела")
        with auth_page.expect_response(lambda r: r.url.endswith("/requisitions/") and r.request.method == "POST") as resp:
            box.get_by_role("button", name="Создать", exact=True).click()
        assert resp.value.ok, resp.value.text()
        req_id = resp.value.json()["id"]
        req = hrm_json(hrm_get(auth_page, f"/requisitions/{req_id}"))
        assert (req["title"], req["headcount"], req["urgency"], float(req["salary_min"]), float(req["salary_max"])) == \
            (title, 2, "high", 15000.0, 20000.0), req
        item = main(auth_page).get_by_role("button").filter(has_text=title)
        expect(item).to_contain_text("Высокая")
        expect(item).to_contain_text("0/2 чел.")
        item.click()
        expect(main(auth_page)).to_contain_text(re.compile(r"15\s000 – 20\s000"))
        expect(main(auth_page)).to_contain_text("Заявитель")
        expect(main(auth_page)).to_contain_text("Укомплектовано")
    finally:
        if req_id:
            hrm_post(auth_page, f"/requisitions/{req_id}/cancel")  # удаления нет — только отмена


# Кейс: Цель OKR с ключевым результатом своему сотруднику — две записи (цель и KR) = API, карточка «Активна · OKR», KR «0%»
@pytest.mark.writes_data
def test_goal_with_key_result(auth_page: Page, person):
    title, kr = f"{QA_PREFIX} Цель {stamp()}", f"{QA_PREFIX} KR закрыть 10 задач"
    open_hrm(auth_page, "/admin/performance")
    for name in ("Цели / OKR", "Циклы оценки", "1-on-1", "Обратная связь"):
        expect(screen_action(auth_page, name, exact=True)).to_be_visible()
    screen_action(auth_page, "Создать цель").click()
    box = modal(auth_page, "Новая цель")
    field(box, "Название *").fill(title)
    expect(field(box, "Тип цели")).to_contain_text("Индивидуальная")
    expect(field(box, "Фреймворк")).to_contain_text("OKR")
    dropdown(auth_page, box, "Ответственный", person["telegram_first_name"])
    box.get_by_placeholder("KR 1").fill(kr)
    box.get_by_placeholder("Цель").first.fill("10")
    posts = []
    auth_page.on("response", lambda r: posts.append(r) if r.url.endswith("/performance/goals") and r.request.method == "POST" else None)
    box.get_by_role("button", name="Создать", exact=True).click()
    expect(main(auth_page)).to_contain_text("Цель создана")
    for _ in range(20):  # цель и KR уходят двумя POST подряд
        if len(posts) >= 2:
            break
        auth_page.wait_for_timeout(250)
    assert len(posts) == 2 and all(p.ok for p in posts), [(p.status, p.url) for p in posts]
    goals = items(hrm_json(hrm_get(auth_page, "/performance/goals", {"owner_id": person["id"]})))
    goal = next(g for g in goals if g["title"] == title)
    assert (goal["goal_framework"], goal["goal_type"], goal["status"], goal["owner_id"]) == ("okr", "individual", "active", person["id"]), goal
    assert [(k["title"], k["target_value"]) for k in goal["key_results"]] == [(kr, 10)], goal["key_results"]
    item = main(auth_page).get_by_role("button").filter(has_text=title)
    if not item.count():
        item = main(auth_page).locator("div").filter(has_text=title).filter(has_text=kr).last
    for text in ("Активна", "OKR", person["telegram_first_name"], kr, "0%"):
        expect(item).to_contain_text(text)


# Кейс: «Запланировать 1-on-1» со своим сотрудником на завтра — строка «Менеджер / Сотрудник / Дата / Длительность» = API
@pytest.mark.writes_data
def test_one_on_one(auth_page: Page, person):
    open_hrm(auth_page, "/admin/performance")
    tab(auth_page, "1-on-1")
    screen_action(auth_page, "Запланировать 1-on-1").click()
    box = modal(auth_page, "Запланировать 1-on-1")
    dropdown(auth_page, box, "Сотрудник *", person["telegram_first_name"])
    date_field(box, "Дата и время *").fill(f"{TOMORROW}T10:00")
    expect(field(box, "Длительность (мин.)")).to_have_value("30")
    field(box, "Длительность (мин.)").fill("45")
    field(box, "Повестка").fill(f"{QA_PREFIX} цели на квартал")
    with auth_page.expect_response(lambda r: r.url.endswith("/performance/one-on-ones") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Запланировать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    [meeting] = items(hrm_json(hrm_get(auth_page, "/performance/one-on-ones", {"employee_id": person["id"]})))
    assert (meeting["duration_minutes"], meeting["scheduled_at"][:16], meeting["agenda"]) == \
        (45, f"{TOMORROW}T10:00", f"{QA_PREFIX} цели на квартал"), meeting
    row = main(auth_page).locator("tbody tr").filter(has_text=person["telegram_first_name"])
    expect(row).to_contain_text(TOMORROW.strftime("%d.%m.%Y"))
    expect(row).to_contain_text("45")
    expect(row).to_contain_text(meeting.get("manager_name") or "maxim.turcan1")


# Кейс: «Вовлечённость» — вкладки «Опросы» и «События» показывают то же, что API (без создания)
def test_engagement_lists(auth_page: Page):
    surveys = items(hrm_json(hrm_get(auth_page, "/engagement/surveys")))
    events = items(hrm_json(hrm_get(auth_page, "/engagement/events")))
    open_hrm(auth_page, "/admin/engagement")
    expect(screen_action(auth_page, "Создать опрос")).to_be_visible()
    if surveys:
        for s in surveys:
            expect(main(auth_page)).to_contain_text(s["title"])
    else:
        expect(main(auth_page)).to_contain_text("Нет опросов")
    tab(auth_page, "События")
    for e in events:
        expect(main(auth_page)).to_contain_text(e["title"])
