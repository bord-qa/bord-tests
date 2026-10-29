"""Блок 27. CRM — рабочие процессы: crm.bord.one/admin/crm/processes.

Снято 29.10.2026:
- слева список процессов (кнопка-строка: название, «выключен», описание,
  «N этапов», «N задач»), справа выбранный процесс: «Переименовать процесс»,
  «Выключить процесс» / «Включить процесс», «Этап» и строки этапов (номер,
  цвет, название, «завершающий», кодовое имя, «N задач», кнопки «Выше»,
  «Ниже», «Изменить этап», «Удалить этап» — подписи в title);
- API: GET /api/crm/processes/?limit=200 → {items: [{id, name, description,
  is_active, stages: [{id, name, codename, order, color, is_final,
  task_count}], task_count}]}; POST /processes/ {name, description};
  PUT /processes/<id>; этапы — POST/PUT/DELETE /processes/<id>/stages[/<id>];
- «Процесс» → окно «Новый процесс»: пустое название → «Введите название
  процесса.»; «Этап» → «Новый этап»: кодовое имя необязательно (сервер берёт
  транслит: «На проверке» → na_proverke), цвет, переключатель «Завершающий
  этап»; правка — то же окно «Этап процесса»;
- задача на завершающем этапе закрыта (global_status done + completed_on),
  возврат на обычный этап открывает её снова (in_progress, дата стёрта);
- удалить этап с задачами: DELETE → 409, в окне «Перенести задачи на этап» →
  «Перенести и удалить» → DELETE …?move_to=<этап>;
- «Выключить процесс» — это и есть удаление: DELETE /processes/<id> ставит
  is_active=false, процесс и этапы остаются («выключен»), «Включить процесс»
  → PUT is_active=true. С задачами DELETE → 409 и окно «Перенести задачи в
  процесс» → DELETE …?move_to=<процесс> (задачи встают на его первый этап);
- на доске задач: в форме задачи «Этап процесса (колонка на доске задач)»,
  в карточке «Этап процесса» → PUT /tasks/<id>/stage {stage_id}, фильтр
  «Этап» на доске → ?stage_id=<id>.

Насовсем процессы не удаляются. Чтобы выключенные «QA Autotest» не копились,
тесты берут для своих процессов выключенный тестовый из прошлых прогонов:
стирают его этапы, переименовывают и включают; в конце — выключают снова.
Новые процессы заводят только тесты формы «Новый процесс».
"""
import random
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import CRM, QA_PREFIX, crm_delete, crm_get, crm_me, crm_post, crm_put, open_crm

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/processes"
TASKS = "/admin/crm/tasks"


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


def stages_label(n: int) -> str:
    return plural(n, "этап", "этапа", "этапов")


def tasks_label(n: int) -> str:
    return plural(n, "задача", "задачи", "задач")


def all_processes(page: Page) -> list[dict]:
    resp = crm_get(page, "/processes/", {"limit": 200})
    assert resp.ok, resp.text()
    return resp.json()["items"]


def get_process(page: Page, process_id: int) -> dict:
    return next(p for p in all_processes(page) if p["id"] == process_id)


def stages_of(page: Page, process_id: int) -> list[dict]:
    return sorted(crm_get(page, f"/processes/{process_id}/stages").json(), key=lambda s: s["order"])


def _reuse_spare(page: Page, name: str, description: str) -> dict | None:
    """Выключенный «QA Autotest» процесс прошлых прогонов — без этапов, под новым именем."""
    for spare in all_processes(page):
        if spare["is_active"] or not spare["name"].startswith(QA_PREFIX) or spare["id"] in page._qa_processes:
            continue
        if not all(crm_delete(page, f"/processes/{spare['id']}/stages/{s['id']}").ok
                   for s in stages_of(page, spare["id"])):
            continue  # на этапе остались задачи — берём другой
        resp = crm_put(page, f"/processes/{spare['id']}",
                       {"name": name, "description": description, "is_active": True})
        assert resp.ok, resp.text()
        return resp.json()
    return None


