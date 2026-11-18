"""Блок 53. HRM — сотрудники (/admin/employees): список, поиск, форма, карточка, увольнение,
семья, перевод, удаление, выгрузка Excel.

Снято 02.11.2026 (в видимом MCP-браузере):
- список = GET /employees?status_filter=active|inactive|all&limit=1000, вкладки «Активные /
  Неактивные / Все», поиск «Поиск по имени, IDNP…», фильтры «Все отделы / должности / юрлица»;
  в шапке «Сотрудники N», «Скачать Excel», «Добавить»;
- «Добавить» → боковая форма «Добавить сотрудника»: «Имя *», «Фамилия», …, «IDNP», «Юридическое
  лицо *»; IDNP не из 13 цифр — подсказка «IDNP должен содержать ровно 13 цифр» и «Создать»
  неактивна → POST /employees;
- карточка справа: «Редактировать», «Удалить сотрудника», бейдж статуса, вкладки «Основное,
  Семья, Файлы, Документы, Отсутствия, История сотрудника, Совместительство, ТМЦ, Нарушения,
  Обучение, Навыки, 1-on-1»; «Основное» — тип занятости, роль, дата найма, Nr. CIM, «ID
  сотрудника #N», юрлицо, IDNP;
- бейдж «Активный» → «Уволить» → окно «Деактивировать сотрудника?» (дата увольнения, причина) →
  POST /employees/<id>/deactivate {termination_date} → «Уволен ДД.ММ.ГГГГ»; бейдж «Уволен» →
  «Вернуть в работу» → POST /employees/<id>/restore (без окна);
- «Семья» → «Добавить члена семьи» (встроенная форма: «Степень родства *» — «Ребёнок», «ФИО *»,
  «IDNP», «Дата рождения») → счётчики «Кол-во несовершеннолетних детей / Кол-во детей»;
- «Скачать Excel» → выбор колонок «Колонки (20/20)» → «Скачать» (xlsx, строка на сотрудника);
- «Удалить сотрудника» → «Удалить сотрудника? … Учётка в SSO будет полностью удалена…»,
  галочка «Удалить все связанные объекты» → DELETE /employees/<id>?cascade=true.
Все сотрудники тестов — свои («QA Autotest», без email) на своём юрлице, удаляются каскадом.
"""
import datetime as dt
import io
import re

import openpyxl
import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (QA_PREFIX, clean_up, create_employee, create_legal_entity, date_field, delete_employee,
                         dropdown, field, header_button, hrm_delete, hrm_get, hrm_json, hrm_post, items, open_hrm,
                         person_name, screen_title, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()
TABS = ["Основное", "Семья", "Файлы", "Документы", "Отсутствия", "История сотрудника", "Совместительство", "ТМЦ",
        "Нарушения", "Обучение", "Навыки", "1-on-1"]


def main(page: Page):
    return page.locator("main")


def full_name(emp: dict) -> str:
    return person_name(emp)  # с 17.11 «Фамилия Имя» (порядок ФИО организации)


def list_row(page: Page, name: str):
    """Строка списка: кнопка с именем (рядом — чекбокс и бейдж статуса)."""
    return main(page).get_by_role("button").filter(has_text=name).first


def list_line(page: Page, name: str):
    return main(page).locator("div").filter(has=page.locator("input[type=checkbox]")).filter(has_text=name).last


def card(page: Page):
    """Карточка справа — блок с кнопкой «Редактировать»."""
    return main(page).locator("div").filter(has=page.get_by_role("button", name="Редактировать")).filter(
        has=page.get_by_role("button", name="Основное", exact=True)).last


def open_card(page: Page, emp: dict):
    open_hrm(page, "/admin/employees")
    list_row(page, full_name(emp)).click()
    expect(card(page)).to_contain_text(f"#{emp['id']}")
    return card(page)


@pytest.fixture
def le(auth_page: Page):
    entity = create_legal_entity(auth_page)
    yield entity
    clean_up(auth_page)


@pytest.fixture
def person(auth_page: Page, le):
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} {stamp()}", last_name="Тестов", national_id="2009001234567")
    yield emp
    delete_employee(auth_page, emp["id"])


# ───────────────────────── список ─────────────────────────

