"""Блок 59. HRM — дисциплина (/admin/discipline): типы нарушений, нарушения, испытательные сроки, аудит.

Снято 03.11.2026 в видимом MCP-браузере:
- вкладки «Испытательный срок / Нарушения / Типы нарушений / Аудит»;
- «Типы нарушений» — «Каталог типов нарушений», колонки «Код, Название, Тяжесть по умолч., Системный,
  Активен, Действия»; «Добавить тип» → «Новый тип нарушения»: «Код» (tardiness), «Название», «Описание»,
  «Тяжесть по умолчанию» (Незначительное / Среднее / Серьёзное / Критическое), «Порядок» →
  POST /discipline/violation-types; в строке значки «Деактивировать / Редактировать / Удалить»;
  «Удалить» → «Удалить тип "<название>"?» → «Да» — с 04.11 тип удаляется совсем (до этого удаление было
  мягким: is_active=false, код занят навсегда); каталог показывает только активные;
  «Деактивировать» шлёт PUT {is_active:false} → тип неактивен (bord-51 починен 04.11);
- «Нарушения»: список слева (сотрудник, тяжесть, КОД типа, дата), «Создать» → «Новое нарушение»:
  «Сотрудник», «Тип нарушения» (подставляет тяжесть типа), «Тяжесть», «Дата инцидента» (сегодня),
  «Описание»; без сотрудника — «Выберите сотрудника.», без описания — «Заполните описание
  нарушения.» (запрос не уходит) → POST /discipline/violations/; карточка: шаги «Зафиксировано
  (дата — кто) / Объяснительная / Решение / Подписание», «Тип нарушения», «Дата инцидента»,
  «Решение» (сырое pending), «Описание»; значки «Редактировать» и «Отменить нарушение»;
  «Редактировать» → «Сохранить» всегда 500 (любая дата в PUT) — bord-50; «Отменить нарушение» →
  «Причина отмены *» («Отменить» неактивна без причины) → POST …/cancel {cancel_reason}, к описанию
  дописывается «--- Отменено --- Причина: …»; объяснительную, решение и подписи экран не ведёт;
  решение (resolution) берёт номер из журнала документов — не трогаем;
- «Испытательный срок»: фильтр «Все / Активный / Пройден / Не пройден / Продлён», «Создать» →
  «Новый испытательный срок»: «Сотрудник», «Начало» (сегодня), «Окончание» (пусто — считает сервер),
  «Длительность (мес.)» 3 → POST /discipline/trials/ → «Активный», «ДД.ММ.ГГГГ — ДД.ММ.ГГГГ (3 мес.)»;
  «Редактировать ИС» — «Продлить до» (+ «Причина продления») → PUT extended_to; сервер считает
  effective_end_date, но статус остаётся «Активный», а экран продление не показывает (спорно);
- «Аудит» — «Журнал изменений» (Все / Нарушения / Увольнения): «Нарушение зафиксировано: <код>,
  серьёзность: …», «Нарушение отменено: <причина>».
Удаления нарушений и испытательных сроков нет — уходят каскадом вместе со своим сотрудником.
Увольнения (/discipline/dismissals) не трогаем: «Деактивировать доступ» отключает учётки.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (QA_PREFIX, clean_up, create_employee, create_legal_entity, date_field, delete_employee,
                         dropdown, field, hrm_delete, hrm_get, hrm_json, hrm_post, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()
SEVERITY = {"minor": "Незначительное", "moderate": "Среднее", "major": "Серьёзное", "critical": "Критическое"}


def main(page: Page):
    return page.locator("main")


def tab(page: Page, name: str):
    main(page).get_by_role("button", name=name, exact=True).click()


def modal(page: Page, heading: str):
    return page.locator("div.fixed.inset-0:visible").filter(
        has=page.get_by_role("heading", name=heading, exact=True)).last


def ddmmyyyy(iso: str) -> str:
    return dt.date.fromisoformat(iso[:10]).strftime("%d.%m.%Y")


def code() -> str:
    return "qa_" + re.sub(r"\D", "", stamp())[-9:]


def type_row(page: Page, vt_code: str):
    return main(page).locator("tbody tr").filter(has_text=vt_code)


def get_type(page: Page, vt_id: int) -> dict | None:
    return next((t for t in hrm_json(hrm_get(page, "/discipline/violation-types", {"active_only": "false"}))
                 if t["id"] == vt_id), None)


def violations_of(page: Page, emp_id: int) -> list:
    return items(hrm_json(hrm_get(page, "/discipline/violations/", {"employee_id": emp_id})))


def remove_own_types(page: Page):
    """Убрать свои активные типы из каталога (удаление мягкое — неактивные остаются в API)."""
    for t in hrm_json(hrm_get(page, "/discipline/violation-types")):
        if t["name"].startswith(QA_PREFIX) and not t["is_system"]:
            hrm_delete(page, f"/discipline/violation-types/{t['id']}")


@pytest.fixture
def discipline(auth_page: Page):
    yield
    remove_own_types(auth_page)
    clean_up(auth_page)


@pytest.fixture
def person(auth_page: Page, discipline):
    """Свой сотрудник — нарушения и испытательные сроки уходят с ним каскадом."""
    le = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Дисциплина {stamp()}")
    yield emp
    delete_employee(auth_page, emp["id"])


@pytest.fixture
def vtype(auth_page: Page, discipline):
    return hrm_json(hrm_post(auth_page, "/discipline/violation-types", {
        "code": code(), "name": f"{QA_PREFIX} Опоздание {stamp()}", "severity_default": "major"}))


# ───────────────────────── типы нарушений ─────────────────────────

# Кейс: Тип нарушения — форма → строка = API → «Редактировать» → «Удалить тип "…"?» → «Да»
@pytest.mark.writes_data
def test_violation_type_crud(auth_page: Page, discipline):
    vt_code, name = code(), f"{QA_PREFIX} Тип {stamp()}"
    open_hrm(auth_page, "/admin/discipline")
    tab(auth_page, "Типы нарушений")
    expect(main(auth_page)).to_contain_text("Каталог типов нарушений")
    main(auth_page).get_by_role("button", name="Добавить тип").click()
    box = modal(auth_page, "Новый тип нарушения")
    field(box, "Код").fill(vt_code)
    field(box, "Название").fill(name)
    field(box, "Описание").fill("QA: пришёл позже начала смены")
    expect(field(box, "Тяжесть по умолчанию")).to_contain_text("Незначительное")
    dropdown(auth_page, box, "Тяжесть по умолчанию", "Серьёзное")
    with auth_page.expect_response(lambda r: r.url.endswith("/discipline/violation-types") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    vt = get_type(auth_page, resp.value.json()["id"])
    assert (vt["code"], vt["name"], vt["description"], vt["severity_default"], vt["is_system"], vt["is_active"]) == \
        (vt_code, name, "QA: пришёл позже начала смены", "major", False, True), vt
    row = type_row(auth_page, vt_code)
    expect(row).to_have_count(1)
    expect(row).to_contain_text(name)
    expect(row).to_contain_text("Серьёзное")

    row.get_by_role("button", name="Редактировать").click()
    box = auth_page.locator("div.fixed.inset-0:visible").filter(has=auth_page.get_by_role("button", name="Сохранить")).last
    expect(field(box, "Код")).to_have_value(vt_code)
    field(box, "Название").fill(name + " изм")
    with auth_page.expect_response(lambda r: f"/violation-types/{vt['id']}" in r.url and r.request.method == "PUT") as upd:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert upd.value.ok, upd.value.text()
    expect(row).to_contain_text(name + " изм")
    assert get_type(auth_page, vt["id"])["name"] == name + " изм"

    row.get_by_role("button", name="Удалить").click()
    sure = auth_page.locator("div.fixed.inset-0:visible").filter(has_text=f'Удалить тип "{name} изм"?').last
    with auth_page.expect_response(lambda r: f"/violation-types/{vt['id']}" in r.url and r.request.method == "DELETE") as gone:
        sure.get_by_role("button", name="Да", exact=True).click()
    assert gone.value.ok, gone.value.text()
    expect(row).to_have_count(0)
    # с 04.11 удаление настоящее: типа нет даже в списке с неактивными (раньше оставался is_active=false)
    assert get_type(auth_page, vt["id"]) is None
    assert vt["id"] not in [t["id"] for t in hrm_json(hrm_get(auth_page, "/discipline/violation-types"))]


# Кейс: «Деактивировать» тип нарушения — тип становится неактивным
@pytest.mark.writes_data
def test_violation_type_deactivate(auth_page: Page, vtype):
    open_hrm(auth_page, "/admin/discipline")
    tab(auth_page, "Типы нарушений")
    with auth_page.expect_response(lambda r: f"/violation-types/{vtype['id']}" in r.url and r.request.method == "PUT") as off:
        type_row(auth_page, vtype["code"]).get_by_role("button", name="Деактивировать").click()
    assert off.value.ok, off.value.text()
    assert get_type(auth_page, vtype["id"])["is_active"] is False
    auth_page.reload()
    tab(auth_page, "Типы нарушений")
    expect(type_row(auth_page, vtype["code"]).get_by_role("button", name="Деактивировать")).to_have_count(0)


# ───────────────────────── нарушения ─────────────────────────

# Кейс: Нарушение — проверки формы, тип подставляет тяжесть, карточка = API; «Отменить нарушение» с причиной; запись в «Аудите»
@pytest.mark.writes_data
def test_violation_create_and_cancel(auth_page: Page, person, vtype):
    emp = person
    posts = []
    auth_page.on("request", lambda r: posts.append(r.url) if r.url.endswith("/discipline/violations/") and r.method == "POST" else None)
    open_hrm(auth_page, "/admin/discipline")
    tab(auth_page, "Нарушения")
    main(auth_page).get_by_role("button", name="Создать", exact=True).click()
    box = modal(auth_page, "Новое нарушение")
    expect(field(box, "Тяжесть")).to_contain_text("Незначительное")
    dropdown(auth_page, box, "Тип нарушения", vtype["name"])
    expect(field(box, "Тяжесть")).to_contain_text("Серьёзное")  # из типа
    expect(date_field(box, "Дата инцидента")).to_have_value(str(TODAY))
    box.get_by_role("button", name="Создать", exact=True).click()
    expect(box).to_contain_text("Выберите сотрудника.")
    dropdown(auth_page, box, "Сотрудник", emp["telegram_first_name"])
    box.get_by_role("button", name="Создать", exact=True).click()
    expect(box).to_contain_text("Заполните описание нарушения.")
    assert not posts, f"неполная форма ушла: {posts}"

    field(box, "Описание").fill(f"{QA_PREFIX} опоздал на 40 минут")
    with auth_page.expect_response(lambda r: r.url.endswith("/discipline/violations/") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    [v] = violations_of(auth_page, emp["id"])
    assert (v["id"], v["violation_type"], v["violation_type_id"], v["severity"], v["incident_date"], v["resolution"]) == \
        (resp.value.json()["id"], vtype["code"], vtype["id"], "major", str(TODAY), "pending"), v
    item = main(auth_page).get_by_role("button").filter(has_text=emp["telegram_first_name"])
    expect(item).to_have_count(1)
    expect(item).to_contain_text("Серьёзное")
    expect(item).to_contain_text(TODAY.strftime("%d.%m.%Y"))

    item.click()
    expect(main(auth_page)).to_contain_text(f"{TODAY.strftime('%d.%m.%Y')} — {v['reporter_name']}")  # шаг «Зафиксировано»
    for step in ("Зафиксировано", "Объяснительная", "Решение", "Подписание", f"{QA_PREFIX} опоздал на 40 минут"):
        expect(main(auth_page)).to_contain_text(step)

    main(auth_page).get_by_role("button", name="Отменить нарушение").click()
    dlg = modal(auth_page, "Отменить нарушение")
    expect(dlg.get_by_role("button", name="Отменить", exact=True)).to_be_disabled()
    reason = f"{QA_PREFIX} заведено ошибочно {stamp()}"
    dlg.locator("textarea").fill(reason)
    with auth_page.expect_response(lambda r: f"/violations/{v['id']}/cancel" in r.url) as cancel:
        dlg.get_by_role("button", name="Отменить", exact=True).click()
    assert cancel.value.ok, cancel.value.text()
    assert cancel.value.request.post_data_json == {"cancel_reason": reason}
    got = hrm_json(hrm_get(auth_page, f"/discipline/violations/{v['id']}"))
    assert got["resolution"] == "cancelled" and f"--- Отменено ---\nПричина: {reason}" in got["description"], got

    tab(auth_page, "Аудит")
    expect(main(auth_page)).to_contain_text("Журнал изменений")
    expect(main(auth_page)).to_contain_text(f"Нарушение отменено: {reason}")
    # с 08.11 в аудите — название типа, а не его код
    expect(main(auth_page)).to_contain_text(f"Нарушение зафиксировано: {vtype['name']}, серьёзность: серьёзное")


# Кейс: «Редактировать нарушение» → «Сохранить» сохраняет правку
@pytest.mark.writes_data
def test_violation_edit(auth_page: Page, person, vtype):
    hrm_json(hrm_post(auth_page, "/discipline/violations/", {
        "employee_id": person["id"], "violation_type": vtype["code"], "violation_type_id": vtype["id"],
        "severity": "major", "description": f"{QA_PREFIX} для правки", "incident_date": str(TODAY)}))
    open_hrm(auth_page, "/admin/discipline")
    tab(auth_page, "Нарушения")
    main(auth_page).get_by_role("button").filter(has_text=person["telegram_first_name"]).click()
    main(auth_page).get_by_role("button", name="Редактировать", exact=True).click()
    box = modal(auth_page, "Редактировать нарушение")
    dropdown(auth_page, box, "Тяжесть", "Среднее")
    with auth_page.expect_response(lambda r: "/discipline/violations/" in r.url and r.request.method == "PUT") as upd:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert upd.value.ok, f"{upd.value.status} {upd.value.text()[:200]}"
    assert violations_of(auth_page, person["id"])[0]["severity"] == "moderate"


# ───────────────────────── испытательный срок ─────────────────────────

# Кейс: Испытательный срок — 3 мес. от сегодня, окончание считает сервер; фильтр статусов; «Продлить до»
@pytest.mark.writes_data
def test_trial_period(auth_page: Page, person):
    emp = person
    open_hrm(auth_page, "/admin/discipline")
    expect(main(auth_page).get_by_role("button", name="Испытательный срок", exact=True)).to_be_visible()
    main(auth_page).get_by_role("button", name="Создать", exact=True).click()
    box = modal(auth_page, "Новый испытательный срок")
    dropdown(auth_page, box, "Сотрудник", emp["telegram_first_name"])
    expect(date_field(box, "Начало")).to_have_value(str(TODAY))
    expect(field(box, "Длительность (мес.)")).to_have_value("3")
    with auth_page.expect_response(lambda r: r.url.endswith("/discipline/trials/") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    trial = next(t for t in items(hrm_json(hrm_get(auth_page, "/discipline/trials/"))) if t["employee_id"] == emp["id"])
    assert (trial["start_date"], trial["end_date"], trial["duration_months"], trial["status"]) == \
        (str(TODAY), resp.value.json()["end_date"], 3, "active"), trial
    assert dt.date.fromisoformat(trial["end_date"]) > TODAY + dt.timedelta(days=85), trial
    item = main(auth_page).get_by_role("button").filter(has_text=emp["telegram_first_name"])
    expect(item).to_contain_text("Активный")
    expect(item).to_contain_text(f"{TODAY.strftime('%d.%m.%Y')} — {ddmmyyyy(trial['end_date'])} (3 мес.)")

    main(auth_page).get_by_role("button", name="Пройден", exact=True).click()
    expect(item).to_have_count(0)
    main(auth_page).get_by_role("button", name="Активный", exact=True).click()
    expect(item).to_have_count(1)

    item.click()
    expect(main(auth_page)).to_contain_text("Длительность")
    main(auth_page).get_by_role("button", name="Редактировать", exact=True).click()
    box = modal(auth_page, "Редактировать ИС")
    expect(date_field(box, "Окончание")).to_have_value(trial["end_date"])
    extended = str(dt.date.fromisoformat(trial["end_date"]) + dt.timedelta(days=28))
    date_field(box, "Продлить до").fill(extended)
    expect(field(box, "Причина продления")).to_be_visible()  # появляется после даты продления
    field(box, "Причина продления").fill(f"{QA_PREFIX} нужен ещё месяц")
    with auth_page.expect_response(lambda r: f"/discipline/trials/{trial['id']}" in r.url and r.request.method == "PUT") as upd:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert upd.value.ok, upd.value.text()
    got = next(t for t in items(hrm_json(hrm_get(auth_page, "/discipline/trials/"))) if t["id"] == trial["id"])
    assert (got["extended_to"], got["effective_end_date"]) == (extended, extended), got