def make_process(page: Page, *stages: str | tuple[str, bool]) -> dict:
    """Включённый тестовый процесс с этапами по порядку; ("Имя", True) — завершающий."""
    name, description = f"{QA_PREFIX} {tag()}", f"{QA_PREFIX} описание"
    process = _reuse_spare(page, name, description)
    if process is None:
        resp = crm_post(page, "/processes/", {"name": name, "description": description})
        assert resp.status == 201, resp.text()
        process = resp.json()
    page._qa_processes.append(process["id"])
    for order, stage in enumerate(stages, 1):
        stage_name, final = stage if isinstance(stage, tuple) else (stage, False)
        resp = crm_post(page, f"/processes/{process['id']}/stages",
                        {"name": stage_name, "order": order, "is_final": final})
        assert resp.status == 201, resp.text()
    return get_process(page, process["id"])


def stage_named(process: dict, name: str) -> dict:
    return next(s for s in process["stages"] if s["name"] == name)


def make_task(page: Page, process: dict, stage: dict) -> dict:
    data = {"title": f"{QA_PREFIX} {tag()}", "responsible_id": crm_me(page)["id"],
            "process_id": process["id"], "stage_id": stage["id"]}
    resp = crm_post(page, "/tasks/", data)
    assert resp.status == 201, resp.text()
    page._qa_tasks.append(resp.json()["id"])
    return resp.json()


def cleanup(page: Page):
    for task_id in page._qa_tasks:
        crm_delete(page, f"/tasks/{task_id}")
        crm_delete(page, f"/tasks/{task_id}/permanent")
    # свои — и брошенные упавшими прогонами включённые «QA Autotest»
    ids = set(page._qa_processes) | {p["id"] for p in all_processes(page)
                                     if p["is_active"] and p["name"].startswith(QA_PREFIX)}
    for process_id in ids:
        crm_delete(page, f"/processes/{process_id}")


@pytest.fixture
def processes(auth_page: Page):
    auth_page._qa_processes, auth_page._qa_tasks = [], []
    yield auth_page
    cleanup(auth_page)


def open_processes(page: Page, process: dict | None = None):
    open_crm(page, PAGE)
    expect(page.get_by_role("heading", name="Процессы", exact=True)).to_be_visible()
    if process:
        process_row(page, process["name"]).click()
        expect(detail_heading(page, process["name"])).to_be_visible()


def process_row(page: Page, name: str):
    return page.locator("main button.text-left").filter(has=page.get_by_text(name, exact=True))


def detail_heading(page: Page, name: str):
    return page.locator("main h2").filter(has_text=name)


def stage_row(page: Page, name: str):
    return page.locator("main div.px-5.py-3").filter(
        has=page.locator("button[title='Удалить этап']")).filter(has=page.get_by_text(name, exact=True))


def stage_names(page: Page):
    """Названия этапов на экране по порядку (для expect …to_have_text([...]))."""
    rows = page.locator("main div.px-5.py-3").filter(has=page.locator("button[title='Удалить этап']"))
    return rows.locator("span.font-medium")


def overlay(page: Page, text: str):
    """Окна CRM — div.fixed.inset-0 (не role=dialog)."""
    return page.locator("div.fixed.inset-0").filter(has_text=text).last


def pick(page: Page, option: str):
    """Вариант во всплывающем списке CRM (плавающий блок position: fixed):
    в фильтрах доски — role=option, в формах и карточке — кнопки."""
    popup = page.locator("div.shadow-xl[style*='position: fixed']").last
    popup.get_by_role("option", name=option, exact=True).or_(
        popup.get_by_role("button", name=option, exact=True)).first.click()


def process_post(page: Page):
    return page.expect_response(lambda r: r.url.endswith("/api/crm/processes/") and r.request.method == "POST")


def stage_post(page: Page, process: dict):
    return page.expect_response(
        lambda r: r.url.endswith(f"/api/crm/processes/{process['id']}/stages") and r.request.method == "POST")


