"""Блок 54. HRM — проекты (/admin/projects).

Снято 02.11.2026 (в видимом MCP-браузере и по справке help.bord.one/hrm/proekty):
- список = GET /projects ({items}); пусто — «Ещё нет проектов» и «Создать первый проект»;
  в шапке «Новый проект»; поиск и галочка «Показать неактивные» (неактивные скрыты);
- «Новый проект» → окно: «Название *», «Описание», «Статус *» (Планирование / В работе /
  Приостановлено / Завершено), «Тип объекта» (справочник «Типы объектов»), «Цвет», «Отдел*»
  (обязателен — «Поле обязательное»), «Руководитель проекта», «Работники проекта» («Добавить
  работника...») → POST /projects;
- карточка только для просмотра: статус, тип объекта, руководитель, работники; карандаш
  «Редактировать» (то же окно, PATCH /projects/<id>) и «Удалить» (DELETE);
- уволенный сотрудник сам уходит из работников и из «Руководителя проекта».
Свои отдел, тип объекта, юрлицо и сотрудники — после теста удаляются.
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (QA_PREFIX, clean_up, create_employee, create_legal_entity, delete_employee, dropdown, field,
                         header_button, hrm_delete, hrm_get, hrm_json, hrm_post, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth


def main(page: Page):
    return page.locator("main")


def project_card(page: Page, name: str):
    """Карточка целиком (рамка со скруглением): шапка с названием и кнопками + руководитель и работники."""
    return main(page).locator("div[class*='rounded'][class*='border']").filter(has=page.get_by_text(name, exact=True)).last


@pytest.fixture
def team(auth_page: Page):
    """Своё юрлицо, отдел, тип объекта, руководитель и работник."""
    le = create_legal_entity(auth_page)
    dep = hrm_json(hrm_post(auth_page, "/org/departments", {"name": f"{QA_PREFIX} Отдел проекта {stamp()}", "legal_entity_id": le["id"]}))
    otype = hrm_json(hrm_post(auth_page, "/org/project-object-types", {"name": f"{QA_PREFIX} Объект {stamp()}"}))
    boss = create_employee(auth_page, le["id"], f"{QA_PREFIX} Прораб {stamp()}")
    worker = create_employee(auth_page, le["id"], f"{QA_PREFIX} Рабочий {stamp()}")
    data = {"le": le, "dep": dep, "otype": otype, "boss": boss, "worker": worker}
    yield data
    for p in items(hrm_json(hrm_get(auth_page, "/projects", {"include_inactive": "true"}))):
        if p["name"].startswith(QA_PREFIX):
            hrm_delete(auth_page, f"/projects/{p['id']}")
    for emp in (boss, worker):
        delete_employee(auth_page, emp["id"])
    hrm_delete(auth_page, f"/org/project-object-types/{otype['id']}")
    hrm_delete(auth_page, f"/org/departments/{dep['id']}")
    clean_up(auth_page)


def create_project_via_form(page: Page, team: dict, name: str) -> dict:
    header_button(page, "Новый проект").click()
    box = page.locator("div.fixed:visible").filter(has_text="Новый проект").last
    field(box, "Название *").fill(name)
    field(box, "Описание").fill(f"{QA_PREFIX} описание")
    expect(field(box, "Статус *")).to_contain_text("Планирование")
    expect(box).to_contain_text("Поле обязательное")  # отдел ещё не выбран
    dropdown(page, box, "Тип объекта", team["otype"]["name"])
    dropdown(page, box, "Отдел*", team["dep"]["name"])
    dropdown(page, box, "Руководитель проекта", team["boss"]["telegram_first_name"])
    dropdown(page, box, "Работники проекта", team["worker"]["telegram_first_name"])  # combobox с выбором нескольких
    page.keyboard.press("Escape")
    expect(field(box, "Работники проекта")).to_contain_text(team["worker"]["telegram_first_name"])
    with page.expect_response(lambda r: r.url.split("?")[0].rstrip("/").endswith("/api/hrm/projects") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    return resp.value.json()


# Кейс: Проекты = API (пусто — «Ещё нет проектов»)
def test_list_matches_api(auth_page: Page):
    # точный путь: экран ещё зовёт /api/hrm/projects/assignable-employees (сотрудники, не проекты)
    with auth_page.expect_response(lambda r: r.url.split("?")[0].rstrip("/").endswith("/api/hrm/projects")
                                   and r.request.method == "GET") as resp:
        open_hrm(auth_page, "/admin/projects")
    projects = items(resp.value.json())
    if not projects:
        expect(main(auth_page)).to_contain_text("Ещё нет проектов")
        expect(main(auth_page).get_by_role("button", name="Создать первый проект")).to_be_visible()
    for p in projects:
        expect(main(auth_page).get_by_text(p["name"], exact=True).first).to_be_visible()


# Кейс: Новый проект — отдел, тип объекта, руководитель, работник; правка статуса; удаление
@pytest.mark.writes_data
def test_project_lifecycle(auth_page: Page, team):
    name = f"{QA_PREFIX} Проект {stamp()}"
    open_hrm(auth_page, "/admin/projects")
    created = create_project_via_form(auth_page, team, name)
    prj = next(p for p in items(hrm_json(hrm_get(auth_page, "/projects"))) if p["id"] == created["id"])
    assert (prj["department_id"], prj["manager_id"], prj["object_type_id"], prj["status"]) == \
        (team["dep"]["id"], team["boss"]["id"], team["otype"]["id"], "planning"), prj
    assert team["worker"]["id"] in [m.get("id", m.get("employee_id")) if isinstance(m, dict) else m
                                     for m in prj.get("members") or prj.get("member_ids") or []], prj

    card = project_card(auth_page, name)
    for text in ("Планирование", team["otype"]["name"], team["boss"]["telegram_first_name"], team["worker"]["telegram_first_name"]):
        expect(card).to_contain_text(text)

    # правка: статус «В работе»
    card.get_by_title("Редактировать").click()
    box = auth_page.locator("div.fixed:visible").last
    dropdown(auth_page, box, "Статус *", "В работе")
    with auth_page.expect_response(lambda r: f"/api/hrm/projects/{created['id']}" in r.url and r.request.method in ("PATCH", "PUT")) as upd:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert upd.value.ok, upd.value.text()
    expect(project_card(auth_page, name)).to_contain_text("В работе")

    # удаление — с предупреждением, сотрудники остаются
    project_card(auth_page, name).get_by_title("Удалить").click()
    dlg = auth_page.locator("div.fixed:visible").last
    expect(dlg).to_contain_text(name)
    with auth_page.expect_response(lambda r: f"/api/hrm/projects/{created['id']}" in r.url and r.request.method == "DELETE") as gone:
        dlg.get_by_role("button", name=re.compile("^(Удалить|Да)$")).last.click()
    assert gone.value.ok, gone.value.text()
    expect(main(auth_page).get_by_text(name, exact=True)).to_have_count(0)
    assert hrm_get(auth_page, f"/employees/{team['worker']['id']}").ok


# Кейс: Уволенный сотрудник уходит из работников и из руководителей проекта
@pytest.mark.writes_data
def test_dismissed_leaves_project(auth_page: Page, team):
    prj = hrm_json(hrm_post(auth_page, "/projects", {
        "name": f"{QA_PREFIX} Проект увольнения {stamp()}", "department_id": team["dep"]["id"],
        "manager_id": team["boss"]["id"], "member_ids": [team["worker"]["id"]], "status": "active"}))
    for emp in (team["boss"], team["worker"]):
        resp = hrm_post(auth_page, f"/employees/{emp['id']}/deactivate", {"termination_date": str(__import__("datetime").date.today())})
        assert resp.ok, resp.text()
    got = next(p for p in items(hrm_json(hrm_get(auth_page, "/projects"))) if p["id"] == prj["id"])
    members = [m.get("id", m.get("employee_id")) if isinstance(m, dict) else m for m in got.get("members") or got.get("member_ids") or []]
    assert team["worker"]["id"] not in members, got
    assert got["manager_id"] is None, got
    open_hrm(auth_page, "/admin/projects")
    card = project_card(auth_page, prj["name"])
    expect(card).not_to_contain_text(team["worker"]["telegram_first_name"])
    expect(card).not_to_contain_text(team["boss"]["telegram_first_name"])
