"""Блок 58. HRM — компенсация (/admin/compensation) и заявки на повышение (/admin/raise-requests).

Снято 03.11.2026 в видимом MCP-браузере:
- «Компенсация»: вкладки «Грейды / Записи / По местам работы / Льготы / Подписки»;
- «Создать грейд»: «Название», «Уровень (число)», «Мин. зарплата», «Макс. зарплата», «Валюта» (MDL) →
  POST /compensation/bands; мин. больше макс. — окно «Минимальная зарплата грейда больше максимальной»
  (OK), запрос не уходит; карточка — h3 с названием, «Все позиции -- <уровень>», мин. и макс.
  («10 000 MDL»), значки «Редактировать» / «Удалить» → «Удалить грейд» («Вы уверены, что хотите
  удалить "<название>"?») → «Удалить»;
- «Записи» → «Добавить запись»: «Сотрудник», «Место работы (юрлицо)» (подставляется «Основное место
  работы — <юрлицо>»), «Грейд (необязательно)», «Сумма», «Валюта», «Действует с» (сегодня) →
  POST /compensation/records; строка «Оклад · 18 000 MDL · ежемесячно · ДД.ММ.ГГГГ · утвердил»;
  у строки только «Редактировать» (PUT, правка на месте) — удаления записи нет ни на экране, ни
  в API, записи уходят вместе со своим сотрудником (каскад);
- «По местам работы»: сотрудник + месяц/год → блок на каждое место работы (= /compensation/payroll),
  «Ставка не задана», «Часов за месяц», записи оплаты, примечание «Модуль не рассчитывает
  начисления…»;
- «Льготы» → «Создать льготу» («Название», «Описание», «Тип льготы», «Активна») →
  POST /compensation/benefits; экран показывает и неактивные (API по умолчанию — только активные);
  «Подписки» → «Записать сотрудника на льготу» («Сотрудник», «Льгота», «Дата начала») →
  POST /compensation/enrollments;
- «Повышение квалификации» (/admin/raise-requests — так подписан экран, по сути заявки на
  повышение зарплаты): в шапке счётчик, «Все статусы», «Новая заявка» → «Заявка на повышение»
  («Сотрудник» — «Я сам(а)» или любой, текущая и желаемая, «За период» в месяц/в час, валюта,
  «Аргументация … *», «Планы развития»); без аргументации — «Опишите, чего вы достигли — это
  основа заявки.» и запрос не уходит; карточка заявки: «Статус» (Новая / На рассмотрении /
  Одобрена / Отклонена), «Внутренние заметки», «Решение для сотрудника», «Сохранить» → PATCH,
  «История статусов» («Новая → Одобрена · <кто>»), «Удалить заявку» → «Удалить заявку насовсем,
  вместе с историей статусов?» → «Удалить» (204). Одобрение оклад не меняет.
Сумма вне вилки грейда принимается без предупреждения; грейд с пустым названием и 0–0 сервер
тоже создаёт (спорно, не заводили). Всё своё удаляется: каскад сотрудника уносит записи, заявки и подписки.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (QA_PREFIX, RAISE_MANAGER, pick_raise_manager, clean_up, create_employee, create_legal_entity, date_field, delete_employee,
                         dropdown, field, header_button, hrm_delete, hrm_get, hrm_json, hrm_patch, hrm_post, items,
                         open_hrm, screen_title, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()


def main(page: Page):
    return page.locator("main")


def tab(page: Page, name: str):
    main(page).get_by_role("button", name=name, exact=True).click()


def modal(page: Page, heading: str):
    return page.locator("div.fixed.inset-0:visible").filter(
        has=page.get_by_role("heading", name=heading, exact=True)).last


def band_card(page: Page, name: str):
    return main(page).locator("div").filter(has=page.get_by_role("heading", name=name, exact=True)).filter(
        has=page.get_by_role("button", name="Удалить")).filter(has_text="MDL").last  # шапка карточки — без сумм


def money(amount: int) -> str:
    """«18 000 MDL» — разделитель тысяч у экрана неразрывный (или узкий неразрывный) пробел."""
    return re.compile(rf"{amount // 1000}\s{amount % 1000:03d} MDL") if amount >= 1000 else re.compile(rf"{amount} MDL")


def remove_own(page: Page):
    for b in items(hrm_json(hrm_get(page, "/compensation/bands"))):
        if (b.get("name") or "").startswith(QA_PREFIX):
            hrm_delete(page, f"/compensation/bands/{b['id']}")
    for b in items(hrm_json(hrm_get(page, "/compensation/benefits", {"active_only": "false"}))):
        if b["name"].startswith(QA_PREFIX):
            for en in items(hrm_json(hrm_get(page, "/compensation/enrollments", {"benefit_id": b["id"]}))):
                hrm_delete(page, f"/compensation/enrollments/{en['id']}")
            hrm_delete(page, f"/compensation/benefits/{b['id']}")
    for rr in items(hrm_json(hrm_get(page, "/raise-requests"))):
        if (rr.get("employee_name") or "").startswith(QA_PREFIX):
            hrm_delete(page, f"/raise-requests/{rr['id']}")


@pytest.fixture
def comp(auth_page: Page):
    yield
    remove_own(auth_page)
    clean_up(auth_page)


@pytest.fixture
def person(auth_page: Page, comp):
    """Свой сотрудник на своём юрлице — {le, emp}; удаляется каскадом вместе с записями и заявками."""
    le = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Оклад {stamp()}")
    yield {"le": le, "emp": emp}
    delete_employee(auth_page, emp["id"])


# ───────────────────────── грейды ─────────────────────────

# Кейс: Грейд — мин. больше макс. не сохраняется; форма → карточка = API → «Редактировать» → «Удалить грейд»
@pytest.mark.writes_data
def test_band_form(auth_page: Page, comp):
    name = f"{QA_PREFIX} Грейд {stamp()}"
    posts = []
    auth_page.on("request", lambda r: posts.append(r.url) if "/compensation/bands" in r.url and r.method == "POST" else None)
    open_hrm(auth_page, "/admin/compensation")
    expect(screen_title(auth_page)).to_have_attribute("title", "Компенсация")
    main(auth_page).get_by_role("button", name="Создать грейд").click()
    box = modal(auth_page, "Создать грейд")
    field(box, "Название").fill(name)
    field(box, "Уровень (число)").fill("2")
    field(box, "Мин. зарплата").fill("20000")
    field(box, "Макс. зарплата").fill("10000")
    expect(field(box, "Валюта")).to_contain_text("MDL")
    box.get_by_role("button", name="Сохранить", exact=True).click()
    alert = auth_page.locator("div.fixed.inset-0:visible").filter(has_text="Минимальная зарплата грейда больше максимальной").last
    expect(alert).to_be_visible()
    alert.get_by_role("button", name="OK", exact=True).click()
    assert not posts, f"при мин. > макс. ушёл запрос: {posts}"

    field(box, "Мин. зарплата").fill("10000")
    field(box, "Макс. зарплата").fill("20000")
    with auth_page.expect_response(lambda r: r.url.endswith("/compensation/bands") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    band = next(b for b in hrm_json(hrm_get(auth_page, "/compensation/bands")) if b["id"] == resp.value.json()["id"])
    assert (band["name"], str(band["level"]), float(band["min_salary"]), float(band["max_salary"]), band["currency"]) == \
        (name, "2", 10000.0, 20000.0, "MDL"), band
    card = band_card(auth_page, name)
    expect(card).to_contain_text("Все позиции -- 2")
    expect(card).to_contain_text(money(10000))
    expect(card).to_contain_text(money(20000))

    card.get_by_role("button", name="Редактировать").click()
    box = auth_page.locator("div.fixed.inset-0:visible").filter(has=auth_page.get_by_role("button", name="Сохранить")).last
    expect(field(box, "Название")).to_have_value(name)
    field(box, "Макс. зарплата").fill("25000")
    with auth_page.expect_response(lambda r: f"/compensation/bands/{band['id']}" in r.url and r.request.method == "PUT") as upd:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert upd.value.ok, upd.value.text()
    expect(band_card(auth_page, name)).to_contain_text(money(25000))
    assert float(next(b for b in hrm_json(hrm_get(auth_page, "/compensation/bands")) if b["id"] == band["id"])["max_salary"]) == 25000.0

    band_card(auth_page, name).get_by_role("button", name="Удалить").click()
    dlg = modal(auth_page, "Удалить грейд")
    expect(dlg).to_contain_text(f'Вы уверены, что хотите удалить "{name}"?')
    with auth_page.expect_response(lambda r: f"/compensation/bands/{band['id']}" in r.url and r.request.method == "DELETE") as gone:
        dlg.get_by_role("button", name="Удалить", exact=True).click()
    assert gone.value.ok, gone.value.text()
    expect(main(auth_page).get_by_role("heading", name=name, exact=True)).to_have_count(0)
    assert band["id"] not in [b["id"] for b in hrm_json(hrm_get(auth_page, "/compensation/bands"))]


# ───────────────────────── записи оплаты ─────────────────────────

# Кейс: Запись оклада — форма с подставленным местом работы и грейдом = API, «Редактировать» правит сумму на месте
@pytest.mark.writes_data
def test_salary_record(auth_page: Page, person):
    emp, le = person["emp"], person["le"]
    band = hrm_json(hrm_post(auth_page, "/compensation/bands", {"name": f"{QA_PREFIX} Грейд записи {stamp()}", "level": "1",
                                                                "min_salary": 10000, "max_salary": 20000, "currency": "MDL"}))
    open_hrm(auth_page, "/admin/compensation")
    tab(auth_page, "Записи")
    main(auth_page).get_by_role("button", name="Добавить запись").click()
    box = modal(auth_page, "Добавить запись")
    dropdown(auth_page, box, "Сотрудник", emp["telegram_first_name"])
    expect(field(box, "Место работы (юрлицо)")).to_contain_text(f"Основное место работы — {le['name']}")
    dropdown(auth_page, box, "Грейд (необязательно)", band["name"])
    field(box, "Сумма").fill("15000")
    expect(date_field(box, "Действует с")).to_have_value(str(TODAY))
    with auth_page.expect_response(lambda r: r.url.endswith("/compensation/records") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    [rec] = hrm_json(hrm_get(auth_page, "/compensation/records", {"employee_id": emp["id"]}))
    assert (rec["id"], rec["record_type"], float(rec["amount"]), rec["currency"], rec["frequency"], rec["effective_from"],
            rec["band_id"], rec["legal_entity_id"], rec["employment_kind"]) == \
        (resp.value.json()["id"], "base_salary", 15000.0, "MDL", "monthly", str(TODAY), band["id"], le["id"], "main"), rec
    row = main(auth_page).locator("tr").filter(has_text=emp["telegram_first_name"])
    expect(row).to_have_count(1)
    for text in (le["name"], "Оклад", "ежемесячно", TODAY.strftime("%d.%m.%Y"), rec["approver_name"]):
        expect(row).to_contain_text(text)
    expect(row).to_contain_text(money(15000))

    row.get_by_role("button", name="Редактировать").click()
    box = modal(auth_page, "Редактировать запись")
    expect(field(box, "Сумма")).to_have_value("15000")
    expect(field(box, "Грейд (необязательно)")).to_contain_text(band["name"])
    field(box, "Сумма").fill("18000")
    with auth_page.expect_response(lambda r: f"/compensation/records/{rec['id']}" in r.url and r.request.method == "PUT") as upd:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert upd.value.ok, upd.value.text()
    expect(row).to_contain_text(money(18000))
    after = hrm_json(hrm_get(auth_page, "/compensation/records", {"employee_id": emp["id"]}))
    assert [(r["id"], float(r["amount"])) for r in after] == [(rec["id"], 18000.0)], after  # правка на месте, без новой записи
    expect(row.get_by_role("button", name=re.compile("Удалить"))).to_have_count(0)  # удаления записи нет


# Кейс: «По местам работы» — расчёт сотрудника за месяц = /compensation/payroll
@pytest.mark.writes_data
def test_payroll_by_employment(auth_page: Page, person):
    emp, le = person["emp"], person["le"]
    hrm_json(hrm_post(auth_page, "/compensation/records", {"employee_id": emp["id"], "amount": 12500, "currency": "MDL",
                                                           "effective_from": str(TODAY)}))
    payroll = hrm_json(hrm_get(auth_page, "/compensation/payroll", {"employee_id": emp["id"], "year": TODAY.year, "month": TODAY.month}))
    [job] = payroll["employments"]
    assert (job["kind"], job["legal_entity_id"], len(job["compensation_records"])) == ("main", le["id"], 1), job

    open_hrm(auth_page, "/admin/compensation")
    tab(auth_page, "По местам работы")
    expect(main(auth_page)).to_contain_text("Выберите сотрудника, чтобы увидеть расчёт по каждому месту работы")
    dropdown(auth_page, main(auth_page), "Сотрудник", emp["telegram_first_name"])
    block = main(auth_page).locator("div").filter(has_text=le["name"]).filter(has=auth_page.locator("table")).last
    expect(block).to_contain_text(job["title"])  # «Основное место работы»
    expect(block).to_contain_text("Ставка")
    expect(block).to_contain_text("не задана")
    expect(block.locator("tr").filter(has_text="Оклад")).to_contain_text(money(12500))
    expect(block).to_contain_text("Часов за месяц")
    expect(main(auth_page)).to_contain_text(payroll["scope_note"])


# ───────────────────────── льготы ─────────────────────────

# Кейс: Льгота — «Создать льготу», «Записать сотрудника на льготу» → подписка = API
@pytest.mark.writes_data
def test_benefit_enrollment(auth_page: Page, person):
    emp = person["emp"]
    name = f"{QA_PREFIX} Льгота {stamp()}"
    open_hrm(auth_page, "/admin/compensation")
    tab(auth_page, "Льготы")
    main(auth_page).get_by_role("button", name="Создать льготу").click()
    box = auth_page.locator("div.fixed.inset-0:visible").filter(has=auth_page.get_by_role("button", name="Сохранить")).last
    field(box, "Название").fill(name)
    field(box, "Описание").fill("QA: медицинская страховка")
    with auth_page.expect_response(lambda r: r.url.endswith("/compensation/benefits") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    benefit = next(b for b in hrm_json(hrm_get(auth_page, "/compensation/benefits")) if b["id"] == resp.value.json()["id"])
    assert (benefit["name"], benefit["description"], benefit["is_active"], benefit["enrollment_count"]) == \
        (name, "QA: медицинская страховка", True, 0), benefit
    card = main(auth_page).locator("div").filter(has_text=name).filter(has_text="подпис").last
    expect(card).to_contain_text("0 подписок")

    tab(auth_page, "Подписки")
    main(auth_page).get_by_role("button", name="Записать сотрудника").click()
    box = modal(auth_page, "Записать сотрудника на льготу")
    dropdown(auth_page, box, "Сотрудник", emp["telegram_first_name"])
    dropdown(auth_page, box, "Льгота", name)
    date_field(box, "Дата начала").fill(str(TODAY))
    with auth_page.expect_response(lambda r: r.url.endswith("/compensation/enrollments") and r.request.method == "POST") as en:
        box.get_by_role("button", name="Записать", exact=True).click()
    assert en.value.ok, en.value.text()
    [mine] = hrm_json(hrm_get(auth_page, "/compensation/enrollments", {"employee_id": emp["id"]}))
    assert (mine["benefit_id"], mine["enrolled_at"][:10]) == (benefit["id"], str(TODAY)), mine
    row = main(auth_page).locator("tr").filter(has_text=emp["telegram_first_name"])
    expect(row).to_contain_text(name)
    expect(row).to_contain_text(TODAY.strftime("%d.%m.%Y"))
    tab(auth_page, "Льготы")
    expect(main(auth_page).locator("div").filter(has_text=name).filter(has_text="подпис").last).to_contain_text("1 подпис")


# ───────────────────────── заявки на повышение ─────────────────────────

def rr_modal(page: Page, emp_name: str):  # карточка заявки — окно с h3 = имя сотрудника
    return modal(page, emp_name)


# Кейс: Заявка на повышение — без аргументации не уходит; «Новая» → «Одобрена» с заметками, история статусов, оклад не меняется; «Удалить заявку»
@pytest.mark.writes_data
def test_raise_request_flow(auth_page: Page, person):
    emp = person["emp"]
    hrm_json(hrm_post(auth_page, "/compensation/records", {"employee_id": emp["id"], "amount": 18000, "currency": "MDL",
                                                           "effective_from": str(TODAY)}))
    posts = []
    auth_page.on("request", lambda r: posts.append(r.url) if r.url.endswith("/raise-requests") and r.method == "POST" else None)
    open_hrm(auth_page, "/admin/raise-requests")
    header_button(auth_page, "Новая заявка").click()
    box = modal(auth_page, "Заявка на повышение")
    expect(field(box, "Сотрудник")).to_contain_text("Я сам(а)")
    pick_raise_manager(auth_page, box)
    dropdown(auth_page, box, "Сотрудник", emp["telegram_first_name"])
    field(box, "Текущая ставка / зарплата").fill("18000")
    field(box, "Желаемая ставка / зарплата").fill("21000")
    expect(field(box, "За период")).to_contain_text("в месяц")
    box.get_by_role("button", name="Отправить", exact=True).click()
    expect(box).to_contain_text("Опишите, чего вы достигли — это основа заявки.")
    assert not posts, f"заявка без аргументации ушла: {posts}"

    field(box, "Аргументация", starts=True).fill("QA: закрыл план квартала")
    with auth_page.expect_response(lambda r: r.url.endswith("/raise-requests") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Отправить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    rr = resp.value.json()
    assert (rr["employee_id"], rr["current_salary"], rr["desired_salary"], rr["salary_period"], rr["status"]) == \
        (emp["id"], 18000.0, 21000.0, "month", "new"), rr
    row = main(auth_page).locator("tr").filter(has_text=emp["telegram_first_name"])
    expect(row).to_contain_text("Новая")
    expect(row).to_contain_text(re.compile(r"18\s000 MDL/мес"))
    expect(row).to_contain_text(re.compile(r"21\s000 MDL/мес"))

    row.click()
    card = rr_modal(auth_page, emp["telegram_first_name"])
    expect(card).to_contain_text(f"Подана {TODAY.strftime('%d.%m.%Y')}")
    expect(card).to_contain_text("QA: закрыл план квартала")
    dropdown(auth_page, card, "Статус", "Одобрена")
    field(card, "Внутренние заметки (сотрудник не видит)").fill("QA внутреннее")
    field(card, "Решение для сотрудника (сотрудник видит)").fill("QA с декабря")
    with auth_page.expect_response(lambda r: f"/raise-requests/{rr['id']}" in r.url and r.request.method == "PATCH") as upd:
        card.get_by_role("button", name="Сохранить", exact=True).click()
    assert upd.value.ok, upd.value.text()
    got = hrm_json(hrm_get(auth_page, f"/raise-requests/{rr['id']}"))
    expect(card.locator("li").filter(has_text="Новая → Одобрена")).to_contain_text(got["history"][-1]["changed_by_name"])
    assert (got["status"], got["internal_notes"], got["decision_comment"]) == ("approved", "QA внутреннее", "QA с декабря"), got
    assert [h["to_status"] for h in got["history"]] == ["new", "approved"], got["history"]
    expect(row).to_contain_text("Одобрена")
    # одобрение само оклад не меняет — запись оплаты та же
    recs = hrm_json(hrm_get(auth_page, "/compensation/records", {"employee_id": emp["id"]}))
    assert [float(r["amount"]) for r in recs] == [18000.0], recs

    card.get_by_role("button", name="Удалить заявку").click()
    sure = auth_page.locator("div.fixed.inset-0:visible").filter(has_text="Удалить заявку насовсем, вместе с историей статусов?").last
    with auth_page.expect_response(lambda r: f"/raise-requests/{rr['id']}" in r.url and r.request.method == "DELETE") as gone:
        sure.get_by_role("button", name="Удалить", exact=True).click()
    assert gone.value.status == 204, gone.value.text()
    expect(row).to_have_count(0)
    assert hrm_get(auth_page, f"/raise-requests/{rr['id']}").status == 404


# Кейс: Список заявок = API — счётчик в шапке, фильтр «Все статусы» → «Отклонена»
@pytest.mark.writes_data
def test_raise_request_filter(auth_page: Page, person):
    emp = person["emp"]
    base = {"employee_id": emp["id"], "salary_period": "month", "currency": "MDL", "achievements": "QA",
            "manager_ids": [RAISE_MANAGER]}
    new = hrm_json(hrm_post(auth_page, "/raise-requests", {**base, "current_salary": 100, "desired_salary": 110}))
    rej = hrm_json(hrm_post(auth_page, "/raise-requests", {**base, "current_salary": 200, "desired_salary": 220}))
    hrm_json(hrm_patch(auth_page, f"/raise-requests/{rej['id']}", {"status": "rejected", "decision_comment": "QA нет"}))
    all_rr = items(hrm_json(hrm_get(auth_page, "/raise-requests")))
    rejected = items(hrm_json(hrm_get(auth_page, "/raise-requests", {"status": "rejected"})))
    assert rej["id"] in [r["id"] for r in rejected] and new["id"] not in [r["id"] for r in rejected], rejected

    open_hrm(auth_page, "/admin/raise-requests")
    expect(screen_title(auth_page)).to_contain_text(str(len(all_rr)))
    expect(main(auth_page).locator("tbody tr")).to_have_count(len(all_rr))
    mine = main(auth_page).locator("tr").filter(has_text=emp["telegram_first_name"])
    expect(mine).to_have_count(2)

    status = header_button(auth_page, "Все статусы")
    opt = auth_page.get_by_role("option", name="Отклонена", exact=True)
    for _ in range(3):
        status.click()
        try:
            expect(opt).to_be_visible(timeout=2_000)
            break
        except AssertionError:
            pass
    with auth_page.expect_response(lambda r: "/raise-requests" in r.url and "status=rejected" in r.url):
        opt.click()
    expect(main(auth_page).locator("tbody tr")).to_have_count(len(rejected))
    expect(mine).to_have_count(1)
    expect(mine).to_contain_text("Отклонена")
    expect(mine).to_contain_text(re.compile(r"200 MDL/мес"))