def new_stage_form(page: Page):
    page.locator("main").get_by_role("button", name="Этап", exact=True).click()
    form = overlay(page, "Новый этап")
    expect(form.get_by_placeholder("Например: На проверке")).to_be_visible()
    return form


def open_task_card(page: Page, task: dict):
    page.goto(f"{CRM}{TASKS}?task={task['id']}")
    card = overlay(page, task["title"])
    expect(card.get_by_role("button", name="Инфо")).to_be_visible(timeout=20_000)
    return card


# Кейс: Список процессов совпадает с API — этапы и задачи в строке, «выключен», этапы по порядку
def test_list_matches_api(processes: Page):
    t = tag()
    work, done = f"Разбор {t}", f"Сделано {t}"
    process = make_process(processes, work, (done, True))
    make_task(processes, process, stage_named(process, work))
    open_processes(processes, process)

    items = all_processes(processes)
    expect(processes.locator("main button.text-left")).to_have_count(len(items))
    for item in items:
        row = process_row(processes, item["name"])
        expect(row).to_contain_text(stages_label(len(item["stages"])))
        expect(row).to_contain_text(tasks_label(item["task_count"]))
        if item["is_active"]:
            expect(row).not_to_contain_text("выключен")
        else:
            expect(row).to_contain_text("выключен")

    mine = get_process(processes, process["id"])
    assert (mine["task_count"], [s["task_count"] for s in mine["stages"]]) == (1, [1, 0]), mine
    expect(stage_names(processes)).to_have_text([work, done])
    for stage in mine["stages"]:
        row = stage_row(processes, stage["name"])
        expect(row).to_contain_text(stage["codename"])
        expect(row).to_contain_text(tasks_label(stage["task_count"]))
    expect(stage_row(processes, done)).to_contain_text("завершающий", ignore_case=True)
    expect(stage_row(processes, work)).not_to_contain_text("завершающий", ignore_case=True)
    expect(stage_row(processes, work).get_by_role("button", name="Выше")).to_be_disabled()
    expect(stage_row(processes, done).get_by_role("button", name="Ниже")).to_be_disabled()


# Кейс: «Сохранить» без названия процесса — «Введите название процесса.», ничего не создаётся
def test_create_without_name(processes: Page):
    sent = []
    processes.on("request", lambda r: r.url.endswith("/api/crm/processes/") and r.method == "POST" and sent.append(r))
    open_processes(processes)

    processes.locator("main").get_by_role("button", name="Процесс", exact=True).click()
    form = overlay(processes, "Новый процесс")
    form.get_by_role("button", name="Сохранить", exact=True).click()

    expect(form).to_contain_text("Введите название процесса.")
    form.get_by_role("button", name="Отмена", exact=True).click()
    expect(form).to_have_count(0)
    assert not sent, "пустая форма отправила процесс"


# Кейс: Новый процесс через форму — в списке и справа, без этапов и с подсказкой «некуда поставить задачу»
def test_create_process_via_form(processes: Page):
    name, about = f"{QA_PREFIX} {tag()}", f"{QA_PREFIX} для задач поддержки"
    open_processes(processes)

    processes.locator("main").get_by_role("button", name="Процесс", exact=True).click()
    form = overlay(processes, "Новый процесс")
    expect(form).to_contain_text("Этапы у нового процесса задаются следующим шагом")
    form.get_by_placeholder("Например: Разработка").fill(name)
    form.get_by_placeholder("Для каких задач этот процесс").fill(about)
    with process_post(processes) as resp:
        form.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    created = resp.value.json()
    processes._qa_processes.append(created["id"])

    assert (created["name"], created["description"], created["is_active"]) == (name, about, True), created
    expect(form).to_have_count(0)
    row = process_row(processes, name)
    expect(row).to_contain_text(about)
    expect(row).to_contain_text("0 этапов")
    expect(row).to_contain_text("0 задач")
    expect(detail_heading(processes, name)).to_be_visible()
    expect(processes.locator("main")).to_contain_text("В процессе нет ни одного этапа")


