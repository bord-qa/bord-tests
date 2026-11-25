"""Блок 34. CRM — проекты (плагин «Проекты»): crm.bord.one/admin/crm/projects.

Снято в MCP-браузере 30.10.2026:
- список: GET /api/crm/projects/?status=open&limit=200, таблица «Проект, Ответственный,
  Этап, Задачи…», внизу «Итого: N проект»; чип «Идущие» = status=open. Пусто —
  «Проектов пока нет»;
- «Новый проект» → окно: Название («Например: Doctorlica…»), Продукт, Приоритет,
  Компании, Сделка, Старт (сегодня), Шаблон этапов, Ответственный. «Создать» активна
  и без названия, но запрос не уходит, окно остаётся. После создания — снова список;
- карточка /admin/crm/projects/<id>: вкладки «План работ», «Информация по проекту»,
  «График платежей» (?tab=payments), кнопки «+ Задача», «+ Этап», выпадашки статуса
  (Запланирован / В работе / Пауза / Завершён / Отменён) и состояния, «Изменить»;
  справа выезжает «Обсуждение»;
- этап: окно «Новый этап», проверки «Назовите этап», «Конец этапа раньше начала»;
- задача заводится общей формой, проект и этап подставлены (Тип связи «Проект»);
- график платежей: «Этап оплаты» → строка формы (название «Аванс 30 %», сумма
  «15000.00», дата), «Отметить оплаченным» спрашивает дату;
- из сделки — POST /projects/from-deal/<id>: у сделки одно внедрение, повтор
  возвращает тот же проект;
- шаблон этапов раскладывается только на проект без плана;
- «Удалить проект» в окне «Изменить»: этапы удаляются, задачи остаются.

Плагин включается на время блока. API принимает пустое название — как у клиентов.
"""
import datetime as dt
import random
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import (CRM, QA_PREFIX, crm_delete, crm_get, crm_me, crm_post, crm_put, crm_ready,
                         open_crm, plugins_on)
from helpers.ui import screen_button, screen_field

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/projects"


@pytest.fixture(scope="module", autouse=True)
def projects_plugin(browser, auth_state, browser_context_args):
    yield from plugins_on(browser, auth_state, browser_context_args, "projects")


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


def create_project(page: Page, **fields) -> dict:
    resp = crm_post(page, "/projects/", {"name": f"{QA_PREFIX} {tag()}", **fields})
    assert resp.status == 201, resp.text()
    return resp.json()


def add_stage(page: Page, project: dict, **fields) -> dict:
    resp = crm_post(page, f"/projects/{project['id']}/stages", {"name": f"QA этап {tag()}", **fields})
    assert resp.status == 201, resp.text()
    return resp.json()


def add_task(page: Page, project: dict, stage: dict | None = None, **fields) -> dict:
    data = {"title": f"{QA_PREFIX} {tag()}", "entity_type": "project", "entity_id": project["id"],
            "responsible_id": crm_me(page)["id"], **fields}
    if stage:
        data["project_stage_id"] = stage["id"]
    resp = crm_post(page, "/tasks/", data)
    assert resp.status == 201, resp.text()
    return resp.json()


def clean_up(page: Page):
    projects = crm_get(page, "/projects/", {"limit": 200}).json()
    for p in projects["items"] if isinstance(projects, dict) else projects:
        if not p["name"].startswith(QA_PREFIX):
            continue
        for t in crm_get(page, f"/projects/{p['id']}/tasks").json()["items"]:
            crm_delete(page, f"/tasks/{t['id']}/permanent")
        crm_delete(page, f"/projects/{p['id']}")
    tasks = crm_get(page, "/tasks/", {"search": QA_PREFIX, "limit": 100})
    for t in tasks.json()["items"] if tasks.ok else []:
        if t["title"].startswith(QA_PREFIX):
            crm_delete(page, f"/tasks/{t['id']}/permanent")
    templates = crm_get(page, "/projects/templates/").json()["items"]
    for t in templates:
        if t["name"].startswith(QA_PREFIX):
            crm_delete(page, f"/projects/templates/{t['id']}")
    deals = crm_get(page, "/deals/", {"search": QA_PREFIX, "limit": 100})
    for d in deals.json()["items"] if deals.ok else []:
        if d["title"].startswith(QA_PREFIX):
            crm_delete(page, f"/deals/{d['id']}")


@pytest.fixture
def projects(auth_page: Page):
    open_crm(auth_page, PAGE)
    yield auth_page
    clean_up(auth_page)


def row(page: Page, name: str):
    return page.locator("main tbody tr").filter(has_text=name)


def open_project(page: Page, project: dict, tab: str | None = None):
    page.goto(f"{CRM}{PAGE}/{project['id']}" + (f"?tab={tab}" if tab else ""))
    expect(page.get_by_role("button", name="Изменить", exact=True).first).to_be_visible(timeout=20_000)
    crm_ready(page)
    return page