# Кейс: Список = API — вкладки «Активные / Неактивные / Все» и счётчик в шапке
def test_list_matches_api(auth_page: Page):
    with auth_page.expect_response(lambda r: "/api/hrm/employees?" in r.url and "status_filter=active" in r.url) as resp:
        open_hrm(auth_page, "/admin/employees")
    active = items(resp.value.json())
    expect(screen_title(auth_page)).to_have_text(re.compile(rf"Сотрудники\s*{len(active)}$"))
    for emp in active:
        expect(list_row(auth_page, full_name(emp) or emp["name"])).to_be_visible()
    for tab, status in (("Неактивные", "inactive"), ("Все", "all")):
        with auth_page.expect_response(lambda r: "/api/hrm/employees?" in r.url and f"status_filter={status}" in r.url) as tr:
            main(auth_page).get_by_role("button", name=tab, exact=True).click()
        n = len(items(tr.value.json()))  # чекбоксы строк + «Выбрать всех»; пустой список — без чекбоксов
        expect(main(auth_page).locator("input[type=checkbox]")).to_have_count(n + 1 if n else 0)


# Кейс: Поиск по имени и по IDNP
def test_search(auth_page: Page, person):
    open_hrm(auth_page, "/admin/employees")
    search = main(auth_page).get_by_placeholder("Поиск по имени, IDNP…")
    for query in (person["telegram_first_name"], "2009001234567"):
        search.fill(query)
        expect(list_row(auth_page, full_name(person))).to_be_visible()
        expect(list_row(auth_page, "maxim.turcan1")).to_have_count(0)
    search.fill(f"{QA_PREFIX} нет такого {stamp()}")
    expect(list_row(auth_page, full_name(person))).to_have_count(0)


# ───────────────────────── форма и карточка ─────────────────────────

# Кейс: «Добавить сотрудника» — IDNP ровно 13 цифр, создание через форму
@pytest.mark.writes_data
def test_create_via_form(auth_page: Page, le):
    first = f"{QA_PREFIX} Форма {stamp()}"
    open_hrm(auth_page, "/admin/employees")
    header_button(auth_page, "Добавить").click()
    form = auth_page.locator("div").filter(has=auth_page.get_by_role("heading", name="Добавить сотрудника")).filter(
        has=auth_page.get_by_role("button", name="Создать", exact=True)).last
    field(form, "Имя *").fill(first)
    field(form, "Фамилия").fill("Формов")
    dropdown(auth_page, form, "Юридическое лицо *", le["name"])
    create = form.get_by_role("button", name="Создать", exact=True)
    field(form, "IDNP").fill("12345")
    expect(form).to_contain_text("IDNP должен содержать ровно 13 цифр")
    expect(create).to_be_disabled()
    field(form, "IDNP").fill("2009001234568")
    expect(create).to_be_enabled()
    with auth_page.expect_response(lambda r: r.url.endswith("/api/hrm/employees") and r.request.method == "POST") as resp:
        create.click()
    assert resp.value.ok, resp.value.text()
    emp = resp.value.json()
    try:
        assert (emp["telegram_first_name"], emp["last_name"], emp["legal_entity_id"], emp["national_id"]) == \
            (first, "Формов", le["id"], "2009001234568"), emp
        assert not emp.get("email"), emp  # без email — без приглашения
        assert emp["hire_date"] == str(TODAY), emp
        expect(list_row(auth_page, person_name({"telegram_first_name": first, "last_name": "Формов"}))).to_be_visible()
    finally:
        delete_employee(auth_page, emp["id"])


# Кейс: Карточка — «Основное» и вкладки = API
def test_card_matches_api(auth_page: Page, person, le):
    box = open_card(auth_page, person)
    for tab in TABS:
        expect(box.get_by_role("button", name=tab, exact=True).last).to_be_visible()
    expect(box).to_contain_text(full_name(person))
    expect(box).to_contain_text(le["name"])
    expect(box).to_contain_text("2009001234567")
    expect(box).to_contain_text(f"{TODAY:%d.%m.%Y}")
    expect(box.get_by_role("button", name="Активный")).to_be_visible()


# Кейс: «Редактировать» — фамилия сохраняется
@pytest.mark.writes_data
def test_edit(auth_page: Page, person):
    box = open_card(auth_page, person)
    box.get_by_role("button", name="Редактировать").click()
    form = auth_page.locator("div.fixed:visible").last
    expect(form).to_contain_text(f"Редактировать: {full_name(person)}")
    field(form, "Фамилия").fill("Изменённый")
    with auth_page.expect_response(lambda r: f"/api/hrm/employees/{person['id']}" in r.url and r.request.method == "PUT") as resp:
        form.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    assert hrm_json(hrm_get(auth_page, f"/employees/{person['id']}"))["last_name"] == "Изменённый"
    expect(card(auth_page)).to_contain_text(person_name({**person, "last_name": "Изменённый"}))