# Кейс: Этап через форму — кодовое имя из названия, цвет, номер; подсказка «ни один этап не завершающий»
def test_add_stage_via_form(processes: Page):
    process = make_process(processes)
    open_processes(processes, process)

    form = new_stage_form(processes)
    form.get_by_placeholder("Например: На проверке").fill("На проверке")
    form.get_by_role("button", name="#8b5cf6", exact=True).click()
    with stage_post(processes, process) as resp:
        form.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.status == 201, resp.value.text()

    stage = resp.value.json()
    assert (stage["name"], stage["codename"], stage["color"], stage["order"], stage["is_final"]) == (
        "На проверке", "na_proverke", "#8b5cf6", 1, False), stage
    expect(form).to_have_count(0)
    row = stage_row(processes, "На проверке")
    expect(row).to_contain_text("na_proverke")
    expect(row.locator("span[title='#8b5cf6']")).to_be_visible()
    expect(processes.locator("main")).to_contain_text("Ни один этап не помечен завершающим")
    expect(process_row(processes, process["name"])).to_contain_text("1 этап")


# Кейс: Завершающий этап через форму — встаёт вторым, отметка «завершающий», подсказка пропадает
def test_add_final_stage_via_form(processes: Page):
    t = tag()
    process = make_process(processes, f"Разбор {t}")
    open_processes(processes, process)
    expect(processes.locator("main")).to_contain_text("Ни один этап не помечен завершающим")

    form = new_stage_form(processes)
    form.get_by_placeholder("Например: На проверке").fill(f"Сделано {t}")
    toggle = form.get_by_role("button", name="Завершающий этап")
    toggle.click()
    expect(toggle.locator("svg.lucide-toggle-right")).to_be_visible()
    with stage_post(processes, process) as resp:
        form.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.status == 201, resp.value.text()

    stage = resp.value.json()
    assert (stage["order"], stage["is_final"], stage["codename"]) == (2, True, f"sdelano_{t}"), stage
    expect(stage_row(processes, f"Сделано {t}")).to_contain_text("завершающий", ignore_case=True)
    expect(processes.locator("main")).not_to_contain_text("Ни один этап не помечен завершающим")
    expect(stage_names(processes)).to_have_text([f"Разбор {t}", f"Сделано {t}"])


# Кейс: Переименовать процесс — окно с текущими значениями, новое название и описание в списке и в API
def test_rename_process(processes: Page):
    process = make_process(processes)
    new_name, new_about = f"{QA_PREFIX} {tag()}", f"{QA_PREFIX} новое описание"
    open_processes(processes, process)

    processes.get_by_role("button", name="Переименовать процесс").click()
    form = overlay(processes, "Рабочий процесс")
    expect(form.get_by_placeholder("Например: Разработка")).to_have_value(process["name"])
    expect(form.get_by_placeholder("Для каких задач этот процесс")).to_have_value(process["description"])
    form.get_by_placeholder("Например: Разработка").fill(new_name)
    form.get_by_placeholder("Для каких задач этот процесс").fill(new_about)
    with processes.expect_response(lambda r: r.url.endswith(f"/api/crm/processes/{process['id']}")
                                   and r.request.method == "PUT") as resp:
        form.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()

    expect(detail_heading(processes, new_name)).to_be_visible()
    expect(process_row(processes, new_name)).to_contain_text(new_about)
    expect(process_row(processes, process["name"])).to_have_count(0)
    saved = get_process(processes, process["id"])
    assert (saved["name"], saved["description"]) == (new_name, new_about), saved