def window(page: Page, title: str):
    """Окно формы: fixed-слой с затемнением, не role=dialog."""
    return page.locator("div.fixed.inset-0").filter(has_text=title).last


def pick(page: Page, option: str):
    page.locator("div.shadow-xl[style*='position: fixed']").last.get_by_text(option, exact=True).click()


def confirm(page: Page, question: str):
    return page.locator("div.fixed.inset-0").filter(has_text=question).last


def status_button(page: Page, text: str):
    """Выпадашка статуса: кнопка с текстом текущего значения (доступное имя у неё другое)."""
    return page.locator("main button", has_text=text).first


def project_saved(page: Page, project: dict):
    return page.expect_response(lambda r: r.url.endswith(f"/api/crm/projects/{project['id']}")
                                and r.request.method == "PATCH")


def stage_form(page: Page):
    screen_button(page, "Этап", exact=True).click()
    form = window(page, "Новый этап")
    expect(form).to_be_visible()
    return form


# Кейс: Список проектов совпадает с API
def test_list_matches_api(projects: Page):
    project = create_project(projects)
    projects.reload()
    api = crm_get(projects, "/projects/", {"status": "open", "limit": 200}).json()
    items = api["items"]

    expect(row(projects, project["name"])).to_have_count(1)
    expect(projects.locator("main tbody tr").filter(has=projects.locator("td"))).not_to_have_count(0)
    for p in items:
        expect(row(projects, p["name"]).first).to_be_visible()
    word = "проект" if len(items) == 1 else None
    expect(projects.locator("main")).to_contain_text(f"Итого: {len(items)}" + (f" {word}" if word else ""))


# Кейс: Без названия проект не создаётся, окно остаётся
def test_create_requires_name(projects: Page):
    projects.get_by_role("button", name="Новый проект").click()
    form = window(projects, "Новый проект")
    sent = []
    projects.on("request", lambda r: sent.append(r.url) if r.method == "POST" and "/api/crm/projects" in r.url else None)

    form.get_by_role("button", name="Создать", exact=True).click()
    projects.wait_for_timeout(1_000)

    assert sent == []
    expect(form).to_be_visible()
    form.get_by_role("button", name="Отмена", exact=True).click()
    expect(projects.get_by_text("Например: Doctorlica")).to_have_count(0)


