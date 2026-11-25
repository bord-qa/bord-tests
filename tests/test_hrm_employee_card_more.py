"""Блок 71. HRM — карточка сотрудника (файлы, фото, совместительство, история и номер договора, связи) и
цепочки согласования отсутствий.

Снято 03.11.2026 в видимом MCP-браузере:
- «Файлы»: «Загрузить файлы» (можно несколько, до 50 МБ), «Нет файлов» → POST /employees/<id>/files,
  скачивание /files/<id>/download, удаление DELETE;
- фото — «Загрузить фото (макс. 5MB)» у аватара → POST /employees/<id>/photo, GET …/photo;
- «Совместительство» → «Добавить совместительство»: «Юридическое лицо *» (основное — «внутреннее»),
  «Отдел», «Должность», «Ставка, % от полной занятости», «График работы», «Тип занятости» (Частичная),
  «Дата начала *», «Дата окончания», «Номер договора» (пусто — из журнала юрлица), «Примечание» → POST
  /secondary-employments → «Совместительство оформлено: <юрлицо>. Номер договора: …», «Активно ·
  50% (0,5 ставки)»; «Завершить» → «Завершить совместительство» («Дата окончания *», «Причина
  завершения») → POST …/close → «… завершено <дата>. Основное трудоустройство не затронуто.», «ИСТОРИЯ
  ЗАВЕРШЁННЫХ ЗАПИСЕЙ»;
- «История сотрудника»: период «<дата> — по настоящее время · Текущий», «Закрыть период», «Изменить
  номер договора» → окно «Номер договора для этого периода» → PATCH /employees/<id>/periods/<pid>;
- «Основное» → «Связи» → «Добавить связь» → «Модуль…» (Финансы, Склад, …, HR, CRM…) → «Тип…» (для HR:
  Сотрудник, Отпуск, Цель, Обучение) → «ID» → «Связано» … → «Добавить» → POST /api/entity-links/ →
  «Сотрудник #<id> · HR · Связано» → «Удалить связь» → «Удалить связь?» → «Да»;
- «Отсутствия» → «Цепочки согласования» → «Создать» → «Новая цепочка согласования» («Название»,
  «Применяется к»: Все / Рабочие / Офисные) → POST /leave/approval-chains (не по умолчанию — на чужие
  заявки не влияет) → «Активна · Все -- N шагов»; в карточке «Шаги согласования»: тип согласующего
  (Непосредственный руководитель / Руководитель отдела / HR менеджер / Директор / Конкретный сотрудник),
  «Обязат.», «Добавить» → PUT steps; «HR менеджер» без назначенной роли → «В организации не назначен
  HR-менеджер…»; «Вверх / Вниз / Удалить шаг», «Удалить цепочку».
"""
import datetime as dt

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (screen_action, HRM, QA_PREFIX, _headers, clean_up, create_employee, create_legal_entity, date_field,
                         delete_employee, dropdown, field, hrm_delete, hrm_get, hrm_json, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()
ME = 1717


def main(page: Page):
    return page.locator("main")


def dialog(page: Page, text: str):
    return page.locator("div.fixed.inset-0:visible").filter(has_text=text).last


@pytest.fixture
def person(auth_page: Page):
    le = create_legal_entity(auth_page)
    le2 = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Карточка {stamp()}")
    yield {**emp, "le": le, "le2": le2}
    links = auth_page.request.get(f"{HRM}/api/entity-links/", headers=_headers(auth_page),
                                  params={"module": "hrm", "entity_type": "employee", "entity_id": emp["id"]})
    data = links.json() if links.ok else []
    for link in (data.get("items", []) if isinstance(data, dict) else data):
        auth_page.request.delete(f"{HRM}/api/entity-links/{link['id']}", headers=_headers(auth_page))
    delete_employee(auth_page, emp["id"])
    clean_up(auth_page)


def file_list(page: Page, emp_id: int) -> list:
    data = hrm_json(hrm_get(page, f"/employees/{emp_id}/files"))
    return data["items"] if isinstance(data, dict) else data


def open_card(page: Page, emp: dict, tab: str | None = None):
    open_hrm(page, "/admin/employees")
    main(page).get_by_role("button").filter(has_text=emp["telegram_first_name"]).first.click()
    expect(main(page)).to_contain_text(f"#{emp['id']}")
    if tab:
        main(page).get_by_role("button", name=tab, exact=True).last.click()


# Кейс: «Файлы» — загрузить файл, скачать то же содержимое, удалить
@pytest.mark.writes_data
def test_employee_files(auth_page: Page, person, tmp_path):
    doc = tmp_path / f"qa-autotest-{stamp().replace(' ', '-').replace('.', '')}.txt"
    doc.write_text("QA Autotest: справка сотрудника", encoding="utf-8")
    open_card(auth_page, person, "Файлы")
    expect(main(auth_page)).to_contain_text("Нет файлов")
    with auth_page.expect_response(lambda r: f"/employees/{person['id']}/files" in r.url and r.request.method == "POST") as up:
        # с 08.11 над «Файлами» — «Папка на Диске» со своим полем «На Диск» (туда не грузим):
        # поле «Загрузить файлы» — последнее из множественных (первое поле карточки — фото)
        main(auth_page).locator("input[type=file][multiple]").last.set_input_files(str(doc))
    assert up.value.ok, up.value.text()
    [f] = hrm_json(hrm_get(auth_page, f"/employees/{person['id']}/files")) if isinstance(
        hrm_json(hrm_get(auth_page, f"/employees/{person['id']}/files")), list) else \
        hrm_json(hrm_get(auth_page, f"/employees/{person['id']}/files"))["items"]
    row = main(auth_page).locator("tr, li, div").filter(has_text=doc.name).last
    expect(row).to_be_visible()
    body = hrm_get(auth_page, f"/employees/{person['id']}/files/{f['id']}/download")
    assert body.ok and body.text() == "QA Autotest: справка сотрудника", body.status
    assert hrm_delete(auth_page, f"/employees/{person['id']}/files/{f['id']}").ok
    assert file_list(auth_page, person["id"]) == []


# Кейс: Фото сотрудника — «Загрузить фото» у аватара, фото отдаётся API; удаление
@pytest.mark.writes_data
def test_employee_photo(auth_page: Page, person, tmp_path):
    import base64
    png = tmp_path / "qa-photo.png"
    png.write_bytes(base64.b64decode(  # 1×1 px PNG
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="))
    open_card(auth_page, person)
    with auth_page.expect_response(lambda r: f"/employees/{person['id']}/photo" in r.url and r.request.method == "POST") as up:
        main(auth_page).locator("[title='Загрузить фото (макс. 5MB)'] input[type=file], label:has([title]) input[type=file]").first \
            .set_input_files(str(png))
    assert up.value.ok, up.value.text()
    photo = hrm_get(auth_page, f"/employees/{person['id']}/photo")
    assert photo.ok and photo.headers["content-type"].startswith("image/"), (photo.status, photo.headers.get("content-type"))
    assert hrm_delete(auth_page, f"/employees/{person['id']}/photo").ok
    assert hrm_get(auth_page, f"/employees/{person['id']}/photo").status == 404


# Кейс: «Добавить совместительство» в другом своём юрлице на 0,5 ставки → «Активно» = API → «Завершить» → история завершённых
@pytest.mark.writes_data
def test_secondary_employment(auth_page: Page, person):
    number = f"QA-SEC-{stamp()[-6:]}"
    open_card(auth_page, person, "Совместительство")
    expect(main(auth_page)).to_contain_text("Совместительство не оформлено")
    screen_action(auth_page, "Добавить совместительство").click()
    box = dialog(auth_page, "Добавить совместительство")
    field(box, "Юридическое лицо *").click()
    expect(auth_page.get_by_role("option").filter(has_text=f"{person['le']['name']} — внутреннее (основное юрлицо)")).to_be_visible()
    auth_page.get_by_role("option", name=person["le2"]["name"], exact=True).click()
    field(box, "Ставка, % от полной занятости").fill("50")
    expect(field(box, "Тип занятости")).to_contain_text("Частичная занятость")
    date_field(box, "Дата начала *").fill(str(TODAY))
    field(box, "Номер договора").fill(number)
    with auth_page.expect_response(lambda r: r.url.endswith(f"/employees/{person['id']}/secondary-employments")
                                   and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Добавить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    sec = resp.value.json()
    assert (sec["legal_entity_id"], sec["rate_percent"], sec["employment_type"], sec["contract_number"]) == \
        (person["le2"]["id"], 50.0, "part_time", number), sec
    expect(main(auth_page)).to_contain_text(f"Совместительство оформлено: {person['le2']['name']}. Номер договора: {number}.")
    expect(main(auth_page)).to_contain_text("50% (0,5 ставки)")
    expect(main(auth_page)).to_contain_text("Активно")

    screen_action(auth_page, "Завершить", exact=True).click()
    box = dialog(auth_page, "Завершить совместительство")
    expect(box).to_contain_text("Это не увольнение из группы")
    date_field(box, "Дата окончания *").fill(str(TODAY))
    with auth_page.expect_response(lambda r: f"/secondary-employments/{sec['id']}/close" in r.url) as close:
        box.get_by_role("button", name="Завершить совместительство").click()
    assert close.value.ok, close.value.text()
    expect(main(auth_page)).to_contain_text(f"завершено {TODAY:%d.%m.%Y}. Основное трудоустройство не затронуто.")
    expect(main(auth_page)).to_contain_text("История завершённых записей")  # капс — CSS
    [got] = items(hrm_json(hrm_get(auth_page, f"/employees/{person['id']}/secondary-employments")))
    assert (got["id"], got["end_date"]) == (sec["id"], str(TODAY)), got
    assert hrm_json(hrm_get(auth_page, f"/employees/{person['id']}"))["is_active"] is True  # основное не тронуто


# Кейс: «История сотрудника» — текущий период с юрлицом, «Изменить номер договора» = PATCH периода
@pytest.mark.writes_data
def test_history_contract_number(auth_page: Page, person):
    number = f"QA-CIM-{stamp()[-6:]}"
    open_card(auth_page, person, "История сотрудника")
    expect(main(auth_page)).to_contain_text(f"{TODAY:%d.%m.%Y} — по настоящее время")
    expect(main(auth_page)).to_contain_text("Текущий")
    expect(main(auth_page)).to_contain_text(person["le"]["name"])
    screen_action(auth_page, "Изменить номер договора").click()
    box = dialog(auth_page, "Номер договора для этого периода")
    box.locator("input").fill(number)
    with auth_page.expect_response(lambda r: f"/employees/{person['id']}/periods/" in r.url and r.request.method == "PATCH") as upd:
        box.get_by_role("button", name="OK", exact=True).click()
    assert upd.value.ok and upd.value.json()["contract_number"] == number, upd.value.text()
    expect(main(auth_page)).to_contain_text(number)


# Кейс: «Связи» — связать своего сотрудника с другим сотрудником HR (= /api/entity-links) и удалить связь
@pytest.mark.writes_data
def test_entity_link(auth_page: Page, person):
    open_card(auth_page, person, "Основное")
    links = main(auth_page).locator("div").filter(has_text="Связи").filter(
        has=auth_page.get_by_role("button", name="Добавить связь")).last.locator("xpath=..")  # шапка + список
    expect(links).to_contain_text("Нет связей")
    links.get_by_role("button", name="Добавить связь").click()
    links.get_by_role("button", name="Модуль...").click()
    auth_page.get_by_role("option", name="HR", exact=True).click()
    links.get_by_role("button", name="Тип...").click()
    auth_page.get_by_role("option", name="Сотрудник", exact=True).click()
    links.get_by_placeholder("ID", exact=True).fill(str(ME))
    with auth_page.expect_response(lambda r: r.url.endswith("/api/entity-links/") and r.request.method == "POST") as resp:
        links.get_by_role("button", name="Добавить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    link = resp.value.json()
    assert (link["source_entity_id"], link["target_module"], link["target_entity_type"], link["target_entity_id"],
            link["link_type"]) == (person["id"], "hrm", "employee", ME, "related"), link
    expect(links).to_contain_text(f"Сотрудник #{ME}")
    expect(links).to_contain_text("HR · Связано")
    links.get_by_role("button", name="Удалить связь").click()
    sure = dialog(auth_page, "Удалить связь?")
    with auth_page.expect_response(lambda r: f"/api/entity-links/{link['id']}" in r.url and r.request.method == "DELETE") as gone:
        sure.get_by_role("button", name="Да", exact=True).click()
    assert gone.value.ok, gone.value.text()
    expect(links).to_contain_text("Нет связей")


# ───────────────────────── цепочки согласования ─────────────────────────

# Кейс: Цепочка согласования — создать, шаг «Непосредственный руководитель», «HR менеджер» без роли — подсказка, удалить шаг и цепочку
@pytest.mark.writes_data
def test_approval_chain(auth_page: Page):
    name = f"{QA_PREFIX} Цепочка {stamp()}"
    chain_id = None
    try:
        open_hrm(auth_page, "/admin/leave")
        screen_action(auth_page, "Цепочки согласования", exact=True).click()
        screen_action(auth_page, "Создать", exact=True).click()
        box = dialog(auth_page, "Новая цепочка согласования")
        field(box, "Название").fill(name)
        dropdown(auth_page, box, "Применяется к", "Все")
        with auth_page.expect_response(lambda r: r.url.endswith("/leave/approval-chains") and r.request.method == "POST") as resp:
            box.get_by_role("button", name="Создать", exact=True).click()
        assert resp.value.ok, resp.value.text()
        chain = resp.value.json()
        chain_id = chain["id"]
        assert (chain["applies_to"], chain["is_default"], chain["is_active"], chain["steps"]) == ("all", False, True, []), chain
        item = main(auth_page).get_by_role("button").filter(has_text=name)
        expect(item).to_contain_text("Все -- 0 шагов")
        item.click()
        expect(main(auth_page)).to_contain_text("Нет шагов. Добавьте первый шаг.")
        with auth_page.expect_response(lambda r: f"/leave/approval-chains/{chain_id}" in r.url and r.request.method == "PUT") as s1:
            screen_action(auth_page, "Добавить", exact=True).click()
        assert s1.value.ok and [st["approver_type"] for st in s1.value.json()["steps"]] == ["direct_manager"], s1.value.text()
        expect(main(auth_page)).to_contain_text("Обязательный")

        main(auth_page).get_by_role("button", name="Непосредственный руководитель", exact=True).last.click()
        auth_page.get_by_role("option", name="HR менеджер", exact=True).click()
        with auth_page.expect_response(lambda r: f"/leave/approval-chains/{chain_id}" in r.url and r.request.method == "PUT") as s2:
            screen_action(auth_page, "Добавить", exact=True).click()
        assert s2.value.status == 422, s2.value.text()
        expect(main(auth_page)).to_contain_text("В организации не назначен HR-менеджер.")

        with auth_page.expect_response(lambda r: f"/leave/approval-chains/{chain_id}" in r.url and r.request.method == "PUT") as s3:
            screen_action(auth_page, "Удалить шаг").first.click()
            confirm = auth_page.locator("div.fixed.inset-0:visible").get_by_role("button", name="Да", exact=True)
            if confirm.count():
                confirm.click()
        assert s3.value.ok and s3.value.json()["steps"] == [], s3.value.text()

        screen_action(auth_page, "Удалить цепочку").click()
        sure = auth_page.locator("div.fixed.inset-0:visible").last
        with auth_page.expect_response(lambda r: f"/leave/approval-chains/{chain_id}" in r.url and r.request.method == "DELETE") as gone:
            sure.get_by_role("button", name="Да", exact=True).or_(sure.get_by_role("button", name="Удалить", exact=True)).first.click()
        assert gone.value.ok, gone.value.text()
        chain_id = None
        expect(main(auth_page).get_by_role("button").filter(has_text=name)).to_have_count(0)
    finally:
        if chain_id:
            hrm_delete(auth_page, f"/leave/approval-chains/{chain_id}")
