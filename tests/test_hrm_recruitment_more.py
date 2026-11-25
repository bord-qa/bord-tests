"""Блок 68. HRM — рекрутинг (канбан, настройка воронок, отклик, собеседование и оценочный лист, правка
кандидата) и отклонение заявки на повышение.

Снято 03.11.2026 в видимом MCP-браузере:
- «Вакансии» → в шапке «Список / Канбан»; канбан: выбор воронки, значок «Настроить воронки», выбор
  вакансии «<вакансия> (N заявок)», «Добавить кандидата», колонки этапов «<этап> N» с карточками
  (draggable) и «Добавить кандидата на этот этап»; перенос карточки в другую колонку (HTML5 drag&drop) →
  POST /recruit/candidates/<id>/move {stage_id}; «Настроить воронки» (после гидрации) → «Настройка
  воронок»: список «<воронка> · N этапов» с «Редактировать / Удалить», «Создать воронку» → «Новая
  воронка» («Название *», «По умолчанию») → POST /recruit/pipelines; «Удалить» → «Удалить воронку? Это
  действие нельзя отменить.» → «Да»; этапы воронки на экране не правятся (только API);
- собеседования и оценочные листы на экране кандидата не показаны — только API по отклику
  (/recruit/applications → /interviews, /interviews/<id>/status, /scorecards; у кандидата —
  /candidates/<id>/interviews, /scorecards);
- у строки кандидата «Редактировать» → форма как «Добавить кандидата» → PUT /recruit/candidates/<id>;
- «Повышение квалификации» → карточка заявки → «Статус» «Отклонена» + «Решение для сотрудника» → PATCH,
  в списке «Отклонена», «История статусов» «Новая → Отклонена».
"""
import datetime as dt

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (screen_action, QA_PREFIX, RAISE_MANAGER, clean_up, create_employee, create_legal_entity, delete_employee, dropdown, field,
                         hrm_delete, hrm_get, hrm_json, hrm_post, hrm_put, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()
DND = """async ([card, column]) => {
    const pause = ms => new Promise(r => setTimeout(r, ms));  // React должен увидеть dragstart до dragover
    const dt = new DataTransfer();
    card.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));
    await pause(200);
    column.dispatchEvent(new DragEvent('dragenter', {bubbles: true, dataTransfer: dt}));
    column.dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true, dataTransfer: dt}));
    await pause(200);
    column.dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: dt}));
    card.dispatchEvent(new DragEvent('dragend', {bubbles: true, dataTransfer: dt}));
}"""


def main(page: Page):
    return page.locator("main")


def dialog(page: Page, text: str):
    return page.locator("div.fixed.inset-0:visible").filter(has_text=text).last


@pytest.fixture
def board(auth_page: Page):
    """Своя воронка «Отклик → Интервью → Оффер», открытая вакансия и кандидат на «Отклике» с откликом."""
    pl = hrm_json(hrm_post(auth_page, "/recruit/pipelines", {"name": f"{QA_PREFIX} Воронка канбан {stamp()}"}))
    stages = {n: hrm_json(hrm_post(auth_page, f"/recruit/pipelines/{pl['id']}/stages",
                                   {"name": n, "stage_type": t, "ordering": i}))["id"]
              for i, (n, t) in enumerate([("Отклик", "applied"), ("Интервью", "interview"), ("Оффер", "offer")])}
    job = hrm_json(hrm_post(auth_page, "/recruit/jobs", {"title": f"{QA_PREFIX} Вакансия канбан {stamp()}",
                                                         "pipeline_id": pl["id"], "headcount": 1}))
    hrm_json(hrm_put(auth_page, f"/recruit/jobs/{job['id']}", {"status": "open"}))
    first = f"{QA_PREFIX} Канбан {stamp()}"
    cand = hrm_json(hrm_post(auth_page, "/recruit/candidates", {"first_name": first,
                                                                "current_job_posting_id": job["id"],
                                                                "current_stage_id": stages["Отклик"]}))
    app = hrm_json(hrm_post(auth_page, "/recruit/applications", {"candidate_id": cand["id"], "job_posting_id": job["id"],
                                                                 "stage_id": stages["Отклик"]}))
    yield {"pipeline": pl, "stages": stages, "job": job, "cand": {**cand, "first_name": first}, "app": app}
    hrm_delete(auth_page, f"/recruit/candidates/{cand['id']}")
    hrm_put(auth_page, f"/recruit/jobs/{job['id']}", {"status": "cancelled"})
    hrm_delete(auth_page, f"/recruit/jobs/{job['id']}")
    for p in hrm_json(hrm_get(auth_page, "/recruit/pipelines")):
        if p["name"].startswith(QA_PREFIX):
            hrm_delete(auth_page, f"/recruit/pipelines/{p['id']}")


def open_kanban(page: Page, board: dict):
    open_hrm(page, "/admin/recruitment/vacancies")
    page.wait_for_load_state("networkidle")  # до гидрации кнопки канбана не срабатывают
    page.locator("header button:visible").filter(has_text="Канбан").first.click()
    expect(screen_action(page, "Настроить воронки")).to_be_visible()
    pipe = main(page).get_by_role("button").filter(has_text=board["pipeline"]["name"])
    if not pipe.count():
        main(page).locator("button[aria-haspopup=listbox]").first.click()
        page.get_by_role("option", name=board["pipeline"]["name"], exact=True).click()
    expect(main(page)).to_contain_text(f"{board['job']['title']} (1 заявок)")


def column(page: Page, stage: str):
    return main(page).locator("div[class*='min-w-[280px]']").filter(has_text=stage).first


# Кейс: Канбан вакансии — колонки этапов своей воронки, перенос карточки «Отклик → Интервью» = /candidates/<id>/move
@pytest.mark.writes_data
def test_kanban_drag_candidate(auth_page: Page, board):
    open_kanban(auth_page, board)
    applied, interview = column(auth_page, "Отклик"), column(auth_page, "Интервью")
    expect(applied).to_contain_text(board["cand"]["first_name"])
    expect(interview).to_contain_text("Нет кандидатов")
    card = applied.locator("[draggable=true]").first
    with auth_page.expect_response(lambda r: f"/recruit/candidates/{board['cand']['id']}/move" in r.url) as mv:
        auth_page.evaluate(DND, [card.element_handle(), interview.element_handle()])
    assert mv.value.ok and mv.value.request.post_data_json == {"stage_id": board["stages"]["Интервью"]}, mv.value.text()
    expect(column(auth_page, "Интервью").locator("[draggable=true]")).to_have_count(1)
    expect(column(auth_page, "Отклик")).to_contain_text("Нет кандидатов")
    got = hrm_json(hrm_get(auth_page, f"/recruit/candidates/{board['cand']['id']}"))
    assert got["current_stage_id"] == board["stages"]["Интервью"], got


# Кейс: «Настроить воронки» — «Создать воронку» = API, «Удалить воронку?» → «Да»
@pytest.mark.writes_data
def test_pipeline_settings(auth_page: Page, board):
    name = f"{QA_PREFIX} Воронка форма {stamp()}"
    open_kanban(auth_page, board)
    screen_action(auth_page, "Настроить воронки").click()
    box = dialog(auth_page, "Настройка воронок")
    expect(box).to_contain_text(f"{board['pipeline']['name']}")
    expect(box).to_contain_text("3 этапов")
    box.get_by_role("button", name="Создать воронку").click()
    box.get_by_placeholder("Например: Стандартная воронка").fill(name)
    with auth_page.expect_response(lambda r: r.url.endswith("/recruit/pipelines") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok and resp.value.request.post_data_json == {"name": name, "is_default": False}, resp.value.text()
    pid = resp.value.json()["id"]
    expect(box).to_contain_text("Воронка создана")
    row = box.locator("div").filter(has_text=name).filter(has=auth_page.get_by_role("button", name="Удалить")).last
    expect(row).to_contain_text("0 этапов")
    row.get_by_role("button", name="Удалить").click()
    sure = dialog(auth_page, "Удалить воронку? Это действие нельзя отменить.")
    with auth_page.expect_response(lambda r: f"/recruit/pipelines/{pid}" in r.url and r.request.method == "DELETE") as gone:
        sure.get_by_role("button", name="Да", exact=True).click()
    assert gone.value.ok, gone.value.text()
    assert pid not in [p["id"] for p in hrm_json(hrm_get(auth_page, "/recruit/pipelines"))]


# Кейс: Собеседование и оценочный лист по отклику (API) — видны у кандидата; статус собеседования «completed»
@pytest.mark.writes_data
def test_interview_and_scorecard(auth_page: Page, board):
    when = dt.datetime.combine(TODAY + dt.timedelta(days=2), dt.time(11, 0)).isoformat()
    iv = hrm_json(hrm_post(auth_page, "/recruit/interviews", {"application_id": board["app"]["id"], "interview_type": "technical",
                                                              "scheduled_at": when, "duration_minutes": 45, "location": "Zoom"}))
    assert hrm_put(auth_page, f"/recruit/interviews/{iv['id']}/status?status=completed").ok  # статус — query-параметр
    sc = hrm_json(hrm_post(auth_page, "/recruit/scorecards", {"application_id": board["app"]["id"], "interview_id": iv["id"],
                                                              "overall_rating": 4, "recommendation": "hire",
                                                              "strengths": "QA сильные стороны", "weaknesses": "QA зоны роста"}))
    [got_iv] = items(hrm_json(hrm_get(auth_page, f"/recruit/candidates/{board['cand']['id']}/interviews")))
    assert (got_iv["id"], got_iv["status"], got_iv["duration_minutes"], got_iv["scheduled_at"][:16]) == \
        (iv["id"], "completed", 45, when[:16]), got_iv
    [got_sc] = items(hrm_json(hrm_get(auth_page, f"/recruit/candidates/{board['cand']['id']}/scorecards")))
    assert (got_sc["id"], got_sc["overall_rating"], got_sc["recommendation"], got_sc["interview_id"]) == \
        (sc["id"], 4, "hire", iv["id"]), got_sc


# Кейс: «Редактировать» кандидата — текущая позиция, компания, опыт = API
@pytest.mark.writes_data
def test_edit_candidate(auth_page: Page, board):
    first = board["cand"]["first_name"]
    open_hrm(auth_page, "/admin/recruitment/candidates")
    row = main(auth_page).locator("tbody tr").filter(has_text=first)
    row.get_by_role("button", name="Редактировать").click()
    box = dialog(auth_page, "Имя *")
    expect(field(box, "Имя *")).to_have_value(first)
    field(box, "Текущая позиция").fill("QA инженер")
    field(box, "Текущая компания").fill("QA Corp")
    field(box, "Опыт (лет)").fill("3")
    with auth_page.expect_response(lambda r: f"/recruit/candidates/{board['cand']['id']}" in r.url and r.request.method == "PUT") as upd:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert upd.value.ok, upd.value.text()
    got = hrm_json(hrm_get(auth_page, f"/recruit/candidates/{board['cand']['id']}"))
    assert (got["current_position"], got["current_company"], got["experience_years"], got["first_name"]) == \
        ("QA инженер", "QA Corp", 3, first), got


# ───────────────────────── повышения ─────────────────────────

@pytest.fixture
def person(auth_page: Page):
    le = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Повышение {stamp()}")
    yield emp
    delete_employee(auth_page, emp["id"])
    clean_up(auth_page)


# Кейс: Заявка на повышение «Новая» → «Отклонена» с решением для сотрудника — список и история = API
@pytest.mark.writes_data
def test_raise_request_reject(auth_page: Page, person):
    rr = hrm_json(hrm_post(auth_page, "/raise-requests", {"employee_id": person["id"], "current_salary": 10000,
                                                          "desired_salary": 13000, "salary_period": "month",
                                                          "currency": "MDL", "achievements": "QA",
                                                          "manager_ids": [RAISE_MANAGER]}))
    open_hrm(auth_page, "/admin/raise-requests")
    row = main(auth_page).locator("tr").filter(has_text=person["telegram_first_name"])
    expect(row).to_contain_text("Новая")
    row.click()
    card = auth_page.locator("div.fixed.inset-0:visible").filter(
        has=auth_page.get_by_role("heading", name=person["telegram_first_name"], exact=True)).last
    dropdown(auth_page, card, "Статус", "Отклонена")
    field(card, "Решение для сотрудника (сотрудник видит)").fill("QA вернёмся через полгода")
    with auth_page.expect_response(lambda r: f"/raise-requests/{rr['id']}" in r.url and r.request.method == "PATCH") as upd:
        card.get_by_role("button", name="Сохранить", exact=True).click()
    assert upd.value.ok, upd.value.text()
    expect(card.locator("li").filter(has_text="Новая → Отклонена")).to_be_visible()
    expect(row).to_contain_text("Отклонена")
    got = hrm_json(hrm_get(auth_page, f"/raise-requests/{rr['id']}"))
    assert (got["status"], got["decision_comment"]) == ("rejected", "QA вернёмся через полгода"), got
    assert [h["to_status"] for h in got["history"]] == ["new", "rejected"], got["history"]
