"""Блок 62. HRM — документы (/admin/doc-templates): журналы нумерации, шаблоны, генерация, реестр.

Снято 03.11.2026 в видимом MCP-браузере:
- вкладки «Шаблоны / Генерация / Документы / Пакеты документов / Журналы нумерации»; системные шаблоны
  «Акт выдачи / приёма ТМЦ» и их журналы («СИСТЕМНЫЙ») не трогаем;
- «Журналы нумерации» → «Добавить журнал» → «Новый журнал нумерации»: «Название *», «Юридическое лицо *»,
  «Префикс», «Разделитель» (/), «Начальное значение» (1), «Шаг» (1), «Положение префикса» (В начале / В
  конце), «Контрактный регистр», «ПРЕВЬЮ НОМЕРА» («QA/100» ↔ «100/QA») → POST /doc-templates/journals;
  колонки «Название, Юр. лицо, Префикс, Положение, Разделитель, Начало, Шаг, Текущее, Действия».
  Тест ведёт нумерацию ТОЛЬКО в своём журнале на своём юрлице — номера чужих документов не задеваются;
- «Новый шаблон»: «Название *», «Тип документа» (Контракт, …, Направление, …), «Пакет документов»,
  «Кол-во копий», «Область применения» — «Универсальный» кладёт шаблон в пакет ЛЮБОГО сотрудника, поэтому
  тест берёт «По месту работы» со своим местом работы (без места «Создать» неактивна: «Выберите хотя бы
  одно место работы…»); редактор tiptap, «Вставить переменную» → {{employee.full_name}} …;
  «Авто-нумерация документов» → «Журнал нумерации» («<название> (<префикс>)») → POST /doc-templates/;
- «Генерация» — мастер «1 Шаблон · 2 Сотрудник · 3 Переменные · 4 Предпросмотр · 5 Результат»: переменные
  заполняются из профиля (ФИО = «Фамилия Имя»), предпросмотр с подставленным текстом, «Сгенерировать» →
  POST /doc-templates/<id>/generate → «Документ создан · Номер: QA/100 · Статус: Сгенерирован»;
- «Документы» (реестр): «Номер, Название, Тип, Статус, Дата, Действия»; значки «Просмотр, Скачать PDF,
  Печать, Перегенерировать, Отменить (номер останется занят), Удалить»; «Отменить» → «Отменить документ?
  … Номер останется занят … снять отмену нельзя» → «Отменить документ» (PUT …/status?status=cancelled) →
  «Отменён»; «Удалить» → «Удалить документ? … Номер будет освобождён …» → DELETE (204); следующий номер —
  наименьший свободный (удалённый выдаётся снова).
Своё (юрлицо, место работы, журнал, шаблон, документы, сотрудник) удаляется после теста.
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (QA_PREFIX, clean_up, create_employee, create_legal_entity, delete_employee, dropdown, field,
                         hrm_delete, hrm_get, hrm_json, hrm_post, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth


def main(page: Page):
    return page.locator("main")


def tab(page: Page, name: str):
    main(page).get_by_role("button", name=name, exact=True).click()


def modal(page: Page, heading: str):
    return page.locator("div.fixed.inset-0:visible").filter(
        has=page.get_by_role("heading", name=heading, exact=True)).last


def remove_own(page: Page, emp_id: int | None = None):
    if emp_id:
        for d in items(hrm_json(hrm_get(page, "/doc-templates/generated", {"employee_id": emp_id}))):
            hrm_delete(page, f"/doc-templates/generated/{d['id']}")
    for t in items(hrm_json(hrm_get(page, "/doc-templates/"))):
        if t["name"].startswith(QA_PREFIX):
            hrm_delete(page, f"/doc-templates/{t['id']}")
    for j in hrm_json(hrm_get(page, "/doc-templates/journals")):
        if j["name"].startswith(QA_PREFIX) and not j.get("is_system"):
            hrm_delete(page, f"/doc-templates/journals/{j['id']}")
    for w in hrm_json(hrm_get(page, "/org/workplaces")):
        if w["name"].startswith(QA_PREFIX):
            hrm_delete(page, f"/org/workplaces/{w['id']}")


@pytest.fixture
def env(auth_page: Page):
    """Своё юрлицо, сотрудник с фамилией и место работы — {le, emp, wp}."""
    le = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Документ {stamp()}", last_name="Тестов")
    wp = hrm_json(hrm_post(auth_page, "/org/workplaces", {"name": f"{QA_PREFIX} Место {stamp()}"}))
    yield {"le": le, "emp": emp, "wp": wp}
    remove_own(auth_page, emp["id"])
    delete_employee(auth_page, emp["id"])
    clean_up(auth_page)


@pytest.fixture
def journal(auth_page: Page, env):
    return hrm_json(hrm_post(auth_page, "/doc-templates/journals", {
        "name": f"{QA_PREFIX} Журнал {stamp()}", "legal_entity_id": env["le"]["id"], "prefix": "QA",
        "separator": "/", "start_value": 100, "step": 1, "prefix_position": "start", "is_contract": False}))


@pytest.fixture
def template(auth_page: Page, env, journal):
    """Шаблон «Направление» только для своего места работы, с нумерацией по своему журналу."""
    return hrm_json(hrm_post(auth_page, "/doc-templates/", {
        "name": f"{QA_PREFIX} Направление {stamp()}", "doc_type": "referral_form", "template_kind": "html",
        "body_template": "<p>Направляется сотрудник {{employee.full_name}}</p>", "variables": [],
        "numbering_enabled": True, "numbering_journal_id": journal["id"], "applicability": "by_workplace",
        "workplace_ids": [env["wp"]["id"]], "position_ids": [], "journal_ids": [], "print_copies": 1}))


def generate(page: Page, template_id: int, emp_id: int) -> dict:
    return hrm_json(hrm_post(page, f"/doc-templates/{template_id}/generate", {"employee_id": emp_id}))


# Кейс: Журнал нумерации — превью номера «QA/100» ↔ «100/QA», форма = API, строка в списке
@pytest.mark.writes_data
def test_numbering_journal_form(auth_page: Page, env):
    name = f"{QA_PREFIX} Журнал {stamp()}"
    open_hrm(auth_page, "/admin/doc-templates")
    tab(auth_page, "Журналы нумерации")
    main(auth_page).get_by_role("button", name="Добавить журнал").click()
    box = modal(auth_page, "Новый журнал нумерации")
    field(box, "Название *").fill(name)
    dropdown(auth_page, box, "Юридическое лицо *", env["le"]["name"])
    field(box, "Префикс").fill("QA")
    expect(field(box, "Разделитель")).to_have_value("/")
    field(box, "Начальное значение").fill("100")
    expect(field(box, "Шаг")).to_have_value("1")
    preview = box.locator("xpath=.//*[normalize-space(.)='Превью номера']/following-sibling::*[1]")  # капс — CSS
    expect(preview).to_have_text("QA/100")
    box.get_by_role("button", name="В конце", exact=True).click()
    expect(preview).to_have_text("100/QA")
    expect(box).to_contain_text("Изменение положения не пересчитывает уже выпущенные номера.")
    box.get_by_role("button", name="В начале", exact=True).click()
    expect(preview).to_have_text("QA/100")
    with auth_page.expect_response(lambda r: r.url.endswith("/doc-templates/journals") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    j = next(x for x in hrm_json(hrm_get(auth_page, "/doc-templates/journals")) if x["id"] == resp.value.json()["id"])
    assert (j["name"], j["legal_entity_id"], j["prefix"], j["separator"], j["start_value"], j["step"], j["prefix_position"],
            j["current_value"], j["is_contract"], j["is_system"]) == \
        (name, env["le"]["id"], "QA", "/", 100, 1, "start", 0, False, False), j
    row = main(auth_page).locator("tbody tr").filter(has_text=name)
    for text in (env["le"]["name"], "QA", "в начале", "/", "100"):
        expect(row).to_contain_text(text)
    expect(row).not_to_contain_text("СИСТЕМНЫЙ")


# Кейс: Шаблон документа — «По месту работы» без места не создаётся; переменная из каталога, авто-нумерация своим журналом = API
@pytest.mark.writes_data
def test_template_form(auth_page: Page, env, journal):
    name = f"{QA_PREFIX} Направление {stamp()}"
    open_hrm(auth_page, "/admin/doc-templates")
    main(auth_page).get_by_role("button", name="Новый шаблон").click()
    box = modal(auth_page, "Новый шаблон")
    field(box, "Название *").fill(name)
    expect(field(box, "Тип документа")).to_contain_text("Контракт")
    dropdown(auth_page, box, "Тип документа", "Направление")
    box.get_by_role("button", name="По месту работы", exact=True).click()
    expect(box).to_contain_text("Выберите хотя бы одно место работы")
    expect(box.get_by_role("button", name="Создать", exact=True)).to_be_disabled()
    places = box.get_by_role("combobox").filter(has_text="Выберите места работы")
    places.click()
    auth_page.get_by_role("option", name=env["wp"]["name"]).click()
    auth_page.keyboard.press("Escape")
    expect(box).to_be_visible()
    expect(box.get_by_role("button", name="Создать", exact=True)).to_be_enabled()

    editor = box.locator("[contenteditable=true]")
    editor.click()
    auth_page.keyboard.type("Направляется сотрудник ")
    box.get_by_role("button", name="Вставить переменную").click()
    box.get_by_role("button").filter(has_text="{{employee.full_name}}").first.click()
    expect(editor).to_contain_text("Направляется сотрудник {{employee.full_name}}")

    box.get_by_role("checkbox", name="Авто-нумерация документов").check()
    dropdown(auth_page, box, "Журнал нумерации", f"{journal['name']} (QA)")
    expect(box).to_contain_text("Нумерация будет вестись через выбранный журнал.")
    with auth_page.expect_response(lambda r: r.url.endswith("/doc-templates/") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    t = hrm_json(hrm_get(auth_page, f"/doc-templates/{resp.value.json()['id']}"))
    assert (t["name"], t["doc_type"], t["applicability"], t["workplace_ids"], t["numbering_enabled"],
            t["numbering_journal_id"]) == (name, "referral_form", "by_workplace", [env["wp"]["id"]], True, journal["id"]), t
    assert "Направляется сотрудник {{employee.full_name}}" in t["body_template"], t["body_template"]
    expect(main(auth_page).get_by_role("button").filter(has_text=name)).to_be_visible()


# Кейс: Генерация по сотруднику — переменные из профиля, номер из своего журнала; «Отменить» держит номер, «Удалить» освобождает
@pytest.mark.writes_data
def test_generate_cancel_delete(auth_page: Page, env, template):
    emp = env["emp"]
    full = f"Тестов {emp['telegram_first_name']}"
    open_hrm(auth_page, "/admin/doc-templates")
    tab(auth_page, "Генерация")
    wizard = main(auth_page)
    for step in ("Шаблон", "Сотрудник", "Переменные", "Предпросмотр", "Результат"):
        expect(wizard).to_contain_text(step)
    wizard.get_by_role("button").filter(has_text=template["name"]).click()
    wizard.get_by_role("button", name="Далее", exact=True).click()
    expect(wizard).to_contain_text("Выберите сотрудника")
    wizard.get_by_role("button").filter(has_text=emp["telegram_first_name"]).click()
    wizard.get_by_role("button", name="Далее", exact=True).click()
    expect(wizard).to_contain_text("Дополнительные переменные")
    # переменные подтягиваются из профиля не сразу — без этого предпросмотр покажет сырой {{employee.full_name}}
    auth_page.wait_for_function("v => [...document.querySelectorAll('main input')].some(i => i.value === v)", arg=full)
    wizard.get_by_role("button", name="Далее", exact=True).click()
    expect(wizard).to_contain_text("Предпросмотр документа")
    expect(wizard).to_contain_text(f"Направляется сотрудник {full}")
    with auth_page.expect_response(lambda r: f"/doc-templates/{template['id']}/generate" in r.url) as gen:
        wizard.get_by_role("button", name="Сгенерировать", exact=True).click()
    assert gen.value.ok, gen.value.text()
    doc = gen.value.json()
    assert (doc["doc_number"], doc["status"], doc["employee_id"]) == ("QA/100", "generated", emp["id"]), doc
    assert doc["body_rendered"] == f"<p>Направляется сотрудник {full}</p>", doc["body_rendered"]
    for text in ("Документ создан", "Номер: QA/100", "Тип: Направление", "Статус: Сгенерирован"):
        expect(wizard).to_contain_text(text)

    tab(auth_page, "Документы")
    row = main(auth_page).locator("tbody tr").filter(has_text="QA/100")
    expect(row).to_contain_text(f"{template['name']} — {full}")
    expect(row).to_contain_text("Сгенерирован")
    row.get_by_role("button", name="Отменить (номер останется занят)").click()
    sure = modal(auth_page, "Отменить документ?")
    expect(sure).to_contain_text("Документ QA/100 будет отменён. Номер останется занят в журнале нумерации.")
    expect(sure).to_contain_text("снять отмену нельзя")
    with auth_page.expect_response(lambda r: f"/generated/{doc['id']}/status" in r.url) as st:
        sure.get_by_role("button", name="Отменить документ", exact=True).click()
    assert st.value.ok and st.value.json()["status"] == "cancelled", st.value.text()
    expect(row).to_contain_text("Отменён")
    expect(row.get_by_role("button", name="Отменить (номер останется занят)")).to_have_count(0)

    second = generate(auth_page, template["id"], emp["id"])
    assert second["doc_number"] == "QA/101", second  # отменённый QA/100 занят
    auth_page.reload()
    tab(auth_page, "Документы")
    row2 = main(auth_page).locator("tbody tr").filter(has_text="QA/101")
    row2.get_by_role("button", name="Удалить", exact=True).click()
    sure = modal(auth_page, "Удалить документ?")
    expect(sure).to_contain_text("Документ QA/101 будет удалён. Номер будет освобождён")
    with auth_page.expect_response(lambda r: f"/generated/{second['id']}" in r.url and r.request.method == "DELETE") as gone:
        sure.get_by_role("button", name="Удалить", exact=True).click()
    assert gone.value.status == 204, gone.value.text()
    expect(row2).to_have_count(0)
    assert generate(auth_page, template["id"], emp["id"])["doc_number"] == "QA/101"  # удалённый номер выдан снова
    numbers = sorted((d["doc_number"], d["status"]) for d in items(hrm_json(
        hrm_get(auth_page, "/doc-templates/generated", {"employee_id": emp["id"]}))))
    assert numbers == [("QA/100", "cancelled"), ("QA/101", "generated")], numbers
