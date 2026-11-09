"""Блок 67. HRM — документы (пакеты, генерация пакета из карточки, перегенерация с историей версий, PDF)
и адаптация (Offboarding, задачи «Документ» / «ТМЦ»).

Снято 03.11.2026 в видимом MCP-браузере:
- «Пакеты документов» → «Добавить пакет» → «Новый пакет документов» («Название *») → POST
  /doc-templates/types; шаблон кладётся в пакет полем «Пакет документов» (doc_type_id);
- карточка сотрудника → «Документы»: «Сгенерированные (N)», «Обновить документы», «Сгенерировать» →
  «Сгенерировать документ» — «Один шаблон / Пакет по типу» → «Тип документа» → «Сгенерировать пакет» →
  POST /doc-templates/generate-batch → «Документы добавлены в карточку: N»; шаблон «По месту работы»
  попадает в пакет, только если у сотрудника это место (work_location = код места);
- значок «Перегенерировать документ (и история версий)» → «Перегенерировать документ»: «НОМЕР
  ДОКУМЕНТА» (Оставить текущий / Взять новый — следующий по регистру), «ДАТА ДОКУМЕНТА», «ДАТА ГЕНЕРАЦИИ»,
  таблица «Поле · В документе · В карточке» («изменилось»), «История версий и откат (N)» → «Далее —
  предпросмотр» (regenerate dry_run: «Что изменится … (старый номер останется занят как заменённый)»)
  → «Перегенерировать» → новый номер и текст; /versions — версия «regenerated» с изменёнными полями;
  /generated/<id>/pdf — PDF;
- «Адаптация» → «Создать шаблон» с типом «Offboarding» → «Offboarding -- N задач»; задача «Тип задачи»
  «Документ» требует «Шаблон документа *» — сервер сохраняет её «Обычной» (bord-57).
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (QA_PREFIX, clean_up, create_employee, create_legal_entity, delete_employee, dropdown, field,
                         hrm_delete, hrm_get, hrm_json, hrm_post, hrm_put, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()


def main(page: Page):
    return page.locator("main")


def tab(page: Page, name: str):
    main(page).get_by_role("button", name=name, exact=True).click()


def dialog(page: Page, text: str):
    return page.locator("div.fixed.inset-0:visible").filter(has_text=text).last


@pytest.fixture
def env(auth_page: Page):
    """Своё юрлицо, место работы, журнал «QP», пакет и шаблон в пакете; сотрудник с этим местом работы."""
    le = create_legal_entity(auth_page)
    wp = hrm_json(hrm_post(auth_page, "/org/workplaces", {"name": f"{QA_PREFIX} Место {stamp()}"}))
    wp = next(w for w in hrm_json(hrm_get(auth_page, "/org/workplaces")) if w["id"] == wp["id"])
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Пакет {stamp()}", last_name="Пакетов", work_location=wp["code"])
    journal = hrm_json(hrm_post(auth_page, "/doc-templates/journals", {
        "name": f"{QA_PREFIX} Журнал {stamp()}", "legal_entity_id": le["id"], "prefix": "QP", "separator": "/",
        "start_value": 1, "step": 1, "prefix_position": "start", "is_contract": False}))
    package = hrm_json(hrm_post(auth_page, "/doc-templates/types", {"name": f"{QA_PREFIX} Пакет {stamp()}"}))
    tpl = hrm_json(hrm_post(auth_page, "/doc-templates/", {
        "name": f"{QA_PREFIX} Шаблон пакета {stamp()}", "doc_type": "referral_form", "doc_type_id": package["id"],
        "template_kind": "html", "body_template": "<p>Сотрудник {{employee.full_name}}</p>", "variables": [],
        "numbering_enabled": True, "numbering_journal_id": journal["id"], "applicability": "by_workplace",
        "workplace_ids": [wp["id"]], "position_ids": [], "journal_ids": [], "print_copies": 1}))
    yield {"le": le, "wp": wp, "emp": emp, "journal": journal, "package": package, "tpl": tpl}
    for d in items(hrm_json(hrm_get(auth_page, "/doc-templates/generated", {"employee_id": emp["id"]}))):
        hrm_delete(auth_page, f"/doc-templates/generated/{d['id']}")
    delete_employee(auth_page, emp["id"])
    hrm_delete(auth_page, f"/doc-templates/{tpl['id']}")
    hrm_delete(auth_page, f"/doc-templates/journals/{journal['id']}")
    for p in hrm_json(hrm_get(auth_page, "/doc-templates/types")):
        if p["name"].startswith(QA_PREFIX):
            hrm_delete(auth_page, f"/doc-templates/types/{p['id']}")
    hrm_delete(auth_page, f"/org/workplaces/{wp['id']}")
    clean_up(auth_page)


def open_documents_tab(page: Page, emp: dict):
    open_hrm(page, "/admin/employees")
    main(page).get_by_role("button").filter(has_text=emp["telegram_first_name"]).first.click()
    main(page).get_by_role("button", name="Документы", exact=True).last.click()
    expect(main(page).get_by_text(re.compile("Сгенерированные|Документов пока нет")).first).to_be_visible()


# Кейс: «Добавить пакет» на экране документов → пакет = API → удалить
@pytest.mark.writes_data
def test_package_form(auth_page: Page):
    name = f"{QA_PREFIX} Пакет форма {stamp()}"
    open_hrm(auth_page, "/admin/doc-templates")
    tab(auth_page, "Пакеты документов")
    main(auth_page).get_by_role("button", name="Добавить пакет").click()
    box = dialog(auth_page, "Новый пакет документов")
    with auth_page.expect_response(lambda r: r.url.endswith("/doc-templates/types") and r.request.method == "POST") as resp:
        field(box, "Название *").fill(name)
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    pkg = resp.value.json()
    try:
        assert pkg["name"] == name and pkg["id"] in [p["id"] for p in hrm_json(hrm_get(auth_page, "/doc-templates/types"))]
        expect(main(auth_page)).to_contain_text(name)
    finally:
        hrm_delete(auth_page, f"/doc-templates/types/{pkg['id']}")


# Кейс: «Сгенерировать» → «Пакет по типу» в карточке сотрудника — документ шаблона пакета с номером журнала
@pytest.mark.writes_data
def test_generate_package_from_card(auth_page: Page, env):
    open_documents_tab(auth_page, env["emp"])
    expect(main(auth_page)).to_contain_text("Документов пока нет")  # пустая вкладка — своя кнопка
    main(auth_page).get_by_role("button", name="Сгенерировать документ", exact=True).click()
    box = dialog(auth_page, "Сгенерировать документ")
    expect(box).to_contain_text(f"Для: {env['emp']['telegram_first_name']}")
    box.get_by_role("button", name="Пакет по типу", exact=True).click()
    dropdown(auth_page, box, "Тип документа", env["package"]["name"])
    with auth_page.expect_response(lambda r: r.url.endswith("/doc-templates/generate-batch")) as gen:
        box.get_by_role("button", name="Сгенерировать пакет", exact=True).click()
    assert gen.value.ok, gen.value.text()
    assert gen.value.request.post_data_json == {"doc_type_id": env["package"]["id"], "employee_id": env["emp"]["id"]}
    [doc] = gen.value.json()
    assert (doc["template_id"], doc["doc_number"]) == (env["tpl"]["id"], "QP/1"), doc
    expect(main(auth_page)).to_contain_text("Документы добавлены в карточку: 1")
    row = main(auth_page).locator("tr").filter(has_text="QP/1")
    expect(row).to_contain_text(env["tpl"]["name"])
    expect(row).to_contain_text("Сгенерирован")
    # PDF у свежего документа создаётся при первом «Скачать PDF» (POST …/pdf), потом отдаётся GET
    with auth_page.expect_download(timeout=60_000) as dl:
        row.get_by_role("button", name="Скачать PDF").click()
    path = dl.value.path()
    assert dl.value.suggested_filename.endswith(".pdf") and open(path, "rb").read(5) == b"%PDF-", dl.value.suggested_filename


# Кейс: Перегенерация после смены фамилии — сравнение полей, новый номер QP/2, предпросмотр, версия в истории
@pytest.mark.writes_data
def test_regenerate_with_new_number(auth_page: Page, env):
    emp = env["emp"]
    [doc] = hrm_json(hrm_post(auth_page, "/doc-templates/generate-batch", {"doc_type_id": env["package"]["id"], "employee_id": emp["id"]}))
    assert doc["doc_number"] == "QP/1", doc
    hrm_json(hrm_put(auth_page, f"/employees/{emp['id']}", {"last_name": "Новиков"}))
    old, new = f"Пакетов {emp['telegram_first_name']}", f"Новиков {emp['telegram_first_name']}"
    open_documents_tab(auth_page, emp)
    main(auth_page).locator("tr").filter(has_text="QP/1").get_by_role(
        "button", name="Перегенерировать документ (и история версий)").click()
    box = dialog(auth_page, "Перегенерировать документ")
    expect(box).to_contain_text("Оставить текущий номер — QP/1")
    expect(box).to_contain_text("Взять новый номер (следующий по регистру: QP/2)")
    diff = box.locator("tr").filter(has_text="ФИО сотрудника")
    expect(diff).to_contain_text("изменилось")
    expect(diff).to_contain_text(old)
    expect(diff).to_contain_text(new)
    box.locator("label").filter(has_text="Взять новый номер").locator("input").check()
    with auth_page.expect_response(lambda r: f"/generated/{doc['id']}/regenerate" in r.url) as dry:
        box.get_by_role("button", name="Далее — предпросмотр").click()
    assert dry.value.request.post_data_json["dry_run"] is True and dry.value.request.post_data_json["number_mode"] == "new"
    expect(box).to_contain_text("Что изменится")
    expect(box).to_contain_text("(старый номер останется занят как заменённый)")
    expect(box).to_contain_text(f"Сотрудник {new}")
    with auth_page.expect_response(lambda r: f"/generated/{doc['id']}/regenerate" in r.url) as real:
        box.get_by_role("button", name="Перегенерировать", exact=True).click()
    assert real.value.ok and real.value.request.post_data_json["dry_run"] is False, real.value.text()
    got = real.value.json()
    assert (got["doc_number"], got["body_rendered"]) == ("QP/2", f"<p>Сотрудник {new}</p>"), got
    expect(main(auth_page).locator("tr").filter(has_text="QP/2")).to_contain_text(new)
    versions = hrm_json(hrm_get(auth_page, f"/doc-templates/generated/{doc['id']}/versions"))
    assert [(v["version_no"], v["operation"]) for v in versions] == [(1, "regenerated"), (0, "created")], versions
    assert versions[0]["changes"][0]["old"] == old and versions[0]["changes"][0]["new"] == new, versions[0]


# ───────────────────────── адаптация ─────────────────────────

# Кейс: Шаблон «Offboarding» — тип в форме, карточка «Offboarding -- 0 задач»
@pytest.mark.writes_data
def test_offboarding_template(auth_page: Page):
    name = f"{QA_PREFIX} Offboarding {stamp()}"
    open_hrm(auth_page, "/admin/onboarding")
    tab(auth_page, "Шаблоны")
    main(auth_page).get_by_role("button", name="Создать шаблон").click()
    box = dialog(auth_page, "Новый шаблон")
    field(box, "Название *").fill(name)
    dropdown(auth_page, box, "Тип", "Offboarding")
    expect(field(box, "Триггер")).to_contain_text("Вручную")
    with auth_page.expect_response(lambda r: r.url.endswith("/onboard/templates") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    tpl_id = resp.value.json()["id"]
    try:
        assert resp.value.request.post_data_json["workflow_type"] == "offboarding"
        assert hrm_json(hrm_get(auth_page, f"/onboard/templates/{tpl_id}"))["workflow_type"] == "offboarding"
        expect(main(auth_page).locator("div").filter(has=auth_page.get_by_role("heading", name=name, exact=True)).last
               ).to_contain_text("Offboarding · 0 задач")  # до 08.11 — « -- »
    finally:
        hrm_delete(auth_page, f"/onboard/templates/{tpl_id}")


# Кейс: Задача адаптации типа «Документ» сохраняет тип и шаблон документа
@pytest.mark.writes_data
def test_onboarding_document_task(auth_page: Page, env):
    tpl = hrm_json(hrm_post(auth_page, "/onboard/templates", {"name": f"{QA_PREFIX} Адаптация документ {stamp()}",
                                                              "trigger_event": "manual"}))
    try:
        task = hrm_json(hrm_post(auth_page, f"/onboard/templates/{tpl['id']}/tasks", {
            "title": f"{QA_PREFIX} Подписать направление", "task_type": "document", "doc_template_id": env["tpl"]["id"],
            "category": "documents"}))
        saved = next(t for t in hrm_json(hrm_get(auth_page, f"/onboard/templates/{tpl['id']}"))["tasks"] if t["id"] == task["id"])
        assert (saved["task_type"], saved["doc_template_id"]) == ("document", env["tpl"]["id"]), saved
    finally:
        for t in hrm_json(hrm_get(auth_page, f"/onboard/templates/{tpl['id']}"))["tasks"]:
            hrm_delete(auth_page, f"/onboard/templates/tasks/{t['id']}")
        hrm_delete(auth_page, f"/onboard/templates/{tpl['id']}")