# Кейс: Изменить этап — окно с текущими значениями, новое название, кодовое имя остаётся прежним
def test_edit_stage(processes: Page):
    t = tag()
    process = make_process(processes, f"Разбор {t}")
    stage = process["stages"][0]
    open_processes(processes, process)

    stage_row(processes, stage["name"]).get_by_role("button", name="Изменить этап").click()
    form = overlay(processes, "Этап процесса")
    expect(form.get_by_placeholder("Например: На проверке")).to_have_value(stage["name"])
    expect(form.get_by_placeholder("Автоматически из названия")).to_have_value(stage["codename"])
    form.get_by_placeholder("Например: На проверке").fill(f"Анализ {t}")
    with processes.expect_response(lambda r: f"/processes/{process['id']}/stages/{stage['id']}" in r.url
                                   and r.request.method == "PUT") as resp:
        form.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()

    expect(stage_row(processes, f"Анализ {t}")).to_contain_text(stage["codename"])
    expect(stage_row(processes, stage["name"])).to_have_count(0)
    saved = stages_of(processes, process["id"])[0]
    assert (saved["name"], saved["codename"]) == (f"Анализ {t}", stage["codename"]), saved


# Кейс: «Ниже» меняет этапы местами — на экране и в порядке API
def test_stage_move_down(processes: Page):
    t = tag()
    first, second = f"Разбор {t}", f"Проверка {t}"
    process = make_process(processes, first, second)
    open_processes(processes, process)

    # «Ниже» шлёт два PUT — новый order каждому из пары; на время сохранения
    # кнопки отключаются все, так что ждём сам порядок строк
    stage_row(processes, first).get_by_role("button", name="Ниже").click()

    expect(stage_names(processes)).to_have_text([second, first])
    expect(stage_row(processes, first).get_by_role("button", name="Ниже")).to_be_disabled()
    expect(stage_row(processes, second).get_by_role("button", name="Выше")).to_be_disabled()
    assert [s["name"] for s in stages_of(processes, process["id"])] == [second, first]


# Кейс: Удалить пустой этап — подтверждение с названием, этап пропадает из процесса и API
def test_delete_empty_stage(processes: Page):
    t = tag()
    process = make_process(processes, f"Разбор {t}", f"Лишний {t}")
    extra = stage_named(process, f"Лишний {t}")
    open_processes(processes, process)

    stage_row(processes, extra["name"]).get_by_role("button", name="Удалить этап").click()
    confirm = overlay(processes, f"Удалить этап «{extra['name']}»")
    expect(confirm).to_contain_text("Этап исчезнет из процесса")
    with processes.expect_response(lambda r: f"/stages/{extra['id']}" in r.url
                                   and r.request.method == "DELETE") as resp:
        confirm.get_by_role("button", name="Удалить", exact=True).click()
    assert resp.value.status == 204, resp.value.text()

    expect(stage_row(processes, extra["name"])).to_have_count(0)
    assert [s["name"] for s in stages_of(processes, process["id"])] == [f"Разбор {t}"]
    expect(process_row(processes, process["name"])).to_contain_text("1 этап")


# Кейс: Удалить этап с задачей — сервер спрашивает, куда перенести; перенос на завершающий закрывает задачу
def test_delete_stage_with_task_moves_it(processes: Page):
    t = tag()
    work, done = f"Разбор {t}", f"Сделано {t}"
    process = make_process(processes, work, (done, True))
    task = make_task(processes, process, stage_named(process, work))
    open_processes(processes, process)

    stage_row(processes, work).get_by_role("button", name="Удалить этап").click()
    confirm = overlay(processes, f"Удалить этап «{work}»")
    with processes.expect_response(lambda r: r.request.method == "DELETE" and "/stages/" in r.url) as first:
        confirm.get_by_role("button", name="Удалить", exact=True).click()
    assert first.value.status == 409, first.value.text()
    expect(confirm).to_contain_text(f"На этапе «{work}» 1 задача")
    expect(confirm).to_contain_text(f"{done} (завершающий)")

    with processes.expect_response(lambda r: r.request.method == "DELETE" and "move_to=" in r.url) as resp:
        confirm.get_by_role("button", name="Перенести и удалить").click()
    assert resp.value.status == 204, resp.value.text()
    assert f"move_to={stage_named(process, done)['id']}" in resp.value.url

    expect(stage_row(processes, work)).to_have_count(0)
    expect(stage_row(processes, done)).to_contain_text("1 задача")
    saved = crm_get(processes, f"/tasks/{task['id']}").json()
    assert (saved["stage_name"], saved["global_status"]) == (done, "done"), saved
    assert saved["completed_on"], f"перенос на завершающий этап не проставил дату завершения: {saved}"


