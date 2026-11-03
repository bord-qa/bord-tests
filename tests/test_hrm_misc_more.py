"""Блок 72. HRM — ТМЦ (статусы «Утеряно…», акт приёма), файлы кандидата, конструктор отчётов, «Импорт Nr. CIM»,
плагины «Управление арендой» и «Табель из трекера HDS».

Снято 03.11.2026 в видимом MCP-браузере:
- ТМЦ → «Открыть» выдачу → «Изменить статус» → «ТЕКУЩИЙ: ВЫДАНО», «Возвращено / Утеряно / Списано /
  Повреждено (причина)» → «Изменить статус ТМЦ» («Выдано → Утеряно», «Причина *» — без неё «Сохранить»
  неактивна) → PATCH /tmc/issues/<id>/status → «Закрыт · Утеряно ×N», склад не пополняется; утерянное при
  этом числится «возвращён полностью» / returned_quantity — bord-58; после возврата (return-event) — «Акт
  приёма» → /generate-return-act → «TMC-RET-<год>-NNN», удаление акта kind=return;
- кандидат: файлы /recruit/candidates/<id>/files?kind= (резюме — kind=cv), в карточке «Комментарии» —
  «Прикрепить файл» + «Добавить комментарий» → комментарий с вложением;
- «Отчёты» → «Конструктор отчётов»: «Добавить фильтр» (поле · условие · значение) → «Применить» →
  POST /report-builder/query → «Найдено: N»; «Сохранить» → «Сохранить отчёт» («Название», «Видимость»:
  Личный) → POST /report-builder/reports → «Сохранённые отчёты»; фильтр по «ФИО» не находит никого — bord-59;
- Настройки → «Импорт Nr. CIM»: Excel со столбцами «Nr. CIM», «IDNP», «Nume/Prenume», «Data angajării»
  (сотрудник по IDNP, период по дате приёма, заполняются только пустые номера, файл не сохраняется) →
  «Проверить» (dry_run) → «Заполнить»;
- «Плагины» → «Управление арендой»: пункт меню «Управление арендой» (/admin/rentals), «Новое жильё»
  («Адрес *», «Хозяин», «Стоимость аренды в месяц», «Мест всего»…) → «Мест: N · занято 0 · свободно N»,
  «Заселить» своего сотрудника → «занято 1»; «Табель из трекера HDS» — переключатель; тест возвращает плагины.
"""
import datetime as dt
import io
import re

