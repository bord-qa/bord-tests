"""Блок 50. HRM — дашборд (/admin).

Снято 02.11.2026 (в видимом MCP-браузере):
- плитки «Сотрудников / Вакансий / Заявок на отпуск / Обучений» = GET /reports/dashboard
  (total_employees, active_vacancies, pending_leave, active_trainings);
- «С чего начать» «N из M» и шаги-ссылки = GET /onboarding/checklist; «Скрыть» не жмём —
  прячет карточку всей организации; плашка «Здесь лежат примеры» (/starter-samples) —
  «Убрать примеры» тоже не жмём;
- «Нанято сегодня / Уволено сегодня» = GET /reports/dashboard/today {hired, dismissed};
  «Сегодня день рождения 🎂» — по /reports/birthdays?month=<M>; «Отсутствия сегодня и 7 дней» —
  /leave/requests?status=approved&date_from=<сегодня>&date_to=<+7>;
- «Настроить» — выпадашка «Виджеты дашборда» (8 переключателей), клик → PUT
  /dashboard/prefs {hidden_widgets}; настройка своя у пользователя, тест возвращает как было;
- «Быстрый доступ» — 6 ссылок на разделы.
Свой сотрудник для «Нанято сегодня» и дня рождения: принят сегодня, родился в этот день
1990 года, на своём юрлице; после теста удаляется полностью (каскадом, с учёткой SSO).
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (QA_PREFIX, clean_up, create_employee, create_legal_entity, delete_employee, hrm_delete,
                         hrm_get, hrm_json, hrm_put, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]
TILES = [("Сотрудников", "total_employees"), ("Вакансий", "active_vacancies"), ("Заявок на отпуск", "pending_leave"),
         ("Обучений", "active_trainings")]
QUICK = ["/admin/employees", "/admin/recruitment", "/admin/leave", "/admin/training", "/admin/onboarding", "/admin/reports"]


def main(page: Page):
    return page.locator("main")


def card(page: Page, heading: str | re.Pattern):
    return main(page).locator(".rounded-2xl").filter(has=page.get_by_role("heading", name=heading, level=2))


def tile(page: Page, label: str):
    return main(page).locator(".rounded-2xl").filter(has=page.get_by_text(label, exact=True)).first


@pytest.fixture
def newcomer(auth_page: Page):
    """Свой сотрудник: принят сегодня, сегодня день рождения."""
    le = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Новичок {stamp()}",
                          birth_date=str(TODAY.replace(year=1990)) if not (TODAY.month == 2 and TODAY.day == 29) else "1992-02-29")
    yield emp
    delete_employee(auth_page, emp["id"])
    hrm_delete(auth_page, f"/legal-entities/{le['id']}")
    clean_up(auth_page)


# Кейс: Плитки дашборда = /reports/dashboard
def test_tiles_match_api(auth_page: Page):
    with auth_page.expect_response(lambda r: r.url.endswith("/api/hrm/reports/dashboard")) as resp:
        open_hrm(auth_page, "/admin")
    data = resp.value.json()
    for label, key in TILES:
        expect(tile(auth_page, label)).to_have_text(re.compile(rf"^\s*{data[key]}\s*{label}\s*$"))


# Кейс: «С чего начать» — «N из M» и шаги = /onboarding/checklist
def test_checklist_matches_api(auth_page: Page):
    with auth_page.expect_response(lambda r: "/api/hrm/onboarding/checklist" in r.url) as resp:
        open_hrm(auth_page, "/admin")
    steps = resp.value.json()["items"]
    box = card(auth_page, "С чего начать")
    expect(box).to_contain_text(f"{sum(s['done'] for s in steps)} из {len(steps)}")
    for s in steps:
        expect(box.get_by_role("link").filter(has_text=s["title"])).to_have_attribute("href", s["href"])


# Кейс: «Нанято сегодня» и «Сегодня день рождения» — свой сотрудник
@pytest.mark.writes_data
def test_hired_today_and_birthday(auth_page: Page, newcomer):
    name = newcomer["telegram_first_name"]
    with auth_page.expect_response(lambda r: "/api/hrm/reports/dashboard/today" in r.url) as resp:
        open_hrm(auth_page, "/admin")
    hired = resp.value.json()["hired"]
    assert newcomer["id"] in [h.get("id") or h.get("employee_id") for h in hired["items"]], hired
    box = card(auth_page, "Нанято сегодня")
    expect(box).to_contain_text(str(hired["count"]))
    expect(box).to_contain_text(name)

    bd = card(auth_page, re.compile(r"^Сегодня день рождения"))
    expect(bd).to_contain_text(f"{TODAY.day} {MONTHS_GEN[TODAY.month - 1]}")
    expect(bd).to_contain_text(name)
    month = hrm_json(hrm_get(auth_page, "/reports/birthdays", {"month": TODAY.month}))
    assert newcomer["id"] in [b.get("id") or b.get("employee_id") for b in month["items"]], month

    tiles = hrm_json(hrm_get(auth_page, "/reports/dashboard"))
    expect(tile(auth_page, "Сотрудников")).to_contain_text(str(tiles["total_employees"]))


# Кейс: «Отсутствия» на неделю — пусто или те же люди, что в API
def test_absences_week(auth_page: Page):
    with auth_page.expect_response(lambda r: "/api/hrm/leave/requests" in r.url and "status=approved" in r.url) as resp:
        open_hrm(auth_page, "/admin")
    assert f"date_from={TODAY}" in resp.value.url and f"date_to={TODAY + dt.timedelta(days=7)}" in resp.value.url
    leaves = items(resp.value.json())
    box = card(auth_page, "Отсутствия")
    expect(box).to_contain_text("сегодня и 7 дней")
    if not leaves:
        expect(box).to_contain_text("На этой неделе отсутствий нет")


# Кейс: «Настроить» — скрытый виджет пропадает и остаётся скрытым после перезагрузки
@pytest.mark.writes_data
def test_customize_widgets(auth_page: Page):
    before = hrm_json(hrm_get(auth_page, "/dashboard/prefs"))["hidden_widgets"]
    if "active_trainings" in before:
        hrm_put(auth_page, "/dashboard/prefs", {"hidden_widgets": [w for w in before if w != "active_trainings"]})
    try:
        open_hrm(auth_page, "/admin")
        header = auth_page.locator("header")
        header.get_by_role("button", name="Настроить", exact=True).click()
        expect(header.get_by_text("Виджеты дашборда", exact=True)).to_be_visible()
        with auth_page.expect_response(lambda r: "/api/hrm/dashboard/prefs" in r.url and r.request.method == "PUT") as resp:
            header.get_by_role("button", name="Обучений", exact=True).click()
        assert resp.value.ok, resp.value.text()
        expect(main(auth_page).get_by_text("Обучений", exact=True)).to_have_count(0)
        assert "active_trainings" in hrm_json(hrm_get(auth_page, "/dashboard/prefs"))["hidden_widgets"]
        auth_page.reload()
        expect(tile(auth_page, "Сотрудников")).to_be_visible()
        expect(main(auth_page).get_by_text("Обучений", exact=True)).to_have_count(0)

        header.get_by_role("button", name="Настроить", exact=True).click()
        with auth_page.expect_response(lambda r: "/api/hrm/dashboard/prefs" in r.url and r.request.method == "PUT"):
            header.get_by_role("button", name="Обучений", exact=True).click()
        expect(tile(auth_page, "Обучений")).to_be_visible()
    finally:
        hrm_put(auth_page, "/dashboard/prefs", {"hidden_widgets": before})


# Кейс: «Быстрый доступ» — ссылки на разделы
def test_quick_links(auth_page: Page):
    open_hrm(auth_page, "/admin")
    expect(main(auth_page).get_by_role("heading", name="Быстрый доступ")).to_be_visible()
    for href in QUICK:
        expect(main(auth_page).locator(f"a[href='{href}']").last).to_be_visible()