# Кейс: Выключить процесс (это и есть удаление) — пометка «выключен», включить обратно
def test_disable_and_enable_process(processes: Page):
    process = make_process(processes, f"Разбор {tag()}")
    open_processes(processes, process)

    processes.get_by_role("button", name="Выключить процесс (это и есть «удалить»: строка и этапы сохраняются)").click()
    confirm = overlay(processes, f"Выключить процесс «{process['name']}»")
    expect(confirm).to_contain_text("включить обратно можно в любой момент")
    with processes.expect_response(lambda r: r.url.endswith(f"/api/crm/processes/{process['id']}")
                                   and r.request.method == "DELETE") as resp:
        confirm.get_by_role("button", name="Выключить", exact=True).click()
    assert resp.value.status == 204, resp.value.text()

    expect(process_row(processes, process["name"])).to_contain_text("выключен")
    off = get_process(processes, process["id"])
    assert (off["is_active"], [s["name"] for s in off["stages"]]) == (
        False, [s["name"] for s in process["stages"]]), off

    with processes.expect_response(lambda r: r.url.endswith(f"/api/crm/processes/{process['id']}")
                                   and r.request.method == "PUT") as resp:
        processes.get_by_role("button", name="Включить процесс").click()
    assert resp.value.ok, resp.value.text()
    expect(process_row(processes, process["name"])).not_to_contain_text("выключен")
    assert get_process(processes, process["id"])["is_active"] is True


# Кейс: Выключить процесс с задачей — перенос в другой процесс, задача встаёт на его первый этап
def test_disable_process_with_task_moves_it(processes: Page):
    t = tag()
    source = make_process(processes, f"Разбор {t}")
    target = make_process(processes, f"Входящие {t}", f"Проверка {t}")
    task = make_task(processes, source, source["stages"][0])
    open_processes(processes, source)

    processes.get_by_role("button", name="Выключить процесс (это и есть «удалить»: строка и этапы сохраняются)").click()
    confirm = overlay(processes, f"Выключить процесс «{source['name']}»")
    with processes.expect_response(lambda r: r.request.method == "DELETE" and "/processes/" in r.url) as first:
        confirm.get_by_role("button", name="Выключить", exact=True).click()
    assert first.value.status == 409, first.value.text()
    expect(confirm).to_contain_text(f"В процессе «{source['name']}» 1 задача")
    confirm.get_by_role("button", name="Перенести задачи в процесс").click()
    pick(processes, target["name"])

    with processes.expect_response(lambda r: r.request.method == "DELETE" and "move_to=" in r.url) as resp:
        confirm.get_by_role("button", name="Перенести и выключить").click()
    assert resp.value.status == 204, resp.value.text()
    assert f"move_to={target['id']}" in resp.value.url

    expect(process_row(processes, source["name"])).to_contain_text("выключен")
    expect(process_row(processes, target["name"])).to_contain_text("1 задача")
    saved = crm_get(processes, f"/tasks/{task['id']}").json()
    assert (saved["process_id"], saved["stage_id"]) == (target["id"], target["stages"][0]["id"]), saved