# Кейс: Уволить и вернуть в работу
@pytest.mark.writes_data
def test_dismiss_and_restore(auth_page: Page, person):
    box = open_card(auth_page, person)
    box.get_by_role("button", name="Активный").click()
    auth_page.get_by_text("Уволить", exact=True).last.click()
    dlg = auth_page.locator("div.fixed:visible").filter(has_text="Деактивировать сотрудника?").last
    expect(dlg).to_contain_text(f"Сотрудник {full_name(person)} будет помечен как уволенный")
    expect(date_field(dlg, "Дата увольнения")).to_have_value(str(TODAY))
    with auth_page.expect_response(lambda r: f"/employees/{person['id']}/deactivate" in r.url) as resp:
        dlg.get_by_role("button", name="Деактивировать", exact=True).click()
    assert resp.value.ok and resp.value.request.post_data_json["termination_date"] == str(TODAY), resp.value.text()
    expect(card(auth_page)).to_contain_text(f"Уволен {TODAY:%d.%m.%Y}")
    got = hrm_json(hrm_get(auth_page, f"/employees/{person['id']}"))
    assert (got["is_active"], got["termination_date"]) == (False, str(TODAY)), got

    # после перезагрузки — во вкладке «Неактивные», в «Активных» нет
    open_hrm(auth_page, "/admin/employees")
    expect(list_row(auth_page, full_name(person))).to_have_count(0)
    main(auth_page).get_by_role("button", name="Неактивные", exact=True).click()
    list_row(auth_page, full_name(person)).click()

    card(auth_page).get_by_role("button", name="Уволен", exact=True).click()
    with auth_page.expect_response(lambda r: f"/employees/{person['id']}/restore" in r.url) as back:
        auth_page.get_by_text("Вернуть в работу", exact=True).last.click()
    assert back.value.ok, back.value.text()
    expect(card(auth_page).get_by_role("button", name="Активный")).to_be_visible()
    assert hrm_json(hrm_get(auth_page, f"/employees/{person['id']}"))["is_active"] is True