import openpyxl
import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (HRM, QA_PREFIX, _headers, clean_up, create_employee, create_legal_entity, delete_employee,
                         dropdown, field, hrm_delete, hrm_get, hrm_json, hrm_patch, hrm_post, hrm_put, items, open_hrm,
                         sidebar, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()


def main(page: Page):
    return page.locator("main")


def dialog(page: Page, text: str):
    return page.locator("div.fixed.inset-0:visible").filter(has_text=text).last


@pytest.fixture
def person(auth_page: Page):
    le = create_legal_entity(auth_page)
    idnp = "2" + re.sub(r"\D", "", stamp())[-12:].rjust(12, "0")
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Разное {stamp()}", last_name="Тестов", national_id=idnp)
    yield {**emp, "le": le, "idnp": idnp}
    for i in items(hrm_json(hrm_get(auth_page, "/tmc/issues/", {"employee_id": emp["id"]}))):
        for kind in ("return", "issue"):
            hrm_delete(auth_page, f"/tmc/issues/{i['id']}/act", {"kind": kind, "cascade": "true"})
        hrm_delete(auth_page, f"/tmc/issues/{i['id']}")
    for it in items(hrm_json(hrm_get(auth_page, "/tmc/catalog/"))):
        if it["name"].startswith(QA_PREFIX):
            hrm_delete(auth_page, f"/tmc/catalog/{it['id']}")
    delete_employee(auth_page, emp["id"])
    clean_up(auth_page)


def issue_item(page: Page, emp_id: int, qty: int) -> dict:
    item = hrm_json(hrm_post(page, "/tmc/catalog/", {"name": f"{QA_PREFIX} Каска {stamp()}", "code": "QA-ST-" + stamp()[-6:],
                                                     "is_tracked": True, "quantity_on_hand": 5, "unit": "шт", "cost": 300,
                                                     "refund_amount": 100, "is_returnable": True}))
    hrm_json(hrm_post(page, "/tmc/issues/bulk", {"employee_id": emp_id, "issue_date": str(TODAY),
                                                 "items": [{"catalog_id": item["id"], "quantity": qty}]}))
    issue = next(i for i in items(hrm_json(hrm_get(page, "/tmc/issues/", {"employee_id": emp_id}))) if i["tmc_item_id"] == item["id"])
    return {"item": item, "issue": issue}


def stock(page: Page, item_id: int) -> int:
    return next(i for i in items(hrm_json(hrm_get(page, "/tmc/catalog/"))) if i["id"] == item_id)["quantity_on_hand"]


# ───────────────────────── ТМЦ ─────────────────────────

# Кейс: «Изменить статус» → «Утеряно» с причиной — выдача «Закрыт · Утеряно ×3», склад не пополняется
@pytest.mark.writes_data
def test_tmc_lost_status(auth_page: Page, person):
    t = issue_item(auth_page, person["id"], 3)
    assert stock(auth_page, t["item"]["id"]) == 2
    open_hrm(auth_page, "/admin/tmc")
    main(auth_page).get_by_role("button", name="Выдача ТМЦ", exact=True).click()
    main(auth_page).locator("tr").filter(has_text=person["telegram_first_name"]).get_by_role("button", name="Открыть").click()
    card = dialog(auth_page, "Дата выдачи")
    card.get_by_role("button", name="Изменить статус").click()
    expect(card).to_contain_text("ТЕКУЩИЙ: ВЫДАНО", use_inner_text=True)
    for name in ("Возвращено", "Утеряно", "Списано", "Повреждено"):
        expect(card.get_by_role("button").filter(has_text=re.compile(rf"^{name}")).first).to_be_visible()
    card.get_by_role("button").filter(has_text=re.compile(r"^Утеряно")).first.click()
    box = dialog(auth_page, "Изменить статус ТМЦ")
    expect(box).to_contain_text("Выдано")
    expect(box).to_contain_text("Утеряно")
    expect(box.get_by_role("button", name="Сохранить", exact=True)).to_be_disabled()
    box.locator("textarea").fill(f"{QA_PREFIX} потерял на объекте")
    with auth_page.expect_response(lambda r: f"/tmc/issues/{t['issue']['id']}/status" in r.url) as st:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert st.value.ok and st.value.json() == {"ok": True, "status": "lost", "old_status": "issued"}, st.value.text()
    expect(dialog(auth_page, "Дата выдачи")).to_contain_text("Утеряно ×3")
    issue = next(i for i in items(hrm_json(hrm_get(auth_page, "/tmc/issues/", {"employee_id": person["id"]}))))
    assert (issue["status"], issue["status_reason"]) == ("lost", f"{QA_PREFIX} потерял на объекте"), issue
    assert stock(auth_page, t["item"]["id"]) == 2  # утерянное на склад не вернулось


# Кейс: Утерянная позиция не числится возвращённой
@pytest.mark.writes_data
@pytest.mark.xfail(strict=True, reason="bord-58: у утерянной lifecycle_status=returned, returned_quantity = выданному, «возвращён полностью»")
def test_tmc_lost_not_returned(auth_page: Page, person):
    t = issue_item(auth_page, person["id"], 2)
    hrm_json(hrm_patch(auth_page, f"/tmc/issues/{t['issue']['id']}/status", {"status": "lost", "status_reason": f"{QA_PREFIX} утеря"}))
    issue = next(i for i in items(hrm_json(hrm_get(auth_page, "/tmc/issues/", {"employee_id": person["id"]}))))
    assert (issue["returned_quantity"], issue["lifecycle_status"]) == (0, "lost"), issue


# Кейс: Возврат → «Акт приёма» TMC-RET с PDF → удаление акта
@pytest.mark.writes_data
def test_tmc_return_act(auth_page: Page, person):
    t = issue_item(auth_page, person["id"], 1)
    hrm_json(hrm_post(auth_page, f"/tmc/issues/{t['issue']['id']}/return-event", {"quantity": 1, "outcome": "returned"}))
    open_hrm(auth_page, "/admin/tmc")
    main(auth_page).get_by_role("button", name="Выдача ТМЦ", exact=True).click()
    main(auth_page).locator("tr").filter(has_text=person["telegram_first_name"]).get_by_role("button", name="Открыть").click()
    card = dialog(auth_page, "Дата выдачи")
    card.get_by_role("button", name="Акт приёма", exact=True).click()
    form = dialog(auth_page, "Подтвердить")  # «Сформировать акт…» с позициями и «Подтвердить»
    expect(form.get_by_role("button", name="Подтвердить", exact=True)).to_be_visible()
    with auth_page.expect_response(lambda r: "/generate-return-act" in r.url, timeout=60_000) as gen:
        form.get_by_role("button", name="Подтвердить", exact=True).click()
    assert gen.value.ok, gen.value.text()
    act = gen.value.json()
    assert re.fullmatch(rf"TMC-RET-{TODAY.year}-\d{{3}}", act["act_number"]) and act["file_url"].endswith(".pdf"), act
    preview = hrm_json(hrm_get(auth_page, f"/tmc/issues/{t['issue']['id']}/act/delete-preview", {"kind": "return"}))
    assert preview["act_number"] == act["act_number"], preview
    gone = hrm_delete(auth_page, f"/tmc/issues/{t['issue']['id']}/act", {"kind": "return", "cascade": "true"})
    assert gone.ok, gone.text()


# ───────────────────────── кандидат: файлы ─────────────────────────

# Кейс: Резюме кандидата (kind=cv) и комментарий с вложением в карточке кандидата
@pytest.mark.writes_data
def test_candidate_files(auth_page: Page, tmp_path):
    first = f"{QA_PREFIX} Файлы {stamp()}"
    cand = hrm_json(hrm_post(auth_page, "/recruit/candidates", {"first_name": first}))
    try:
        cv = auth_page.request.post(f"{HRM}/api/hrm/recruit/candidates/{cand['id']}/files", params={"kind": "cv"},
                                    headers=_headers(auth_page),
                                    multipart={"file": {"name": "qa-cv.txt", "mimeType": "text/plain",
                                                        "buffer": "QA Autotest резюме".encode()}})
        assert cv.ok, cv.text()
        files = items(hrm_json(hrm_get(auth_page, f"/recruit/candidates/{cand['id']}/files")))
        assert [(f.get("kind"), f.get("original_name") or f.get("name") or f.get("filename")) for f in files][0][0] == "cv", files

        note = tmp_path / "qa-note.txt"
        note.write_text("QA Autotest вложение", encoding="utf-8")
        open_hrm(auth_page, "/admin/recruitment/candidates")
        main(auth_page).locator("tbody tr").filter(has_text=first).locator("td").first.click()
        card = dialog(auth_page, f"Кандидат #{cand['id']}")
        card.locator("input[type=file]").last.set_input_files(str(note))
        text = f"{QA_PREFIX} комментарий с файлом"
        card.locator("textarea").fill(text)
        with auth_page.expect_response(lambda r: f"/recruit/candidates/{cand['id']}/comments/" in r.url
                                       and r.url.endswith("/attachments") and r.request.method == "POST") as att:
            card.get_by_role("button", name="Добавить комментарий").click()  # сначала комментарий, потом файл к нему
        assert att.value.ok, att.value.text()
        expect(card).to_contain_text(text)
        expect(card).to_contain_text("qa-note.txt")
        [comment] = items(hrm_json(hrm_get(auth_page, f"/recruit/candidates/{cand['id']}/comments")))
        assert comment["text"] == text and len(comment["attachments"]) == 1, comment
    finally:
        hrm_delete(auth_page, f"/recruit/candidates/{cand['id']}")


# ───────────────────────── конструктор отчётов ─────────────────────────

# Кейс: «Конструктор отчётов» — фильтр «Имя содержит» = 1 найденный, «Сохранить отчёт» (Личный) → в «Сохранённых»
@pytest.mark.writes_data
def test_report_builder_filter_and_save(auth_page: Page, person):
    name = f"{QA_PREFIX} Отчёт {stamp()}"
    open_hrm(auth_page, "/admin/reports")
    main(auth_page).get_by_role("button", name="Конструктор отчётов", exact=True).click()
    main(auth_page).get_by_role("button", name="Добавить фильтр").click()
    main(auth_page).get_by_role("button", name="ФИО", exact=True).first.click()
    auth_page.get_by_role("option", name="Имя", exact=True).click()
    main(auth_page).locator("input[type=text]:not([placeholder])").first.fill(person["telegram_first_name"])
    with auth_page.expect_response(lambda r: r.url.endswith("/report-builder/query")) as q:
        main(auth_page).get_by_role("button", name="Применить", exact=True).click()
    assert q.value.request.post_data_json["filters"] == [{"key": "first_name", "op": "contains",
                                                         "value": person["telegram_first_name"]}], q.value.request.post_data
    expect(main(auth_page)).to_contain_text("Найдено: 1")
    expect(main(auth_page).locator("tbody tr")).to_contain_text(person["telegram_first_name"])
    report_id = None
    try:
        main(auth_page).get_by_role("button", name="Сохранить", exact=True).click()
        box = dialog(auth_page, "Сохранить отчёт")
        expect(box).to_contain_text("Личный")
        box.locator("input").first.fill(name)
        with auth_page.expect_response(lambda r: r.url.endswith("/report-builder/reports") and r.request.method == "POST") as sv:
            box.get_by_role("button", name="Сохранить", exact=True).click()
        assert sv.value.ok, sv.value.text()
        report_id = sv.value.json()["id"]
        saved = next(r for r in items(hrm_json(hrm_get(auth_page, "/report-builder/reports"))) if r["id"] == report_id)
        assert saved["name"] == name and saved["config"]["filters"][0]["key"] == "first_name", saved
        expect(main(auth_page)).to_contain_text(name)
    finally:
        if report_id:
            hrm_delete(auth_page, f"/report-builder/reports/{report_id}")


# Кейс: Фильтр конструктора по «ФИО» находит сотрудника по части имени
@pytest.mark.writes_data
@pytest.mark.xfail(strict=True, reason="bord-59: фильтр full_name в /report-builder/query всегда 0")
def test_report_builder_fio_filter(auth_page: Page, person):
    r = hrm_json(hrm_post(auth_page, "/report-builder/query", {
        "filters": [{"key": "full_name", "op": "contains", "value": person["telegram_first_name"]}],
        "columns": ["full_name"], "limit": 50}))
    assert r["total"] == 1, r


# ───────────────────────── импорт Nr. CIM ─────────────────────────

# Кейс: «Импорт Nr. CIM» — Excel по IDNP заполняет пустой номер договора текущего периода; «Проверить» ничего не пишет
@pytest.mark.writes_data
def test_cim_import(auth_page: Page, person, tmp_path):
    number = f"QA-CIM-{stamp()[-6:]}"
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Nr. CIM", "IDNP", "Nume/Prenume", "Data angajării"])
    sheet.append([number, person["idnp"], f"Тестов {person['telegram_first_name']}", TODAY.strftime("%d.%m.%Y")])
    path = tmp_path / "qa-cim.xlsx"
    book.save(path)
    open_hrm(auth_page, "/admin/settings")
    main(auth_page).get_by_role("button", name="Импорт Nr. CIM", exact=True).click()
    expect(main(auth_page)).to_contain_text("«Nr. CIM», «IDNP», «Nume/Prenume», «Data angajării»")
    main(auth_page).locator("input[type=file]").set_input_files(str(path))
    with auth_page.expect_response(lambda r: "/contract-numbers/import-xlsx" in r.url) as dry:
        main(auth_page).get_by_role("button", name="Проверить", exact=True).click()
    assert dry.value.ok and "dry_run=true" in dry.value.url, (dry.value.status, dry.value.url, dry.value.text()[:300])
    periods = lambda: [p.get("contract_number") for p in items(hrm_json(hrm_get(auth_page, f"/employees/{person['id']}/employments")))]  # noqa: E731
    assert number not in periods(), "проверка уже записала номер"
    expect(main(auth_page)).to_contain_text("Будет заполнено 1")
    expect(main(auth_page)).to_contain_text("Ничего не изменено.")
    expect(main(auth_page)).to_contain_text(f"{person['telegram_first_name']} Тестов · {TODAY:%d.%m.%Y} → {number}")
    main(auth_page).get_by_role("button", name="Заполнить", exact=True).click()
    sure = auth_page.locator("div.fixed.inset-0:visible").last  # подтверждение записи номеров
    with auth_page.expect_response(lambda r: "/contract-numbers/import-xlsx" in r.url) as real:
        expect(sure).to_be_visible()
        expect(sure).to_contain_text("Заполнить номера договоров у 1 периодов?")
        sure.get_by_role("button", name=re.compile("^(Да|Заполнить|OK|Подтвердить)$")).last.click()
    assert real.value.ok and "dry_run=false" in real.value.url, (real.value.status, real.value.text()[:300])
    assert number in periods(), periods()


# ───────────────────────── плагины ─────────────────────────

def plugin_card(page: Page, title: str):
    return main(page).locator("div").filter(has=page.get_by_text(title, exact=True)).filter(has=page.locator("button")).last


# Кейс: Плагин «Управление арендой» — пункт меню, «Новое жильё», «Заселить» своего сотрудника; выключение возвращает как было
@pytest.mark.writes_data
def test_rent_plugin(auth_page: Page, person):
    before = hrm_json(hrm_get(auth_page, "/plugins"))
    rental_id = None
    try:
        if before["rent"]:
            hrm_put(auth_page, "/plugins", {**before, "rent": False})
        open_hrm(auth_page, "/admin/settings")
        main(auth_page).get_by_role("button", name="Плагины", exact=True).click()
        card = plugin_card(auth_page, "Управление арендой")
        expect(card).to_contain_text("Выключен")
        with auth_page.expect_response(lambda r: r.url.endswith("/api/hrm/plugins") and r.request.method == "PUT") as on:
            card.get_by_role("button").first.click()
        assert on.value.request.post_data_json["rent"] is True
        link = sidebar(auth_page).get_by_role("link", name="Управление арендой", exact=True)
        expect(link).to_have_attribute("href", "/admin/rentals")
        link.click()
        auth_page.locator("header button:visible").filter(has_text="Новое жильё").first.click()
        box = dialog(auth_page, "Новое жильё")
        address = f"{QA_PREFIX} ул. Тестовая {stamp()}"
        field(box, "Адрес *").fill(address)
        field(box, "Хозяин").fill("QA Хозяин")
        field(box, "Стоимость аренды в месяц").fill("6000")
        field(box, "Мест всего").fill("3")
        with auth_page.expect_response(lambda r: r.url.endswith("/api/hrm/rentals") and r.request.method == "POST") as cr:
            box.get_by_role("button", name="Сохранить", exact=True).click()
        assert cr.value.ok, cr.value.text()
        rental_id = cr.value.json()["id"]
        panel = auth_page.locator("div.fixed.inset-0:visible").filter(has_text=address).last
        expect(panel).to_contain_text("Мест: 3 · занято 0 · свободно 3")
        panel.get_by_role("button", name="Заселить", exact=True).first.click()
        panel.get_by_role("button", name="Выберите сотрудника").click()
        auth_page.get_by_role("option").filter(has_text=person["telegram_first_name"]).click()
        with auth_page.expect_response(lambda r: f"/rentals/{rental_id}/tenants" in r.url) as tn:
            panel.get_by_role("button", name="Заселить", exact=True).last.click()
        assert tn.value.ok and tn.value.json()["occupied"] == 1, tn.value.text()
        expect(panel).to_contain_text("Мест: 3 · занято 1 · свободно 2")
        expect(panel.locator("tr").filter(has_text=person["telegram_first_name"])).to_contain_text(f"{TODAY:%d.%m.%Y} — сейчас")
    finally:
        if rental_id:
            hrm_delete(auth_page, f"/rentals/{rental_id}")
        hrm_put(auth_page, "/plugins", before)
    open_hrm(auth_page, "/admin")
    expect(sidebar(auth_page).get_by_role("link", name="Управление арендой")).to_have_count(0 if not before["rent"] else 1)


# Кейс: Плагин «Табель из трекера HDS» — переключатель = /plugins, возвращается как было
@pytest.mark.writes_data
def test_hds_timesheet_plugin(auth_page: Page):
    before = hrm_json(hrm_get(auth_page, "/plugins"))
    try:
        open_hrm(auth_page, "/admin/settings")
        main(auth_page).get_by_role("button", name="Плагины", exact=True).click()
        card = plugin_card(auth_page, "Табель из трекера HDS")
        expect(card).to_contain_text("Включён" if before["hds_timesheet"] else "Выключен")
        with auth_page.expect_response(lambda r: r.url.endswith("/api/hrm/plugins") and r.request.method == "PUT") as sw:
            card.get_by_role("button").first.click()
        assert sw.value.request.post_data_json["hds_timesheet"] is (not before["hds_timesheet"])
        expect(card).to_contain_text("Выключен" if before["hds_timesheet"] else "Включён")
        assert hrm_json(hrm_get(auth_page, "/plugins"))["hds_timesheet"] is (not before["hds_timesheet"])
    finally:
        hrm_put(auth_page, "/plugins", before)