# Кейс: Задача из формы на доске задач встаёт на выбранный этап процесса
def test_task_form_puts_task_on_stage(processes: Page):
    t = tag()
    first, second = f"Разбор {t}", f"Проверка {t}"
    process = make_process(processes, first, second)
    title = f"{QA_PREFIX} {tag()}"
    open_crm(processes, TASKS)

    processes.get_by_role("button", name="Новая задача").click()
    form = overlay(processes, "Новая задача")
    expect(form).to_contain_text("Этап процесса (колонка на доске задач)")
    form.get_by_placeholder("Необязательно — без текста задача будет называться «Задача»").fill(title)
    form.get_by_role("button", name="— первый этап процесса —").click()
    pick(processes, second)
    form.get_by_role("button", name="— Не назначен —").click()
    pick(processes, crm_me(processes)["email"].split("@")[0])
    with processes.expect_response(lambda r: r.url.endswith("/api/crm/tasks/") and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    created = resp.value.json()
    processes._qa_tasks.append(created["id"])

    assert (created["process_id"], created["stage_id"]) == (
        process["id"], stage_named(process, second)["id"]), created
    open_processes(processes, process)
    expect(stage_row(processes, second)).to_contain_text("1 задача")
    expect(stage_row(processes, first)).to_contain_text("0 задач")


# Кейс: Этап в карточке задачи — завершающий закрывает задачу, возврат на обычный открывает снова
def test_task_card_final_stage_closes_and_reopens(processes: Page):
    t = tag()
    work, done = f"Разбор {t}", f"Сделано {t}"
    process = make_process(processes, work, (done, True))
    task = make_task(processes, process, stage_named(process, work))
    card = open_task_card(processes, task)
    expect(card).to_contain_text("Этап процесса")

    def move(current: str, target: str):
        card.get_by_role("button", name=current, exact=True).click()
        with processes.expect_response(lambda r: r.url.endswith(f"/api/crm/tasks/{task['id']}/stage")
                                       and r.request.method == "PUT") as resp:
            pick(processes, target)
        assert resp.value.ok, resp.value.text()
        expect(card.get_by_role("button", name=target, exact=True)).to_be_visible()

    move(work, done)
    expect(card.get_by_role("button", name="Завершена", exact=True)).to_be_visible()
    closed = crm_get(processes, f"/tasks/{task['id']}").json()
    assert (closed["stage_id"], closed["global_status"]) == (stage_named(process, done)["id"], "done"), closed
    assert closed["completed_at"] and closed["completed_on"], closed

    move(done, work)
    expect(card.get_by_role("button", name="Завершена", exact=True)).to_have_count(0)
    reopened = crm_get(processes, f"/tasks/{task['id']}").json()
    assert (reopened["global_status"], reopened["completed_at"], reopened["completed_on"]) == (
        "in_progress", None, None), reopened


# Кейс: Фильтр «Этап» на доске задач — в адресе stage_id, видны только задачи этого этапа
def test_tasks_stage_filter(processes: Page):
    t = tag()
    work, check = f"Разбор {t}", f"Проверка {t}"
    process = make_process(processes, work, check)
    on_work = make_task(processes, process, stage_named(process, work))
    on_check = make_task(processes, process, stage_named(process, check))
    open_crm(processes, TASKS)
    cards = processes.locator("main [draggable=true]")
    expect(cards.filter(has_text=on_work["title"])).to_be_visible()
    expect(cards.filter(has_text=on_check["title"])).to_be_visible()

    processes.get_by_role("button", name="Этап", exact=True).click()
    pick(processes, work)
    processes.keyboard.press("Escape")

    expect(processes).to_have_url(re.compile(rf"[?&]stage_id={stage_named(process, work)['id']}(&|$)"))
    expect(cards.filter(has_text=on_work["title"])).to_be_visible()
    expect(cards.filter(has_text=on_check["title"])).to_have_count(0)


# Кейс: Этапы выключенного процесса не предлагаются в форме задачи
def test_disabled_process_not_offered(processes: Page):
    t = tag()
    live, gone = f"Разбор {t}", f"Архив {t}"
    make_process(processes, live)
    off = make_process(processes, gone)
    assert crm_delete(processes, f"/processes/{off['id']}").status == 204
    open_crm(processes, TASKS)

    processes.get_by_role("button", name="Новая задача").click()
    form = overlay(processes, "Новая задача")
    form.get_by_role("button", name="— первый этап процесса —").click()
    popup = processes.locator("div.shadow-xl[style*='position: fixed']").last
    expect(popup.get_by_role("button", name=live, exact=True)).to_be_visible()
    expect(popup.get_by_role("button", name=gone, exact=True)).to_have_count(0)
