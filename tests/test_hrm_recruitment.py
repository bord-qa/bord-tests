"""Блок 60. HRM — рекрутинг (/admin/recruitment/*) и источники откликов (/admin/recruitment/sources).

Снято 03.11.2026 в видимом MCP-браузере:
- раздел — ссылки «Дашборд / Вакансии / Кандидаты / Источники»; дашборд — фильтры «С даты», «По дату»,
  «Все вакансии», «Все отделы», «Применить», «Период: …», без данных — «Нет данных за выбранный период»;
- воронок в организации нет и экрана для них нет: тест заводит свою воронку с этапами через API
  (/recruit/pipelines, /stages) и удаляет её; этапы кандидата берутся из воронки вакансии;
- «Вакансии»: фильтр статусов «Все / Черновик / Открыта / На паузе / Закрыта / Отменена»,
  «Новая вакансия» → окно: «Название *», «Отдел», «Должность», «Воронка», «Рекрутер», «Количество мест»,
  «Город», «Проекты…», «Тип занятости», «Формат работы», «Зарплата» (От / До / валюта), «Описание»,
  «Требования», «Обязанности», «Навыки» → POST /recruit/jobs → строка «Черновик»; клик по строке —
  карточка («ОСНОВНАЯ ИНФО», «УСЛОВИЯ» — «15000 – 25000 MDL», «КОМАНДА» — «Воронка»), статус — кнопка с
  выпадашкой → PUT {status}; значок статуса в открытой карточке после смены не обновляется (список — да);
  удалить можно только черновик / отменённую («Can only delete draft or cancelled postings»), на
  экране удаления нет — уборка через «Отменена» + DELETE;
- «Кандидаты»: «Скачать в Excel», «Добавить кандидата» → «Имя *», «Фамилия», «Email», «Телефон»,
  «Источник» (+ ссылка «управление источниками» → окно «Управление источниками»: «Новый источник...» →
  «Добавить», у строки «Переименовать» / «Удалить»), «Вакансия», «Этап», … → POST /recruit/candidates;
  строка: вакансия, дата, «Статус рекрутинга» = этап, источник; значки «Редактировать», «Удалить навсегда»;
  карточка «Кандидат #N»: «Статус» (Активен / Отклонён / Нанят), «Этап» → «Сохранить этап»
  (POST …/move), «Отклонить» → «Отклонить кандидата?» → «Да» (POST …/reject), «Комментарии» →
  «Добавить комментарий», «История» («Смена этапа · Отклик → Интервью», «Добавлен комментарий»,
  «Кандидат отклонён»); после действий карточка показывает старый «Статус» до перезагрузки;
  «Удалить навсегда» → «Удалить кандидата навсегда? … этапы, комментарии, файлы, собеседования.»;
- найма в сотрудники на экране нет (этап типа hired статус не меняет) — только API: …/hire →
  …/convert-to-employee → сотрудник HRM (= участник организации) и сразу испытательный срок;
- «Источники откликов» — опрос IMAP / hh.ru / rabota.ru / SuperJob с логином и паролем: проверяем только
  форму (без заполнения «Создать» неактивна), источник не заводим.
Кандидатов без email — никаких писем; нанятый сотрудник удаляется каскадом.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (HRM, QA_PREFIX, clean_up, delete_employee, dropdown, field, hrm_delete, hrm_get, hrm_json,
                         hrm_post, hrm_put, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()
STAGES = [("Отклик", "applied"), ("Интервью", "interview"), ("Оффер", "offer"), ("Нанят", "hired"), ("Отказ", "rejected")]


def main(page: Page):
    return page.locator("main")


def modal(page: Page, text: str):
    return page.locator("div.fixed.inset-0:visible").filter(has_text=text).last


def pick(page: Page, button, option: str):
    """Выпадашка, у которой кнопка найдена не по подписи (несколько кнопок рядом)."""
    opt = page.get_by_role("option", name=option, exact=True)
    for _ in range(3):
        button.click()
        try:
            expect(opt).to_be_visible(timeout=2_000)
            break
        except AssertionError:
            pass
    opt.click()


def remove_own(page: Page):
    for c in items(hrm_json(hrm_get(page, "/recruit/candidates", {"limit": 500}))):
        if (c.get("full_name") or c.get("first_name") or "").startswith(QA_PREFIX):
            if c.get("linked_employee_id"):
                delete_employee(page, c["linked_employee_id"])
            hrm_delete(page, f"/recruit/candidates/{c['id']}")
    for j in items(hrm_json(hrm_get(page, "/recruit/jobs", {"limit": 500}))):
        if j["title"].startswith(QA_PREFIX):
            if j["status"] not in ("draft", "cancelled"):
                hrm_put(page, f"/recruit/jobs/{j['id']}", {"status": "cancelled"})
            hrm_delete(page, f"/recruit/jobs/{j['id']}")
    for p in hrm_json(hrm_get(page, "/recruit/pipelines")):
        if p["name"].startswith(QA_PREFIX):
            hrm_delete(page, f"/recruit/pipelines/{p['id']}")
    for s in items(hrm_json(hrm_get(page, "/recruit/candidate-sources"))):
        if s["name"].startswith(QA_PREFIX):
            hrm_delete(page, f"/recruit/candidate-sources/{s['id']}")


@pytest.fixture
def pipeline(auth_page: Page):
    """Своя воронка с пятью этапами — {id, name, stages: {название: id}}."""
    pl = hrm_json(hrm_post(auth_page, "/recruit/pipelines", {"name": f"{QA_PREFIX} Воронка {stamp()}"}))
    stages = {}
    for i, (name, kind) in enumerate(STAGES):
        stages[name] = hrm_json(hrm_post(auth_page, f"/recruit/pipelines/{pl['id']}/stages",
                                         {"name": name, "stage_type": kind, "ordering": i}))["id"]
    yield {**pl, "stages": stages}
    remove_own(auth_page)
    clean_up(auth_page)


@pytest.fixture
def job(auth_page: Page, pipeline):
    j = hrm_json(hrm_post(auth_page, "/recruit/jobs", {"title": f"{QA_PREFIX} Вакансия {stamp()}", "pipeline_id": pipeline["id"],
                                                       "headcount": 1}))
    hrm_json(hrm_put(auth_page, f"/recruit/jobs/{j['id']}", {"status": "open"}))
    return {**j, "pipeline": pipeline}


def get_job(page: Page, job_id: int) -> dict:
    return hrm_json(hrm_get(page, f"/recruit/jobs/{job_id}"))


# Кейс: Дашборд рекрутинга — навигация раздела, фильтры периода, счётчики = /recruit/stats
def test_recruitment_dashboard(auth_page: Page):
    open_hrm(auth_page, "/admin/recruitment")
    nav = main(auth_page).get_by_role("link")
    for name, href in (("Дашборд", "/admin/recruitment"), ("Вакансии", "/admin/recruitment/vacancies"),
                       ("Кандидаты", "/admin/recruitment/candidates"), ("Источники", "/admin/recruitment/sources")):
        expect(nav.filter(has_text=name).first).to_have_attribute("href", href)
    month_ago = TODAY - dt.timedelta(days=30)
    expect(main(auth_page)).to_contain_text(f"Период: {month_ago} -- {TODAY}")
    for label in ("С даты", "По дату"):
        expect(main(auth_page).get_by_text(label, exact=True)).to_be_visible()
    expect(main(auth_page).get_by_role("button", name="Применить")).to_be_visible()
    stats = hrm_json(hrm_get(auth_page, "/recruit/stats"))
    assert set(stats) >= {"open_jobs", "active_applications", "hired_this_month", "candidate_pool"}, stats


# Кейс: Вакансия — форма с воронкой и вилкой = API, строка «Черновик» → статус «Открыта» в карточке, фильтр статусов
@pytest.mark.writes_data
def test_vacancy_form(auth_page: Page, pipeline):
    title = f"{QA_PREFIX} Вакансия {stamp()}"
    open_hrm(auth_page, "/admin/recruitment/vacancies")
    main(auth_page).get_by_role("button", name="Новая вакансия").click()
    box = modal(auth_page, "Новая вакансия")
    field(box, "Название *").fill(title)
    dropdown(auth_page, box, "Воронка", pipeline["name"])
    field(box, "Количество мест").fill("2")
    expect(field(box, "Тип занятости")).to_contain_text("Полная занятость")
    expect(field(box, "Формат работы")).to_contain_text("Офис")
    box.get_by_placeholder("От", exact=True).fill("15000")
    box.get_by_placeholder("До", exact=True).fill("25000")
    field(box, "Требования").fill("QA: опыт от года")
    with auth_page.expect_response(lambda r: r.url.endswith("/recruit/jobs") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    j = get_job(auth_page, resp.value.json()["id"])
    assert (j["title"], j["pipeline_id"], j["headcount"], j["salary_min"], j["salary_max"], j["salary_currency"],
            j["employment_type"], j["work_location"], j["requirements"], j["status"]) == \
        (title, pipeline["id"], 2, 15000.0, 25000.0, "MDL", "full_time", "office", "QA: опыт от года", "draft"), j
    row = main(auth_page).locator("tbody tr").filter(has_text=title)
    expect(row).to_contain_text("Черновик")
    expect(row).to_contain_text(TODAY.strftime("%d.%m.%Y"))

    row.click()
    card = modal(auth_page, "ОСНОВНАЯ ИНФО")
    expect(card).to_contain_text(title)
    expect(card).to_contain_text("15000 – 25000 MDL")
    expect(card).to_contain_text(pipeline["name"])
    expect(card).to_contain_text("QA: опыт от года")
    with auth_page.expect_response(lambda r: f"/recruit/jobs/{j['id']}" in r.url and r.request.method == "PUT") as upd:
        pick(auth_page, card.get_by_role("button", name="Черновик", exact=True), "Открыта")
    assert upd.value.request.post_data_json == {"status": "open"} and upd.value.ok, upd.value.text()
    assert get_job(auth_page, j["id"])["status"] == "open"
    expect(row).to_contain_text("Открыта")
    auth_page.keyboard.press("Escape")

    auth_page.goto(HRM + "/admin/recruitment/vacancies")
    main(auth_page).get_by_role("button", name="Черновик", exact=True).click()
    expect(main(auth_page).locator("tbody tr").filter(has_text=title)).to_have_count(0)
    main(auth_page).get_by_role("button", name="Открыта", exact=True).click()
    expect(main(auth_page).locator("tbody tr").filter(has_text=title)).to_have_count(1)


# Кейс: Кандидат — свой источник, вакансия и этап из её воронки = API; «Сохранить этап», комментарий, «История», «Отклонить», «Удалить навсегда»
@pytest.mark.writes_data
def test_candidate_flow(auth_page: Page, job):
    first, src = f"{QA_PREFIX} Кандидат {stamp()}", f"{QA_PREFIX} Источник {stamp()}"
    stages = job["pipeline"]["stages"]
    open_hrm(auth_page, "/admin/recruitment/candidates")
    main(auth_page).get_by_role("button", name="Добавить кандидата").click()
    box = modal(auth_page, "Добавить кандидата")
    field(box, "Имя *").fill(first)
    field(box, "Фамилия").fill("Тестовый")

    box.get_by_role("button", name="управление источниками").click()
    mgr = auth_page.locator("div.fixed.inset-0:visible").filter(
        has=auth_page.get_by_role("heading", name="Управление источниками")).last
    expect(mgr.get_by_role("button", name="Добавить", exact=True)).to_be_disabled()
    mgr.get_by_placeholder("Новый источник...").fill(src)
    with auth_page.expect_response(lambda r: r.url.endswith("/recruit/candidate-sources") and r.request.method == "POST") as s:
        mgr.get_by_role("button", name="Добавить", exact=True).click()
    assert s.value.ok, s.value.text()
    line = mgr.locator("div").filter(has_text=src).filter(has=auth_page.get_by_role("button", name="Удалить")).last
    expect(line.get_by_role("button", name="Переименовать")).to_be_visible()
    mgr.locator("button").first.click()  # крестик
    expect(mgr).to_be_hidden()

    source_btn = box.locator("xpath=.//label[normalize-space(.)='Источник']/parent::*/following-sibling::*[1]"
                             "/descendant-or-self::button[1]")
    pick(auth_page, source_btn, src)
    dropdown(auth_page, box, "Вакансия", job["title"])
    dropdown(auth_page, box, "Этап", "Отклик")
    with auth_page.expect_response(lambda r: r.url.endswith("/recruit/candidates") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    cid = resp.value.json()["id"]
    c = hrm_json(hrm_get(auth_page, f"/recruit/candidates/{cid}"))
    assert (c["first_name"], c["last_name"], c["source"], c["current_job_posting_id"], c["current_stage_id"],
            c["funnel_status"], c["email"]) == (first, "Тестовый", src, job["id"], stages["Отклик"], "active", None), c
    row = main(auth_page).locator("tbody tr").filter(has_text=first)
    for text in (job["title"], TODAY.strftime("%d.%m.%Y"), "Отклик", src):
        expect(row).to_contain_text(text)

    row.locator("td").first.click()
    card = modal(auth_page, f"Кандидат #{cid}")
    expect(card).to_contain_text("Активен")
    with auth_page.expect_response(lambda r: f"/recruit/candidates/{cid}/move" in r.url) as mv:
        pick(auth_page, card.get_by_role("button", name="Отклик", exact=True), "Интервью")
        card.get_by_role("button", name="Сохранить этап").click()
    assert mv.value.request.post_data_json == {"stage_id": stages["Интервью"]} and mv.value.ok, mv.value.text()
    note = f"{QA_PREFIX} хорошо прошёл скрининг"
    card.locator("textarea").fill(note)
    with auth_page.expect_response(lambda r: f"/recruit/candidates/{cid}/comments" in r.url and r.request.method == "POST") as cm:
        card.get_by_role("button", name="Добавить комментарий").click()
    assert cm.value.ok, cm.value.text()
    expect(card).to_contain_text(note)
    card.get_by_role("button", name="История", exact=True).click()
    expect(card).to_contain_text("Отклик → Интервью")
    expect(card).to_contain_text(f"Комментарий: {note}")

    card.get_by_role("button", name="Отклонить", exact=True).click()
    sure = modal(auth_page, "Отклонить кандидата?")
    with auth_page.expect_response(lambda r: f"/recruit/candidates/{cid}/reject" in r.url) as rj:
        sure.get_by_role("button", name="Да", exact=True).click()
    assert rj.value.ok, rj.value.text()
    expect(card).to_contain_text("Кандидат отклонён")
    c = hrm_json(hrm_get(auth_page, f"/recruit/candidates/{cid}"))
    assert (c["funnel_status"], c["current_stage_id"]) == ("rejected", stages["Интервью"]), c

    auth_page.reload()
    main(auth_page).locator("tbody tr").filter(has_text=first).locator("td").first.click()
    card = modal(auth_page, f"Кандидат #{cid}")
    expect(card).to_contain_text("Отклонён")  # после перезагрузки (до неё — старый «Активен»)
    expect(card.get_by_role("button", name="Отклонить", exact=True)).to_have_count(0)
    card.get_by_role("button", name="Удалить навсегда").click()
    sure = modal(auth_page, "Удалить кандидата навсегда?")
    expect(sure).to_contain_text(f"{first} Тестовый (#{cid})")
    expect(sure).to_contain_text("этапы, комментарии, файлы, собеседования")
    with auth_page.expect_response(lambda r: f"/recruit/candidates/{cid}" in r.url and r.request.method == "DELETE") as gone:
        sure.get_by_role("button", name="Удалить навсегда").click()
    assert gone.value.ok, gone.value.text()
    expect(main(auth_page).locator("tbody tr").filter(has_text=first)).to_have_count(0)
    assert hrm_get(auth_page, f"/recruit/candidates/{cid}").status == 404


# Кейс: Найм — кандидат → hire → convert-to-employee: сотрудник HRM с испытательным сроком, карточка кандидата «Нанят»
@pytest.mark.writes_data
def test_hire_to_employee(auth_page: Page, job):
    name = f"{QA_PREFIX} Найм {stamp()}"
    c = hrm_json(hrm_post(auth_page, "/recruit/candidates", {"first_name": name, "current_job_posting_id": job["id"],
                                                              "current_stage_id": job["pipeline"]["stages"]["Оффер"]}))
    emp_id = None
    try:
        assert hrm_json(hrm_post(auth_page, f"/recruit/candidates/{c['id']}/hire", {"hire_date": str(TODAY)}))["funnel_status"] == "hired"
        conv = hrm_json(hrm_post(auth_page, f"/recruit/candidates/{c['id']}/convert-to-employee",
                                 {"hire_date": str(TODAY), "auto_onboard": False}))
        emp_id = conv["employee_id"]
        emp = hrm_json(hrm_get(auth_page, f"/employees/{emp_id}"))
        assert (emp["telegram_first_name"] or emp["name"], emp["hire_date"], emp["email"]) == (name, str(TODAY), None), emp
        trial = [t for t in items(hrm_json(hrm_get(auth_page, "/discipline/trials/"))) if t["employee_id"] == emp_id]
        assert [t["end_date"] for t in trial] == [conv["trial_period_end"]], trial
        got = hrm_json(hrm_get(auth_page, f"/recruit/candidates/{c['id']}"))
        assert (got["is_hired"], got["linked_employee_id"]) == (True, emp_id), got

        open_hrm(auth_page, "/admin/recruitment/candidates")
        main(auth_page).locator("tbody tr").filter(has_text=name).locator("td").first.click()
        card = modal(auth_page, f"Кандидат #{c['id']}")
        expect(card).to_contain_text("Нанят")
        expect(card.get_by_role("button", name="Отклонить", exact=True)).to_have_count(0)
    finally:
        if emp_id:
            delete_employee(auth_page, emp_id)


# Кейс: Источники откликов — пустая форма «Новый источник» (IMAP / hh.ru / rabota.ru / SuperJob) не создаётся
def test_import_source_form(auth_page: Page):
    posts = []
    auth_page.on("request", lambda r: posts.append(r.url) if "/import-sources" in r.url and r.method == "POST" else None)
    open_hrm(auth_page, "/admin/recruitment/sources")
    sources = items(hrm_json(hrm_get(auth_page, "/recruit/import-sources/")))
    if not sources:
        expect(main(auth_page)).to_contain_text("Источники ещё не добавлены")
    main(auth_page).get_by_role("button", name=re.compile("^Добавить")).first.click()
    box = modal(auth_page, "Новый источник")
    for label in ("Название *", "Host *", "Username *", "Password *"):
        expect(field(box, label)).to_have_value("")
    expect(field(box, "Port")).to_have_value("993")
    expect(field(box, "Папка (folder)")).to_have_value("INBOX")
    expect(field(box, "Интервал опроса (минут)")).to_have_value("15")
    expect(box.get_by_role("button", name="Создать", exact=True)).to_be_disabled()
    field(box, "Тип источника *").click()
    expect(auth_page.get_by_role("option")).to_have_text(["IMAP (почта)", "hh.ru", "rabota.ru", "SuperJob"])
    auth_page.keyboard.press("Escape")
    box.get_by_role("button", name="Отмена", exact=True).click()
    assert not posts, posts
    assert len(items(hrm_json(hrm_get(auth_page, "/recruit/import-sources/")))) == len(sources)
