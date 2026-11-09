"""Блок 61. HRM — адаптация (/admin/onboarding), навыки (/admin/skills), обучение (/admin/training).

Снято 03.11.2026 в видимом MCP-браузере:
- «Адаптация»: плитки «Активных / Завершено в этом месяце / Просрочено / Средний прогресс», вкладки
  «Активные процессы / Шаблоны», блок «Пробный период»; «Создать шаблон» → «Новый шаблон»:
  «Название *», «Тип» (Адаптация / Offboarding), «Описание», «Триггер» (Вручную / При приёме / При
  увольнении) → POST /onboard/templates; тест ставит только «Вручную» — иначе шаблон сам запустится
  для настоящих сотрудников; карточка шаблона: «Адаптация -- N задач», «Активен», значки «Редактировать /
  Удалить»; «Добавить задачу» — встроенная форма: «Название задачи *» («Добавить» неактивна без него),
  «Описание», категория (Документы / Оборудование / Обучение / IT настройка / Знакомство / Доступы),
  «Дней», «Тип задачи» (Обычная / Документ / ТМЦ), «Обязательная» → POST …/tasks → «+3д.»;
  «Запустить онбординг» → «Сотрудник *» (варианты «undefined null (логин)» — bord-54), «Шаблон *»
  (только активные), «Дата начала» → POST
  /onboard/instances → «Активен · <шаблон> -- с ДД.ММ.ГГГГ · 0%»; процесс → «Задачи (N)», задача
  «Ожидает» со сроком старт+N дней, значок слева → «Изменить статус: Выполнено / Пропустить» →
  PUT /onboard/tasks/<id>/status → в списке «Завершён · 100%» (правая панель до перезагрузки старая);
  шаблон с задачами не удаляется (500, окно висит) — bord-52, уборка: сначала задачи, потом шаблон;
- «Навыки»: вкладки «Каталог / Матрица / Сертификаты / Обучение / Прогресс / Карьерные пути»;
  «Добавить навык» → «Название *», «Категория» (Технический / Soft Skills / Языки / Сертификация /
  Другое), «Описание» → POST /skills/catalog, каталог сгруппирован по категориям; «Матрица» →
  «Назначить навык» → «Сотрудник *», «Навык *», «Уровень владения» (пять звёзд; пока навык не выбран,
  подписан «Средний», выбор навыка сбрасывает на «Начинающий»), «Заметки» → POST /skills/assignments
  {proficiency: 1}; строка — «Изменить уровень» (4 звезды = «Продвинутый», PUT) и «Снять навык» →
  «Снять навык с сотрудника? Запись из матрицы будет удалена. Сам навык останется в каталоге.» → «Снять»;
- «Обучение»: вкладки «Обзор / Программы / Записи / Удержания / Обязательства»; «Новая программа» →
  «Новая программа обучения»: «Название», «Описание», «Тип» (Внутреннее / Внешнее / Онлайн /
  Сертификация), «Провайдер», «Стоимость», «Валюта», «Макс. участников» (20), даты → POST
  /training/programs; выбранный тип теряется (форма шлёт type, сервер ждёт training_type) — bord-53;
  карточка «— <провайдер> · 1 500 MDL · N участников», значки «Записать сотрудника / Редактировать /
  Удалить»; «Записать сотрудника» → POST …/enroll → во вкладке «Записи» строка «Зачислен».
  Доли компании/сотрудника в записи по программе нулевые (спорно, спросить), «Удержания» не трогаем.
Свои сотрудники удаляются каскадом вместе с процессами, навыками и записями обучения.
"""
import datetime as dt
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (QA_PREFIX, clean_up, create_employee, create_legal_entity, delete_employee, dropdown, field,
                         hrm_delete, hrm_get, hrm_json, hrm_post, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth

TODAY = dt.date.today()


def main(page: Page):
    return page.locator("main")


def tab(page: Page, name: str):
    main(page).get_by_role("button", name=name, exact=True).click()


def modal(page: Page, heading: str):
    return page.locator("div.fixed.inset-0:visible").filter(
        has=page.get_by_role("heading", name=heading, exact=True)).last


def delete_template(page: Page, tpl_id: int):
    """Шаблон с задачами DELETE не переживает (500, bord-52) — сначала задачи."""
    full = hrm_get(page, f"/onboard/templates/{tpl_id}")
    if full.ok:
        for task in full.json().get("tasks", []):
            hrm_delete(page, f"/onboard/templates/tasks/{task['id']}")
        hrm_delete(page, f"/onboard/templates/{tpl_id}")


def remove_own(page: Page):
    for t in hrm_json(hrm_get(page, "/onboard/templates", {"active_only": "false"})):
        if t["name"].startswith(QA_PREFIX):
            delete_template(page, t["id"])
    for s in items(hrm_json(hrm_get(page, "/skills/catalog"))):
        if s["name"].startswith(QA_PREFIX):
            hrm_delete(page, f"/skills/catalog/{s['id']}")
    for p in hrm_json(hrm_get(page, "/training/programs", {"active_only": "false"})):
        if p["title"].startswith(QA_PREFIX):
            hrm_delete(page, f"/training/programs/{p['id']}")


@pytest.fixture
def dev(auth_page: Page):
    yield
    remove_own(auth_page)
    clean_up(auth_page)


@pytest.fixture
def person(auth_page: Page, dev):
    le = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Развитие {stamp()}")
    yield emp
    delete_employee(auth_page, emp["id"])


def template_card(page: Page, name: str):
    """Карточка шаблона — родитель шапки (название, «Редактировать / Удалить»): в ней задачи и форма задачи."""
    return main(page).locator("div").filter(has=page.get_by_role("heading", name=name, exact=True)).filter(
        has=page.get_by_role("button", name="Удалить", exact=True)).last.locator("xpath=..")


# ───────────────────────── адаптация ─────────────────────────

# Кейс: Шаблон адаптации с задачей → «Запустить онбординг» своему сотруднику → задача «Выполнено» → процесс «Завершён · 100%»
@pytest.mark.writes_data
def test_onboarding_template_and_process(auth_page: Page, person):
    emp, name, task = person, f"{QA_PREFIX} Шаблон {stamp()}", f"{QA_PREFIX} Выдать ноутбук"
    open_hrm(auth_page, "/admin/onboarding")
    for tile in ("Активных", "Завершено в этом месяце", "Просрочено", "Средний прогресс"):
        expect(main(auth_page)).to_contain_text(tile)
    tab(auth_page, "Шаблоны")
    main(auth_page).get_by_role("button", name="Создать шаблон").click()
    box = modal(auth_page, "Новый шаблон")
    field(box, "Название *").fill(name)
    expect(field(box, "Тип")).to_contain_text("Адаптация")
    expect(field(box, "Триггер")).to_contain_text("Вручную")  # «При приёме» запустил бы его для настоящих сотрудников
    with auth_page.expect_response(lambda r: r.url.endswith("/onboard/templates") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    assert resp.value.request.post_data_json["trigger_event"] == "manual"
    tpl_id = resp.value.json()["id"]
    card = template_card(auth_page, name)
    expect(card).to_contain_text("Адаптация · 0 задач")  # до 08.11 — « -- »
    expect(card).to_contain_text("Активен")

    card.get_by_role("button", name="Добавить задачу").click()
    expect(card.get_by_role("button", name="Добавить", exact=True)).to_be_disabled()
    card.get_by_placeholder("Название задачи *").fill(task)
    card.get_by_placeholder("Дней", exact=True).fill("3")
    expect(card.get_by_role("checkbox", name="Обязательная")).to_be_checked()
    with auth_page.expect_response(lambda r: f"/onboard/templates/{tpl_id}/tasks" in r.url) as tr:
        card.get_by_role("button", name="Добавить", exact=True).click()
    assert tr.value.ok, tr.value.text()
    body = tr.value.request.post_data_json
    assert (body["title"], body["due_offset_days"], body["is_required"], body["task_type"]) == (task, 3, True, "regular"), body
    expect(card).to_contain_text("Адаптация · 1 задач")
    expect(card).to_contain_text(task)
    expect(card).to_contain_text("+3д.")

    tab(auth_page, "Активные процессы")
    main(auth_page).get_by_role("button", name="Запустить онбординг").click()
    box = modal(auth_page, "Запустить онбординг")
    field(box, "Сотрудник *").click()  # варианты подписаны «undefined (<имя>)» — bord-54, берём по подстроке
    auth_page.get_by_role("option").filter(has_text=emp["telegram_first_name"]).click()
    dropdown(auth_page, box, "Шаблон *", name)
    with auth_page.expect_response(lambda r: r.url.endswith("/onboard/instances") and r.request.method == "POST") as run:
        box.get_by_role("button", name="Запустить", exact=True).click()
    assert run.value.ok, run.value.text()
    assert run.value.request.post_data_json == {"employee_id": emp["id"], "template_id": tpl_id, "start_date": str(TODAY)}
    [inst] = hrm_json(hrm_get(auth_page, "/onboard/instances", {"employee_id": emp["id"]}))
    item = main(auth_page).get_by_role("button").filter(has_text=emp["telegram_first_name"])
    expect(item).to_contain_text(f"{name} · с {TODAY.strftime('%d.%m.%Y')}")
    expect(item).to_contain_text("Активен")
    expect(item).to_contain_text("0%")

    item.click()
    expect(main(auth_page)).to_contain_text("Задачи (1)")
    line = main(auth_page).locator("div").filter(has_text=task).filter(has=auth_page.locator("xpath=./button")).last
    expect(line).to_contain_text("Ожидает")
    expect(line).to_contain_text((TODAY + dt.timedelta(days=3)).strftime("%d.%m.%Y"))
    line.locator("xpath=./button").click()
    expect(main(auth_page)).to_contain_text("Изменить статус")
    with auth_page.expect_response(lambda r: "/onboard/tasks/" in r.url and r.url.endswith("/status")) as st:
        main(auth_page).get_by_role("button", name="Выполнено", exact=True).click()
    assert st.value.ok and st.value.json()["progress"] == 100, st.value.text()
    assert st.value.request.post_data_json == {"status": "completed"}
    expect(item).to_contain_text("Завершён")
    expect(item).to_contain_text("100%")
    got = next(i for i in hrm_json(hrm_get(auth_page, "/onboard/instances", {"employee_id": emp["id"]})) if i["id"] == inst["id"])
    assert got["status"] == "completed", got


# Кейс: «Запустить онбординг» — сотрудники в выпадашке подписаны своими именами
def test_onboarding_employee_options(auth_page: Page, person):
    open_hrm(auth_page, "/admin/onboarding")
    main(auth_page).get_by_role("button", name="Запустить онбординг").click()
    box = modal(auth_page, "Запустить онбординг")
    field(box, "Сотрудник *").click()
    options = auth_page.get_by_role("option")
    expect(options.filter(has_text=person["telegram_first_name"])).to_have_count(1)
    assert "undefined" not in " ".join(options.all_inner_texts())
    expect(auth_page.get_by_role("option", name=person["telegram_first_name"], exact=True)).to_be_visible()


# Кейс: «Удалить» шаблон адаптации с задачами → «Подтвердите удаление» → шаблон удалён
@pytest.mark.writes_data
def test_onboarding_template_delete(auth_page: Page, dev):
    name = f"{QA_PREFIX} Шаблон удаление {stamp()}"
    tpl = hrm_json(hrm_post(auth_page, "/onboard/templates", {"name": name, "trigger_event": "manual"}))
    hrm_json(hrm_post(auth_page, f"/onboard/templates/{tpl['id']}/tasks", {"title": f"{QA_PREFIX} задача"}))
    open_hrm(auth_page, "/admin/onboarding")
    tab(auth_page, "Шаблоны")
    template_card(auth_page, name).get_by_role("button", name="Удалить", exact=True).click()
    sure = modal(auth_page, "Подтвердите удаление")
    expect(sure).to_contain_text(f"Вы уверены, что хотите удалить шаблон {name}?")
    with auth_page.expect_response(lambda r: f"/onboard/templates/{tpl['id']}" in r.url and r.request.method == "DELETE") as gone:
        sure.get_by_role("button", name="Удалить", exact=True).click()
    assert gone.value.ok, f"{gone.value.status} {gone.value.text()[:200]}"
    expect(main(auth_page).get_by_role("heading", name=name, exact=True)).to_have_count(0)


# ───────────────────────── навыки ─────────────────────────

# Кейс: Навык — каталог по категориям; «Назначить навык» сотруднику, «Изменить уровень» звёздами, «Снять навык» (в каталоге остаётся)
@pytest.mark.writes_data
def test_skill_catalog_and_matrix(auth_page: Page, person):
    emp, name = person, f"{QA_PREFIX} Румынский {stamp()}"
    open_hrm(auth_page, "/admin/skills")
    main(auth_page).get_by_role("button", name="Добавить навык").click()
    box = modal(auth_page, "Добавить навык")
    field(box, "Название *").fill(name)
    expect(field(box, "Категория")).to_contain_text("Технический")
    dropdown(auth_page, box, "Категория", "Языки")
    with auth_page.expect_response(lambda r: r.url.endswith("/skills/catalog") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    skill = next(s for s in items(hrm_json(hrm_get(auth_page, "/skills/catalog"))) if s["id"] == resp.value.json()["id"])
    assert (skill["name"], skill["category"]) == (name, "language"), skill
    expect(main(auth_page)).to_contain_text(re.compile("Языки", re.I))
    expect(main(auth_page)).to_contain_text(name)

    tab(auth_page, "Матрица")
    main(auth_page).get_by_role("button", name="Назначить навык").click()
    box = modal(auth_page, "Назначить навык")
    dropdown(auth_page, box, "Сотрудник *", emp["telegram_first_name"])
    dropdown(auth_page, box, "Навык *", f"{name} Языки")
    shown = field(box, "Уровень владения").inner_text().strip()  # что форма показывает на момент сохранения
    with auth_page.expect_response(lambda r: r.url.endswith("/skills/assignments") and r.request.method == "POST") as asg:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert asg.value.ok, asg.value.text()
    sent = asg.value.request.post_data_json
    assert (sent["employee_id"], sent["skill_id"]) == (emp["id"], skill["id"]), sent
    row = main(auth_page).locator("tbody tr").filter(has_text=emp["telegram_first_name"])
    expect(row).to_contain_text(name)
    expect(row).to_contain_text(shown)  # сохранился тот уровень, что был подписан в форме

    row.get_by_role("button", name="Изменить уровень").click()
    box = modal(auth_page, "Изменить уровень навыка")
    expect(field(box, "Сотрудник *").locator("button")).to_be_disabled()
    field(box, "Уровень владения").locator("button").nth(3).click()  # 4-я звезда
    expect(box).to_contain_text("Продвинутый")
    with auth_page.expect_response(lambda r: "/skills/assignments/" in r.url and r.request.method == "PUT") as upd:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert upd.value.ok and upd.value.request.post_data_json["proficiency"] == 4, upd.value.text()
    expect(row).to_contain_text("Продвинутый")
    [entry] = items(hrm_json(hrm_get(auth_page, "/skills/assignments", {"employee_id": emp["id"]})))
    assert (entry["skill_id"], entry["proficiency"]) == (skill["id"], 4), entry

    row.get_by_role("button", name="Снять навык").click()
    sure = auth_page.locator("div.fixed.inset-0:visible").filter(has_text="Снять навык с сотрудника?").last
    expect(sure).to_contain_text("Сам навык останется в каталоге.")
    with auth_page.expect_response(lambda r: "/skills/assignments/" in r.url and r.request.method == "DELETE") as gone:
        sure.get_by_role("button", name="Снять", exact=True).click()
    assert gone.value.ok, gone.value.text()
    expect(row).to_have_count(0)
    assert items(hrm_json(hrm_get(auth_page, "/skills/assignments", {"employee_id": emp["id"]}))) == []
    assert skill["id"] in [s["id"] for s in items(hrm_json(hrm_get(auth_page, "/skills/catalog")))]


# ───────────────────────── обучение ─────────────────────────

def program_card(page: Page, title: str):
    """Карточка программы — родитель шапки (название, провайдер, значки): ниже стоимость и участники."""
    return main(page).locator("div").filter(has_text=title).filter(
        has=page.get_by_role("button", name="Записать сотрудника")).last.locator("xpath=..")


# Кейс: Программа обучения — форма = API, «Записать сотрудника» → «1 участников», строка «Зачислен» во вкладке «Записи»
@pytest.mark.writes_data
def test_training_program_enroll(auth_page: Page, person):
    emp, title = person, f"{QA_PREFIX} Курс {stamp()}"
    open_hrm(auth_page, "/admin/training")
    for tile in ("Общие затраты на обучение", "Активные обучения", "Ожидающие удержания"):
        expect(main(auth_page)).to_contain_text(tile)
    tab(auth_page, "Программы")
    main(auth_page).get_by_role("button", name="Новая программа").click()
    box = modal(auth_page, "Новая программа обучения")
    field(box, "Название").fill(title)
    field(box, "Провайдер").fill("QA Academy")
    field(box, "Стоимость").fill("1500")
    expect(field(box, "Валюта")).to_have_value("MDL")
    expect(field(box, "Макс. участников")).to_have_value("20")
    with auth_page.expect_response(lambda r: r.url.endswith("/training/programs") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    prog = next(p for p in hrm_json(hrm_get(auth_page, "/training/programs")) if p["id"] == resp.value.json()["id"])
    assert (prog["title"], prog["provider"], prog["cost"], prog["currency"], prog["enrolled_count"]) == \
        (title, "QA Academy", 1500.0, "MDL", 0), prog
    card = program_card(auth_page, title)
    expect(card).to_contain_text("— QA Academy")
    expect(card).to_contain_text(re.compile(r"1\s500 MDL"))
    expect(card).to_contain_text("0 из 20 участников")

    card.get_by_role("button", name="Записать сотрудника").click()
    box = modal(auth_page, "Записать сотрудника")
    dropdown(auth_page, box, "Сотрудник", emp["telegram_first_name"])
    with auth_page.expect_response(lambda r: f"/training/programs/{prog['id']}/enroll" in r.url) as en:
        box.get_by_role("button", name="Записать", exact=True).click()
    assert en.value.ok and en.value.json()["status"] == "enrolled", en.value.text()
    expect(program_card(auth_page, title)).to_contain_text("1 из 20 участников")  # с 08.11 — «N из макс.»

    [rec] = hrm_json(hrm_get(auth_page, "/training/records", {"employee_id": emp["id"]}))
    assert (rec["training_title"], rec["status"], rec["total_cost"], rec["provider"]) == (title, "enrolled", 1500.0, "QA Academy"), rec
    tab(auth_page, "Записи")
    row = main(auth_page).locator("tbody tr").filter(has_text=emp["telegram_first_name"])
    expect(row).to_contain_text(title)
    expect(row).to_contain_text("Зачислен")
    expect(row).to_contain_text(re.compile(r"1\s500 MDL"))
    expect(row).to_contain_text(f"{rec['company_share_pct']:.0f}%")


# Кейс: «Тип» программы обучения сохраняется таким, как выбран в форме
@pytest.mark.writes_data
def test_training_program_type(auth_page: Page, dev):
    title = f"{QA_PREFIX} Курс тип {stamp()}"
    open_hrm(auth_page, "/admin/training")
    tab(auth_page, "Программы")
    main(auth_page).get_by_role("button", name="Новая программа").click()
    box = modal(auth_page, "Новая программа обучения")
    field(box, "Название").fill(title)
    dropdown(auth_page, box, "Тип", "Внешнее")
    with auth_page.expect_response(lambda r: r.url.endswith("/training/programs") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    prog = next(p for p in hrm_json(hrm_get(auth_page, "/training/programs")) if p["id"] == resp.value.json()["id"])
    assert prog["training_type"] == "external", prog