# Кейс: Новый проект через форму попадает в список и API
def test_create_via_form(projects: Page):
    name, product = f"{QA_PREFIX} {tag()}", "QA продукт"
    projects.get_by_role("button", name="Новый проект").click()
    form = window(projects, "Новый проект")
    form.get_by_placeholder(re.compile("^Например: Doctorlica")).fill(name)
    form.get_by_placeholder("SHOP / APP").fill(product)

    with projects.expect_response(lambda r: r.url.endswith("/api/crm/projects/")
                                  and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    created = resp.value.json()

    line = row(projects, name)
    expect(line).to_have_count(1)
    expect(line).to_contain_text("Средний")
    assert (created["status"], created["state"], created["priority"]) == ("planned", "normal", "medium")
    assert (created["product"], created["start_date"]) == (product, dt.date.today().isoformat())

    line.get_by_text(name).click()
    expect(projects).to_have_url(f"{CRM}{PAGE}/{created['id']}")
    expect(projects).to_have_title(f"{name} · BORD CRM")


# Кейс: Поиск оставляет только подходящий проект
def test_search(projects: Page):
    target, other = create_project(projects, name=f"{QA_PREFIX} Поиск{tag()}"), create_project(projects)
    projects.reload()
    expect(row(projects, target["name"])).to_have_count(1)

    projects.get_by_placeholder(re.compile("^Проект или комментар")).fill(target["name"])

    expect(row(projects, target["name"])).to_have_count(1)
    expect(row(projects, other["name"])).to_have_count(0)


# Кейс: Форма этапа — без названия и с концом раньше начала не сохраняет
def test_stage_validation(projects: Page):
    project = create_project(projects)
    open_project(projects, project)
    form = stage_form(projects)

    form.get_by_role("button", name="Сохранить", exact=True).click()
    expect(form).to_contain_text("Назовите этап")

    form.locator("input").first.fill("QA этап")
    form.locator("input[type=date]").nth(0).fill("2026-11-10")
    form.locator("input[type=date]").nth(1).fill("2026-11-01")
    form.get_by_role("button", name="Сохранить", exact=True).click()
    expect(form).to_contain_text("Конец этапа раньше начала")

    assert crm_get(projects, f"/projects/{project['id']}").json()["stages"] == []


# Кейс: Новый этап через форму появляется в плане и становится текущим
def test_add_stage(projects: Page):
    project = create_project(projects)
    open_project(projects, project)
    expect(projects.locator("main")).to_contain_text("Плана нет")
    name = f"QA этап {tag()}"
    form = stage_form(projects)
    form.locator("input").first.fill(name)
    form.locator("input[type=date]").nth(0).fill("2026-11-10")
    form.locator("input[type=date]").nth(1).fill("2026-11-20")

    with projects.expect_response(lambda r: f"/projects/{project['id']}/stages" in r.url
                                  and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.status == 201, resp.value.text()

    expect(projects.locator("main").get_by_text(name).first).to_be_visible()
    stage = crm_get(projects, f"/projects/{project['id']}").json()["stages"][0]
    assert (stage["name"], stage["start_date"], stage["end_date"], stage["status"]) == \
        (name, "2026-11-10", "2026-11-20", "pending")


# Кейс: Задача из карточки проекта привязана к проекту и его этапу
def test_add_task_to_project(projects: Page):
    project = create_project(projects)
    stage = add_stage(projects, project)
    open_project(projects, project)
    title = f"{QA_PREFIX} {tag()}"

    screen_button(projects, "Задача", exact=True).click()
    form = window(projects, "Новая задача")
    expect(form.get_by_role("button", name=project["name"])).to_be_visible()
    form.get_by_placeholder(re.compile("^Необязательно")).fill(title)
    with projects.expect_response(lambda r: r.url.endswith("/api/crm/tasks/")
                                  and r.request.method == "POST") as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()

    expect(projects.locator("main").get_by_text(title).first).to_be_visible()
    items = crm_get(projects, f"/projects/{project['id']}/tasks").json()["items"]
    assert [(t["title"], t["project_stage_id"]) for t in items] == [(title, stage["id"])]


# Кейс: Счётчик и прогресс проекта считаются по задачам
def test_progress_from_tasks(projects: Page):
    project = create_project(projects)
    stage = add_stage(projects, project)
    done, _ = add_task(projects, project, stage), add_task(projects, project, stage)
    assert crm_put(projects, f"/tasks/{done['id']}", {"global_status": "done"}).ok

    api = crm_get(projects, f"/projects/{project['id']}").json()
    open_project(projects, project)

    counts = api["task_counts"]
    expect(projects.locator("main")).to_contain_text(
        re.compile(rf"Задач выполнено\s*{counts['done']} из {counts['total']}"))
    assert (counts["done"], counts["total"]) == (1, 2)


# Кейс: Статус «В работе» сохраняется, «Завершён» убирает проект из идущих
def test_status_change(projects: Page):
    project = create_project(projects)
    open_project(projects, project)

    with project_saved(projects, project) as resp:
        status_button(projects, "Запланирован").click()
        pick(projects, "В работе")
    assert resp.value.ok, resp.value.text()
    assert crm_get(projects, f"/projects/{project['id']}").json()["status"] == "active"

    with project_saved(projects, project) as resp:
        status_button(projects, "В работе").click()
        pick(projects, "Завершён")
    assert resp.value.ok, resp.value.text()
    assert crm_get(projects, f"/projects/{project['id']}").json()["status"] == "done"

    projects.goto(CRM + PAGE)
    crm_ready(projects)
    expect(projects.get_by_text("Итого:").or_(projects.get_by_text("Проектов пока нет")).first).to_be_visible()
    expect(row(projects, project["name"])).to_have_count(0)


# Кейс: «Изменить» — новое название и бюджет сохраняются
def test_edit_project(projects: Page):
    project = create_project(projects)
    open_project(projects, project)
    new_name = f"{QA_PREFIX} Правка {tag()}"

    projects.get_by_role("button", name="Изменить", exact=True).first.click()
    form = window(projects, "Удалить проект")
    form.locator("input").first.fill(new_name)
    form.get_by_placeholder("Например: 12500").fill("12500")
    with project_saved(projects, project) as resp:
        form.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()

    expect(projects).to_have_title(f"{new_name} · BORD CRM")
    saved = crm_get(projects, f"/projects/{project['id']}").json()
    assert (saved["name"], float(saved["budget"])) == (new_name, 12500)


# Кейс: Удаление проекта — этапы уходят, задача остаётся в очереди
def test_delete_project_keeps_tasks(projects: Page):
    project = create_project(projects)
    stage = add_stage(projects, project)
    task = add_task(projects, project, stage)
    open_project(projects, project)

    projects.get_by_role("button", name="Изменить", exact=True).first.click()
    form = window(projects, "Удалить проект")
    form.get_by_role("button", name="Удалить проект").click()
    question = confirm(projects, f"Удалить проект «{project['name']}»?")
    expect(question).to_contain_text("Задачи останутся в общей очереди дел")
    with projects.expect_response(lambda r: r.url.endswith(f"/api/crm/projects/{project['id']}")
                                  and r.request.method == "DELETE") as resp:
        question.get_by_role("button", name="Продолжить").click()
    assert resp.value.ok, resp.value.text()

    expect(projects).to_have_url(re.compile(rf"{PAGE}$"))
    assert crm_get(projects, f"/projects/{project['id']}").status == 404
    assert crm_get(projects, f"/tasks/{task['id']}").ok, "задача не должна удаляться вместе с проектом"


# Кейс: Этап оплаты — добавить, итоги, отметить оплаченным
def test_payment_flow(projects: Page):
    project = create_project(projects)
    open_project(projects, project, "payments")
    main = projects.locator("main")

    screen_button(projects, "Этап оплаты").click()
    screen_field(projects, "Аванс 30 %").fill("QA аванс")
    screen_field(projects, "15000.00").fill("1000")
    # срок — в будущем: этап с прошедшим сроком показывается просроченным, а не «Не оплачен»
    # (до 18.11 тут была зашита дата 2026-11-15)
    main.locator("input[type=date]").fill((dt.date.today() + dt.timedelta(days=30)).isoformat())
    with projects.expect_response(lambda r: f"/projects/{project['id']}/payments" in r.url
                                  and r.request.method == "POST") as resp:
        screen_button(projects, "Добавить", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    payment = resp.value.json()

    expect(main).to_contain_text("QA аванс")
    expect(main).to_contain_text("Не оплачен")
    expect(main).to_contain_text(re.compile(r"Остаток к получению\s*1\D000\.00\sMDL"))

    assert crm_post(projects, f"/projects/{project['id']}/payments/{payment['id']}/paid",
                    {"paid_at": "2026-11-01"}).ok
    projects.reload()
    expect(main).to_contain_text(re.compile(r"Остаток к получению\s*0\.00\sMDL"), timeout=20_000)
    totals = crm_get(projects, f"/projects/{project['id']}/payments").json()["totals"][0]
    assert (float(totals["paid"]), float(totals["outstanding"])) == (1000, 0)


# Кейс: Вкладка «Информация по проекту» — блоки совпадают с API
def test_info_blocks(projects: Page):
    project = create_project(projects)
    open_project(projects, project)

    screen_button(projects, "Информация по проекту").click()

    blocks = crm_get(projects, f"/projects/{project['id']}/info").json()["blocks"]
    assert blocks
    for b in blocks:
        expect(projects.locator("main").get_by_text(b["title"]).first).to_be_visible()


# Кейс: Проект из сделки — один на сделку, повтор возвращает тот же
def test_project_from_deal_is_idempotent(projects: Page):
    pipelines = crm_get(projects, "/pipeline-statuses/pipelines").json()["items"]
    deal = crm_post(projects, "/deals/", {"title": f"{QA_PREFIX} {tag()}", "responsible_id": crm_me(projects)["id"],
                                          "pipeline_id": next(p for p in pipelines if p["is_main"])["id"]}).json()

    first = crm_post(projects, f"/projects/from-deal/{deal['id']}", {})
    second = crm_post(projects, f"/projects/from-deal/{deal['id']}", {})

    assert first.ok and second.ok, (first.text(), second.text())
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["deal_id"] == deal["id"]
    assert first.json()["name"].startswith(QA_PREFIX)


# Кейс: Шаблон раскладывается на пустой проект и не накладывается повторно
def test_apply_template(projects: Page):
    template = crm_post(projects, "/projects/templates/", {
        "name": f"{QA_PREFIX} шаблон {tag()}",
        "stages": [{"name": "QA первый", "duration_days": 5}, {"name": "QA второй", "duration_days": 3}]})
    assert template.status == 201, template.text()
    project = create_project(projects, start_date="2026-11-02")

    resp = crm_post(projects, f"/projects/{project['id']}/apply-template", {"template_id": template.json()["id"]})
    assert resp.ok, resp.text()
    stages = crm_get(projects, f"/projects/{project['id']}").json()["stages"]
    assert [s["name"] for s in stages] == ["QA первый", "QA второй"]

    again = crm_post(projects, f"/projects/{project['id']}/apply-template", {"template_id": template.json()["id"]})
    assert again.status >= 400, "шаблон лёг на проект, у которого план уже есть"
    assert len(crm_get(projects, f"/projects/{project['id']}").json()["stages"]) == 2


# Кейс: «Нагрузка» открывается и показывает итоги
def test_workload(projects: Page):
    open_crm(projects, PAGE + "/workload")
    main = projects.locator("main")
    for label in ("В работе", "Просрочено", "Срок сегодня", "Закрыто сегодня", "Вне плана"):
        expect(main.get_by_text(label, exact=True).first).to_be_visible()
