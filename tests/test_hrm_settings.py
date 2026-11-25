"""Блок 51. HRM — настройки (/admin/settings): справочники, свои поля, плагины.

Снято 02.11.2026 (в видимом MCP-браузере):
- 12 вкладок: «Пользовательские поля», «Юридические лица», «Типы подразделений»,
  «Категории должностей», «Места работы», «Типы объектов», «Причины увольнения»,
  «Статусы документов», «Степени родства», «Формат экспорта», «Плагины», «Импорт Nr. CIM»;
  вкладка грузит свой справочник (GET /org/workplaces, /org/termination-reasons, …);
- простые справочники — форма «Добавить …» под списком («Название», «Код (необязательно)» —
  код сам собирается из названия: «QA Autotest Место» → qa_autotest_место) → POST;
  у строки три значка без подписей: «Отключить» (title; PUT {is_active:false} → метка
  «ОТКЛЮЧЕНО»), карандаш, корзина → окно «Удалить значение? «…» Если значение уже где-то
  проставлено, сервер удалить его не даст — тогда отключите его вместо удаления.» → «Удалить»
  → DELETE;
- «Типы подразделений» и «Категории должностей» — «Название», «Код» (обязателен),
  «Описание», «Цвет»; отключения нет; POST отвечает только {id, name}; удаление — окна
  «Удалить тип подразделения?» / «Удалить категорию?» («… останутся без типа»);
- «Степени родства» — 4 базовых значения: кнопки «Отключить» / «Удалить» показываются по
  флагам API can_deactivate / can_delete («Ребёнок» — «Всегда включено»; у «Ребёнок» и
  «Супруг/супруга» — «Базовое значение нельзя удалить»; «Родитель» и «Другое» базовые, но
  удаляются — см. README, спорная логика);
- «Статусы документов» — системные «Сгенерирован», «Подписан», «Отменён» (метка «Системный»,
  без удаления), свои добавляются полем «Название статуса»;
- «Плагины» = GET/PUT /plugins {job_instructions, rent, hds_timesheet, tms_timesheet, workday_timesheet}: «Должностные
  инструкции» добавляют пункт меню; тест включает и возвращает как было;
- «Импорт Nr. CIM» — без файла «Проверить» / «Заполнить» ничего не шлют (файл не грузим).
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (screen_action, QA_PREFIX, clean_up, create_employee, create_legal_entity, delete_employee, field, header_button,
                         hrm_delete, hrm_get, hrm_json, hrm_put, items, open_hrm, sidebar, stamp)

pytestmark = pytest.mark.requires_auth

TABS = {
    "Пользовательские поля": "/custom-fields",
    "Юридические лица": "/legal-entities",
    "Типы подразделений": "/org/division-types",
    "Категории должностей": "/org/position-categories",
    "Места работы": "/org/workplaces",
    "Типы объектов": "/org/project-object-types",
    "Причины увольнения": "/org/termination-reasons",
    "Статусы документов": "/documents/statuses",
    "Степени родства": "/family/relations",
    "Формат экспорта": "/export-presets/current",
}
# простые справочники с отключением: вкладка → (API, заголовок формы)
SIMPLE = {
    "Места работы": ("/org/workplaces", "Добавить место работы"),
    "Причины увольнения": ("/org/termination-reasons", "Добавить причину"),
    "Типы объектов": ("/org/project-object-types", "Добавить тип объекта"),
}
# справочники с кодом: вкладка → (API, заголовок формы, окно удаления)
CODED = {
    "Типы подразделений": ("/org/division-types", "Добавить тип", "Удалить тип подразделения?"),
    "Категории должностей": ("/org/position-categories", "Добавить категорию", "Удалить категорию?"),
}


def main(page: Page):
    return page.locator("main")


def open_tab(page: Page, tab: str, api: str | None = None):
    open_hrm(page, "/admin/settings")
    if api:
        with page.expect_response(lambda r: f"/api/hrm{api}" in r.url and r.request.method == "GET") as resp:
            main(page).get_by_role("button", name=tab, exact=True).first.click()
        assert resp.value.ok, f"{api} → {resp.value.status}"
        return resp.value.json()
    main(page).get_by_role("button", name=tab, exact=True).first.click()


def add_form(page: Page, title: str):
    return main(page).locator("div").filter(has=page.get_by_text(title, exact=True)).filter(
        has=page.locator("input")).last


def row(page: Page, name: str):
    """Строка справочника — самый вложенный блок с названием и кнопками."""
    return main(page).locator("div").filter(has=page.get_by_text(name, exact=True)).filter(
        has=page.locator("button")).last


def delete_row(page: Page, name: str, api: str, item_id: int, question: str = "Удалить значение?"):
    row(page, name).locator("button").last.click()  # корзина — последний значок, без подписи
    dialog = page.locator("div.fixed").filter(has_text=question).last
    expect(dialog).to_contain_text(f"«{name}»")
    with page.expect_response(lambda r: f"/api/hrm{api}/{item_id}" in r.url and r.request.method == "DELETE") as resp:
        dialog.get_by_role("button", name="Удалить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    expect(main(page).get_by_text(name, exact=True)).to_have_count(0)


def remove_own(page: Page, api: str, key: str = "name"):
    for it in items(hrm_json(hrm_get(page, api))):
        if str(it.get(key) or "").startswith(QA_PREFIX):
            hrm_delete(page, f"{api}/{it['id']}")


# Кейс: Вкладки настроек — каждая грузит свой справочник
@pytest.mark.parametrize("tab", list(TABS))
def test_tab_loads(auth_page: Page, tab):
    data = open_tab(auth_page, tab, TABS[tab])
    lst = items(data) if not isinstance(data, dict) or "items" in data else []
    for it in lst[:5]:
        name = it.get("name") or it.get("name_ru") or it.get("label")
        if name:  # у степеней родства строка «Ребёнок / Copil» — ищем вхождение
            expect(main(auth_page).get_by_text(name).first).to_be_visible()


# Кейс: Простой справочник — добавить, отключить, включить, удалить
@pytest.mark.writes_data
@pytest.mark.parametrize("tab", list(SIMPLE))
def test_simple_dictionary(auth_page: Page, tab):
    api, form_title = SIMPLE[tab]
    name = f"{QA_PREFIX} {tab[:-1]} {stamp()}"
    open_tab(auth_page, tab, api)
    try:
        form = add_form(auth_page, form_title)
        form.locator("input").first.fill(name)
        with auth_page.expect_response(lambda r: f"/api/hrm{api}" in r.url and r.request.method == "POST") as resp:
            form.get_by_role("button", name="Добавить", exact=True).click()
        assert resp.value.ok, resp.value.text()
        created = resp.value.json()
        assert created["name"] == name and created.get("is_active", True), created
        line = row(auth_page, name)
        expect(line).to_be_visible()

        with auth_page.expect_response(lambda r: f"{api}/{created['id']}" in r.url and r.request.method == "PUT") as off:
            line.get_by_title("Отключить").click()
        assert off.value.request.post_data_json["is_active"] is False
        expect(line).to_contain_text(re.compile("отключено", re.I))
        got = next(i for i in items(hrm_json(hrm_get(auth_page, api))) if i["id"] == created["id"])
        assert got["is_active"] is False, got

        with auth_page.expect_response(lambda r: f"{api}/{created['id']}" in r.url and r.request.method == "PUT") as on:
            line.get_by_title("Включить").click()
        assert on.value.request.post_data_json["is_active"] is True
        expect(line).not_to_contain_text(re.compile("отключено", re.I))

        delete_row(auth_page, name, api, created["id"])
        assert created["id"] not in [i["id"] for i in items(hrm_json(hrm_get(auth_page, api)))]
    finally:
        remove_own(auth_page, api)


# Кейс: Справочник с кодом — «Название», «Код», «Описание» и удаление
@pytest.mark.writes_data
@pytest.mark.parametrize("tab", list(CODED))
def test_coded_dictionary(auth_page: Page, tab):
    api, form_title, question = CODED[tab]
    tag = stamp()
    name, code = f"{QA_PREFIX} {tab.split()[0]} {tag}", f"qa_{re.sub(r'\D', '', tag)}"
    open_tab(auth_page, tab, api)
    try:
        inputs = add_form(auth_page, form_title).locator("input:not([type=color])")
        inputs.nth(0).fill(name)
        inputs.nth(1).fill(code)
        inputs.nth(2).fill(f"{QA_PREFIX} описание")
        with auth_page.expect_response(lambda r: f"/api/hrm{api}" in r.url and r.request.method == "POST") as resp:
            add_form(auth_page, form_title).get_by_role("button", name="Добавить", exact=True).click()
        assert resp.value.ok, resp.value.text()
        created = resp.value.json()  # ответ — только {id, name}, остальное видно в списке
        got = next(i for i in items(hrm_json(hrm_get(auth_page, api))) if i["id"] == created["id"])
        assert (got["name"], got["code"], got["description"]) == (name, code, f"{QA_PREFIX} описание"), got
        line = row(auth_page, name)
        expect(line).to_contain_text(code)
        delete_row(auth_page, name, api, created["id"], question)
    finally:
        remove_own(auth_page, api)


# Кейс: Степени родства — «Отключить» и «Удалить» по флагам API
def test_relations_buttons_follow_api(auth_page: Page):
    rels = items(open_tab(auth_page, "Степени родства", "/family/relations"))
    for rel in rels:
        line = main(auth_page).locator("div").filter(has_text=re.compile(rf"^{re.escape(rel['name_ru'])}\s*/")).filter(
            has=auth_page.get_by_role("button", name="Переименовать")).last
        expect(line).to_be_visible()
        if rel["can_deactivate"]:
            expect(line.get_by_role("button", name=re.compile("^(Отключить|Включить)$"))).to_be_visible()
        else:
            expect(line).to_contain_text("Всегда включено")
        delete = line.get_by_role("button", name="Удалить", exact=True)
        if rel["can_delete"]:
            expect(delete).to_be_enabled()
        else:
            expect(line.get_by_role("button", name="Базовое значение нельзя удалить")).to_be_disabled()


PLUGINS = {"job_instructions": "Должностные инструкции", "rent": "Управление арендой",
           "hds_timesheet": "Табель из трекера HDS", "tms_timesheet": "Табель из рейсов TMS",
           "workday_timesheet": "Рабочий день"}


# Кейс: «Плагины» — карточка на каждый ключ /plugins (с 25.11 их 5: + «Табель из рейсов TMS», «Рабочий день»),
# метка «Включён» / «Выключен» = API
def test_plugins_list(auth_page: Page):
    state = hrm_json(hrm_get(auth_page, "/plugins"))
    open_tab(auth_page, "Плагины")
    assert set(state) == set(PLUGINS), f"плагины изменились: {sorted(state)}"
    for key, title in PLUGINS.items():
        card = main(auth_page).locator("div").filter(has=auth_page.get_by_text(title, exact=True)).filter(
            has=auth_page.locator("button")).last
        expect(card).to_contain_text("Включён" if state[key] else "Выключен")


# Кейс: Плагин «Должностные инструкции» добавляет пункт меню
@pytest.mark.writes_data
def test_plugin_job_instructions(auth_page: Page):
    before = hrm_json(hrm_get(auth_page, "/plugins"))
    try:
        if before["job_instructions"]:
            hrm_put(auth_page, "/plugins", {**before, "job_instructions": False})
        open_tab(auth_page, "Плагины")
        card = main(auth_page).locator("div").filter(has=auth_page.get_by_text("Должностные инструкции", exact=True)).filter(
            has=auth_page.locator("button")).last
        expect(card).to_contain_text("Выключен")
        expect(sidebar(auth_page).get_by_role("link", name="Должностные инструкции")).to_have_count(0)
        with auth_page.expect_response(lambda r: r.url.endswith("/api/hrm/plugins") and r.request.method == "PUT") as resp:
            card.get_by_role("button").first.click()
        assert resp.value.ok and resp.value.request.post_data_json["job_instructions"] is True
        expect(card).to_contain_text("Включён")
        link = sidebar(auth_page).get_by_role("link", name="Должностные инструкции", exact=True)
        expect(link).to_have_attribute("href", "/admin/job-instructions")
    finally:
        hrm_put(auth_page, "/plugins", before)


# Кейс: «Импорт Nr. CIM» — без файла «Проверить» и «Заполнить» неактивны
def test_cim_import_needs_file(auth_page: Page):
    open_tab(auth_page, "Импорт Nr. CIM")
    expect(main(auth_page)).to_contain_text("Файл не сохраняется")
    expect(main(auth_page).locator("label").filter(has_text="Выбрать файл .xlsx")).to_be_visible()
    for name in ("Проверить", "Заполнить"):
        expect(main(auth_page).get_by_role("button", name=name, exact=True)).to_be_disabled()


# ───────────────────────── юридические лица ─────────────────────────

def le_card(page: Page, name: str):
    return main(page).locator("div").filter(has=page.get_by_text(name, exact=True)).filter(has=page.locator("button")).last


# Кейс: Юрлицо — форма с реквизитами, архив и обратно, удаление
@pytest.mark.writes_data
def test_legal_entity_lifecycle(auth_page: Page):
    name, idno, iban = f"{QA_PREFIX} Юрлицо {stamp()}", "1009600099017", "MD24AG000225100013104168"
    open_tab(auth_page, "Юридические лица", "/legal-entities")
    try:
        screen_action(auth_page, "Добавить юрлицо").click()
        form = main(auth_page)
        field(form, "Полное наименование *").fill(name)
        field(form, "IDNO (фискальный код)").fill(idno)
        field(form, "IBAN").fill(iban)
        field(form, "Банк").fill(f"{QA_PREFIX} Банк")
        with auth_page.expect_response(lambda r: r.url.endswith("/api/hrm/legal-entities") and r.request.method == "POST") as resp:
            form.get_by_role("button", name="Сохранить", exact=True).click()
        assert resp.value.ok, resp.value.text()
        le = resp.value.json()
        assert (le["name"], le["idno"], le["iban"], le["bank_name"]) == (name, idno, iban, f"{QA_PREFIX} Банк"), le
        card = le_card(auth_page, name)
        expect(card).to_be_visible()

        # «Архив» — сразу, без окна; повторный клик возвращает
        with auth_page.expect_response(lambda r: f"/legal-entities/{le['id']}" in r.url and r.request.method == "PUT") as arch:
            card.get_by_title("Архив").click()
        assert arch.value.request.post_data_json["is_archived"] is True
        expect(card).to_contain_text("архив")
        with auth_page.expect_response(lambda r: f"/legal-entities/{le['id']}" in r.url and r.request.method == "PUT") as back:
            card.get_by_title("Архив").click()
        assert back.value.request.post_data_json["is_archived"] is False
        expect(card).not_to_contain_text("архив")

        card.locator("button").last.click()
        dialog = auth_page.locator("div.fixed").filter(has_text="Удалить юридическое лицо?").last
        expect(dialog).to_contain_text(f"«{name}»")
        with auth_page.expect_response(lambda r: f"/legal-entities/{le['id']}" in r.url and r.request.method == "DELETE") as gone:
            dialog.get_by_role("button", name="Удалить", exact=True).click()
        assert gone.value.ok
        expect(main(auth_page).get_by_text(name, exact=True)).to_have_count(0)
    finally:
        clean_up(auth_page)


# Кейс: Юрлицо с сотрудником удалить нельзя, «N сотр.» = API
@pytest.mark.writes_data
def test_legal_entity_in_use_is_not_deleted(auth_page: Page):
    le = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"])
    try:
        open_tab(auth_page, "Юридические лица", "/legal-entities")
        card = le_card(auth_page, le["name"])
        expect(card).to_contain_text("1 сотр.")
        card.locator("button").last.click()
        dialog = auth_page.locator("div.fixed").filter(has_text="Удалить юридическое лицо?").last
        with auth_page.expect_response(lambda r: f"/legal-entities/{le['id']}" in r.url and r.request.method == "DELETE") as resp:
            dialog.get_by_role("button", name="Удалить", exact=True).click()
        assert resp.value.status in (400, 409, 422), f"юрлицо с сотрудником удалилось: {resp.value.status}"
        auth_page.reload()
        screen_action(auth_page, "Юридические лица", exact=True).first.click()
        expect(le_card(auth_page, le["name"])).to_be_visible()
        assert le["id"] in [x["id"] for x in items(hrm_json(hrm_get(auth_page, "/legal-entities")))]
    finally:
        delete_employee(auth_page, emp["id"])
        clean_up(auth_page)


# ───────────────────────── свои поля, статусы, родство ─────────────────────────

def status_row(page: Page, name: str):
    """Строка статуса: системный — текстом, свой — значением поля ввода."""
    return main(page).locator("div.flex.items-center.gap-3").filter(
        has=page.locator(f'input[value="{name}"], span:text-is("{name}"), div:text-is("{name}")')).first


def confirm_delete(page: Page, question: re.Pattern | str):
    dialog = page.locator("div.fixed").filter(has_text=question).last
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Удалить", exact=True).click()


# Кейс: Своё поле сотрудника — появляется в форме «Добавить сотрудника», удаляется
@pytest.mark.writes_data
def test_custom_field_for_employee(auth_page: Page):
    label = f"{QA_PREFIX} Поле {stamp()}"
    open_tab(auth_page, "Пользовательские поля")
    try:
        screen_action(auth_page, "Добавить поле").click()
        form = main(auth_page)
        expect(field(form, "Сущность")).to_contain_text("Сотрудник")
        expect(field(form, "Тип")).to_contain_text("Текст")
        field(form, "Название (по умолчанию)").fill(label)
        form.get_by_label("Фильтруемое").check()
        form.get_by_label("В таблице").check()  # «В таблице» и «В карточке» включены по умолчанию
        with auth_page.expect_response(lambda r: r.url.split("?")[0].endswith("/api/hrm/custom-fields") and r.request.method == "POST") as resp:
            form.get_by_role("button", name="Сохранить", exact=True).click()
        assert resp.value.ok, resp.value.text()
        cf = resp.value.json()
        assert (cf["entity_type"], cf["label"], cf["field_type"]) == ("employee", label, "text"), cf
        assert (cf["is_filterable"], cf["is_visible_in_table"], cf["is_visible_in_card"]) == (True, True, True), cf
        line = row(auth_page, label)
        expect(line).to_be_visible()

        # поле видно в форме нового сотрудника
        open_hrm(auth_page, "/admin/employees")
        header_button(auth_page, "Добавить").click()  # кнопка в шапке
        expect(auth_page.get_by_text(label, exact=True).first).to_be_visible()
        auth_page.get_by_role("button", name="Отмена", exact=True).click()

        open_tab(auth_page, "Пользовательские поля")
        row(auth_page, label).locator("button").last.click()
        with auth_page.expect_response(lambda r: f"/custom-fields/{cf['id']}" in r.url and r.request.method == "DELETE") as gone:
            confirm_delete(auth_page, re.compile("Удалить"))
        assert gone.value.ok
        expect(main(auth_page).get_by_text(label, exact=True)).to_have_count(0)
    finally:
        remove_own(auth_page, "/custom-fields", key="label")


# Кейс: Статусы документов — системные без удаления, свой добавляется и удаляется
@pytest.mark.writes_data
def test_document_statuses(auth_page: Page):
    statuses = items(open_tab(auth_page, "Статусы документов", "/documents/statuses"))
    for sys_name in ("Сгенерирован", "Подписан", "Отменён"):
        line = status_row(auth_page, sys_name)
        expect(line).to_contain_text("Системный")
        expect(line.get_by_title("Удалить")).to_have_count(0)
    assert {"Сгенерирован", "Подписан", "Отменён"} <= {s["name"] for s in statuses}, statuses
    name = f"{QA_PREFIX} Статус {stamp()}"
    try:
        main(auth_page).get_by_placeholder("Название статуса").fill(name)
        with auth_page.expect_response(lambda r: r.url.split("?")[0].endswith("/api/hrm/documents/statuses") and r.request.method == "POST") as resp:
            screen_action(auth_page, "Добавить", exact=True).click()
        assert resp.value.ok, resp.value.text()
        st = resp.value.json()
        line = status_row(auth_page, name)  # своё название — в поле ввода (переименование на месте)
        expect(line).not_to_contain_text("Системный")
        expect(line).to_contain_text("0 док.")
        line.get_by_title("Удалить").click()
        with auth_page.expect_response(lambda r: f"/documents/statuses/{st['id']}" in r.url and r.request.method == "DELETE") as gone:
            confirm_delete(auth_page, "Удалить статус?")
        assert gone.value.ok
        expect(status_row(auth_page, name)).to_have_count(0)
    finally:
        remove_own(auth_page, "/documents/statuses")


# Кейс: Степень родства — своя добавляется (RU / RO), отключается и удаляется
@pytest.mark.writes_data
def test_own_relation(auth_page: Page):
    tag = stamp()
    ru, ro = f"{QA_PREFIX} Кузен {tag}", f"{QA_PREFIX} Văr {tag}"
    open_tab(auth_page, "Степени родства", "/family/relations")
    try:
        screen_action(auth_page, "Добавить", exact=True).click()
        main(auth_page).get_by_placeholder("Название (RU)").fill(ru)
        main(auth_page).get_by_placeholder("Название (RO)").fill(ro)
        with auth_page.expect_response(lambda r: r.url.split("?")[0].endswith("/api/hrm/family/relations") and r.request.method == "POST") as resp:
            screen_action(auth_page, "Сохранить", exact=True).click()
        assert resp.value.ok, resp.value.text()
        rel = resp.value.json()
        assert (rel["name_ru"], rel["name_ro"], rel.get("is_base", False)) == (ru, ro, False), rel
        line = main(auth_page).locator("div").filter(has_text=re.compile(rf"^{re.escape(ru)}\s*/")).filter(
            has=auth_page.get_by_role("button", name="Переименовать")).last
        expect(line).to_contain_text(ro)
        expect(line).not_to_contain_text("БАЗОВОЕ")
        with auth_page.expect_response(lambda r: f"/family/relations/{rel['id']}" in r.url and r.request.method == "PUT"):
            line.get_by_role("button", name="Отключить").click()
        expect(line.get_by_role("button", name="Включить")).to_be_visible()
        line.get_by_role("button", name="Удалить", exact=True).click()
        with auth_page.expect_response(lambda r: f"/family/relations/{rel['id']}" in r.url and r.request.method == "DELETE") as gone:
            confirm_delete(auth_page, re.compile("Удалить"))
        assert gone.value.ok
        expect(main(auth_page).get_by_text(ru)).to_have_count(0)
    finally:
        remove_own(auth_page, "/family/relations", key="name_ru")