# Кейс: Семья — ребёнок до 18 лет попадает в счётчики
@pytest.mark.writes_data
def test_family_child(auth_page: Page, person):
    box = open_card(auth_page, person)
    box.get_by_role("button", name="Семья", exact=True).last.click()
    expect(box).to_contain_text("Кол-во несовершеннолетних детей: 0")
    box.get_by_role("button", name="Добавить члена семьи").click()
    expect(field(box, "Степень родства *")).to_contain_text("Ребёнок")
    field(box, "ФИО *").fill(f"{QA_PREFIX} Ребёнок")
    date_field(box, "Дата рождения").fill(f"{TODAY.year - 10}-01-15")
    # под семьёй с 03.11 — «Налоговые льготы» со своей «Сохранить»: берём кнопку формы члена семьи
    form = box.locator("div").filter(has=auth_page.get_by_text("ФИО *", exact=True)).filter(
        has=auth_page.get_by_role("button", name="Сохранить", exact=True)).last
    with auth_page.expect_response(lambda r: f"/employees/{person['id']}/family" in r.url and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    expect(box).to_contain_text("Кол-во несовершеннолетних детей: 1")
    expect(box).to_contain_text("Кол-во детей: 1")
    expect(box).to_contain_text(f"{QA_PREFIX} Ребёнок")
    family = items(hrm_json(hrm_get(auth_page, f"/employees/{person['id']}/family")))
    assert [m["full_name"] for m in family] == [f"{QA_PREFIX} Ребёнок"], family


# Кейс: «Удалить сотрудника» — окно с последствиями, каскад, сотрудника нет и в API
@pytest.mark.writes_data
def test_delete_via_ui(auth_page: Page, le):
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Удаляемый {stamp()}")
    try:
        box = open_card(auth_page, emp)
        box.get_by_role("button", name="Удалить сотрудника").click()
        dlg = auth_page.locator("div.fixed:visible").filter(has_text="Удалить сотрудника?").last
        expect(dlg).to_contain_text(f"Сотрудник {emp['telegram_first_name']} будет удалён без возможности восстановления")
        expect(dlg).to_contain_text("Это не «Деактивировать». Действие необратимо.")
        expect(dlg).to_contain_text("Учётка в SSO будет полностью удалена")
        dlg.get_by_text("Удалить все связанные объекты").click()
        with auth_page.expect_response(lambda r: f"/api/hrm/employees/{emp['id']}" in r.url and r.request.method == "DELETE") as resp:
            dlg.get_by_role("button", name="Удалить сотрудника", exact=True).click()
        assert resp.value.ok and "cascade=true" in resp.value.url, resp.value.url
        expect(list_row(auth_page, emp["telegram_first_name"])).to_have_count(0)
        assert hrm_get(auth_page, f"/employees/{emp['id']}").status == 404
    finally:
        if hrm_get(auth_page, f"/employees/{emp['id']}").ok:
            delete_employee(auth_page, emp["id"])


# Кейс: «Скачать Excel» — файл .xlsx со списком активных
def test_excel_export(auth_page: Page, person):
    active = items(hrm_json(hrm_get(auth_page, "/employees", {"status_filter": "active", "limit": 1000})))
    open_hrm(auth_page, "/admin/employees")
    # кнопка открывает выбор колонок «Колонки (20/20)» (вынесен в конец страницы) с «Скачать» внизу;
    # клик сразу после загрузки экрана иногда теряется (гидрация) — повторяем, пока окно не откроется
    columns = auth_page.get_by_text(re.compile(r"^Колонки \(\d+/\d+\)$"))
    for _ in range(3):
        header_button(auth_page, "Скачать Excel").click()
        try:
            expect(columns).to_be_visible(timeout=3_000)
            break
        except AssertionError:
            continue
    expect(columns).to_be_visible()
    expect(auth_page.get_by_role("checkbox", name="Фамилия Имя")).to_be_checked()
    with auth_page.expect_download() as dl:
        with auth_page.expect_response(lambda r: "/api/hrm/employees/export" in r.url) as resp:
            auth_page.get_by_role("button", name="Скачать", exact=True).click()
    assert "status_filter=active" in resp.value.url, resp.value.url
    download = dl.value
    assert download.suggested_filename == f"employees_active_{TODAY}.xlsx", download.suggested_filename
    wb = openpyxl.load_workbook(io.BytesIO(open(download.path(), "rb").read()), read_only=True)
    rows = [r for r in wb.active.iter_rows(values_only=True) if any(r)]
    text = "\n".join(" ".join(str(c) for c in r if c is not None) for r in rows)
    assert person["telegram_first_name"] in text, text[:500]
    assert len(rows) - 1 == len(active), (len(rows), len(active))


# Кейс: Групповой «Перевести» — сотрудник уходит в свой отдел
@pytest.mark.writes_data
def test_bulk_transfer(auth_page: Page, person, le):
    dep = hrm_json(hrm_post(auth_page, "/org/departments", {"name": f"{QA_PREFIX} Отдел перевода {stamp()}",
                                                           "legal_entity_id": le["id"]}))
    try:
        open_hrm(auth_page, "/admin/employees")
        list_line(auth_page, full_name(person)).locator("input[type=checkbox]").check()
        transfer = auth_page.get_by_role("button", name=re.compile(r"^Перевести")).first
        expect(transfer).to_be_visible()
        transfer.click()
        dlg = auth_page.locator("div.fixed:visible").filter(has_text="Перевести сотрудников").last
        expect(dlg).to_be_visible()
        dlg.get_by_role("button").filter(has_text=re.compile("Не менять|Выберите|Не выбран")).first.click()
        auth_page.get_by_role("option", name=dep["name"], exact=True).click()
        with auth_page.expect_response(lambda r: "/api/hrm/employees/bulk-transfer" in r.url) as resp:
            dlg.get_by_role("button", name="Перевести", exact=True).click()
        assert resp.value.ok, resp.value.text()
        assert person["id"] in resp.value.request.post_data_json.get("employee_ids", []), resp.value.request.post_data
        got = hrm_json(hrm_get(auth_page, f"/employees/{person['id']}"))
        assert got["department_id"] == dep["id"], got
    finally:
        delete_employee(auth_page, person["id"])
        hrm_delete(auth_page, f"/org/departments/{dep['id']}")
