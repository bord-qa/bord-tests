"""Блок 23. CRM — задачи: crm.bord.one/admin/crm/tasks.

Снято 27.10.2026:
- доска «Канбан» — колонки по срокам: GET /api/crm/tasks/due-board?responsible_id=<я>
  → {today, buckets: [{key, title, count, items}]}; ключи overdue / today /
  tomorrow / week / later / no_due («Просроченные», «Сегодня», «Завтра», «Эта
  неделя», «Позже», «Без срока»). Колонка — section[aria-label=<title>], в шапке
  счётчик; карточка — div[draggable] с title «Перетащите в другую колонку,
  чтобы сменить срок»;
- по умолчанию включено «Мои» (подсказка «Включено «Мои» — показаны только
  ваши задачи»), «Все» убирает responsible_id из запросов. Фильтр «Статус»
  (кнопка «Все статусы», aria-label «Статус») — выпадашка с role=option,
  выбор уходит в адрес ?global_status=…; поиск «Поиск по названию...» → ?search=;
- «Новая задача» → окно: Название необязательно («без текста задача будет
  называться «Задача»»), Описание, Приоритет, Статус, Ответственный (по
  умолчанию «— Не назначен —»), Срок → POST /api/crm/tasks/ → 201;
- карточка — окно поверх доски, в адресе ?task=<id>; «Завершить задачу» →
  PUT /tasks/<id> {global_status: done}; статус и приоритет — выпадашки → PUT;
  «Добавить подзадачу» → окно «Новая подзадача» → POST с parent_task_id;
  комментарий «Комментарий…» + «Отправить» → POST /tasks/<id>/comments
  (multipart), виден во вкладке «Активность»;
- «Архивировать» → «Архивировать задачу?» → «Продолжить» → DELETE /tasks/<id>
  (это архив, не удаление). Архивные видны только в «Список» + «Архивные»
  (доска сроков их не показывает), в строке и в карточке — «Разархивировать» →
  POST /tasks/<id>/restore;
- «Удалить навсегда» → «Удалить задачу навсегда?» → DELETE /tasks/<id>/permanent
  (вместе с подзадачами).

Отдельного чек-листа в CRM нет: POST /tasks/<id>/checklist заводит подзадачи.
Свои задачи «QA Autotest …» ставим на тест-учётку и удаляем насовсем.
"""
import datetime as dt
import random
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import CRM, QA_PREFIX, crm_delete, crm_get, crm_me, crm_post, crm_ready, open_crm

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/tasks"
BUCKETS = ["Просроченные", "Сегодня", "Завтра", "Эта неделя", "Позже", "Без срока"]


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


def me_name(page: Page) -> str:
    return crm_me(page)["email"].split("@")[0]


def track(page: Page, task: dict) -> dict:
    page._qa_tasks.append(task["id"])
    return task


def create_task(page: Page, **fields) -> dict:
    data = {"title": f"{QA_PREFIX} {tag()}", "responsible_id": crm_me(page)["id"], **fields}
    resp = crm_post(page, "/tasks/", data)
    assert resp.status == 201, resp.text()
    return track(page, resp.json())


def server_today(page: Page) -> dt.date:
    """«Сегодня» по часам CRM — от него считаются колонки доски."""
    return dt.date.fromisoformat(crm_get(page, "/tasks/due-board", {"limit": 1}).json()["today"])


def delete_qa_tasks(page: Page):
    ids = set(page._qa_tasks)
    for archived in ("false", "true"):
        resp = crm_get(page, "/tasks/", {"search": QA_PREFIX, "is_archived": archived,
                                         "responsible_id": crm_me(page)["id"], "limit": 100})
        ids |= {t["id"] for t in resp.json()["items"] if t["title"].startswith(QA_PREFIX)} if resp.ok else set()
    for task_id in ids:
        crm_delete(page, f"/tasks/{task_id}")
        crm_delete(page, f"/tasks/{task_id}/permanent")


@pytest.fixture
def tasks(auth_page: Page):
    auth_page._qa_tasks = []
    open_crm(auth_page, PAGE)
    yield auth_page
    delete_qa_tasks(auth_page)


def reload_board(page: Page):
    page.reload()
    crm_ready(page)


def column(page: Page, bucket: str):
    return page.locator(f"main section[aria-label='{bucket}']")


def column_count(page: Page, bucket: str):
    return column(page, bucket).locator("header > span")


def task_card(page: Page, title: str):
    return page.locator("main [draggable=true]").filter(has_text=title)


def overlay(page: Page, text: str):
    """Окна CRM — div.fixed.inset-0 (не role=dialog): карточка, форма, подтверждения."""
    return page.locator("div.fixed.inset-0").filter(has_text=text).last


def open_card(page: Page, task: dict):
    page.goto(f"{CRM}{PAGE}?task={task['id']}")
    card = overlay(page, task["title"])
    expect(card.get_by_role("button", name="Инфо")).to_be_visible(timeout=20_000)
    return card


def pick(page: Page, option: str):
    """Вариант во всплывающем списке CRM.

    Список рисуется отдельным плавающим блоком (position: fixed, shadow-xl),
    поле «Поиск…» есть только у длинных. В фильтрах доски варианты —
    role=option с галочкой, в форме и карточке — обычные кнопки."""
    popup = page.locator("div.shadow-xl[style*='position: fixed']").last
    popup.get_by_role("option", name=option, exact=True).or_(
        popup.get_by_role("button", name=option, exact=True)).first.click()


def new_task_form(page: Page):
    page.get_by_role("button", name="Новая задача").click()
    form = overlay(page, "Новая задача")
    expect(form.get_by_role("button", name="Создать", exact=True)).to_be_visible()
    return form


def task_post(page: Page):
    return page.expect_response(lambda r: r.url.endswith("/api/crm/tasks/") and r.request.method == "POST")


# Кейс: Доска задач совпадает с API — колонки сроков, счётчики, место каждой задачи
def test_board_matches_api(tasks: Page):
    today = server_today(tasks)
    mine = {
        "overdue": create_task(tasks, due_date=(today - dt.timedelta(days=2)).isoformat()),
        "today": create_task(tasks, due_date=today.isoformat()),
        "tomorrow": create_task(tasks, due_date=(today + dt.timedelta(days=1)).isoformat()),
        "later": create_task(tasks, due_date=(today + dt.timedelta(days=30)).isoformat()),
        "no_due": create_task(tasks),
    }
    reload_board(tasks)

    board = crm_get(tasks, "/tasks/due-board", {"responsible_id": crm_me(tasks)["id"], "limit": 50}).json()
    assert [b["title"] for b in board["buckets"]] == BUCKETS
    expect(tasks.locator("main section[aria-label]")).to_have_count(len(BUCKETS))
    for bucket in board["buckets"]:
        expect(column_count(tasks, bucket["title"])).to_have_text(str(bucket["count"]))
    for key, task in mine.items():
        bucket = next(b for b in board["buckets"] if b["key"] == key)
        assert task["id"] in [i["id"] for i in bucket["items"]], f"{task['title']} не в «{bucket['title']}» по API"
        expect(column(tasks, bucket["title"]).locator("[draggable=true]").filter(has_text=task["title"])).to_be_visible()
    overdue = dt.date.fromisoformat(mine["overdue"]["due_date"])
    expect(task_card(tasks, mine["overdue"]["title"])).to_contain_text(overdue.strftime("%d.%m.%Y"))


# Кейс: Новая задача через форму — описание, приоритет, ответственный, срок — встаёт в «Сегодня»
def test_create_task_via_form(tasks: Page):
    title, about = f"{QA_PREFIX} {tag()}", f"{QA_PREFIX} описание {tag()}"
    before = int(column_count(tasks, "Сегодня").inner_text())

    form = new_task_form(tasks)
    form.get_by_placeholder(re.compile("^Необязательно")).fill(title)
    form.get_by_placeholder(re.compile("^Описание задачи")).fill(about)
    form.get_by_role("button", name="Средний", exact=True).click()
    pick(tasks, "Высокий")
    form.get_by_role("button", name="— Не назначен —").click()
    pick(tasks, me_name(tasks))
    # поля даты по порядку: начало, срок, факт. начата, факт. сделана
    form.locator("input[type=date]").nth(1).fill(server_today(tasks).isoformat())
    with task_post(tasks) as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    created = track(tasks, resp.value.json())

    assert (created["title"], created["description"], created["priority"], created["responsible_id"],
            created["due_date"], created["global_status"]) == (
        title, about, "high", crm_me(tasks)["id"], server_today(tasks).isoformat(), "new"), created
    expect(form).to_have_count(0)
    expect(column(tasks, "Сегодня").locator("[draggable=true]").filter(has_text=title)).to_be_visible()
    expect(column_count(tasks, "Сегодня")).to_have_text(str(before + 1))


# Кейс: Задача без названия получает название «Задача»
def test_create_without_title(tasks: Page):
    form = new_task_form(tasks)
    form.get_by_role("button", name="— Не назначен —").click()
    pick(tasks, me_name(tasks))
    with task_post(tasks) as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    created = track(tasks, resp.value.json())

    assert created["title"] == "Задача", created


# Кейс: «Отмена» в заполненной форме спрашивает «Закрыть без сохранения?» и ничего не создаёт
def test_create_cancel(tasks: Page):
    sent = []
    tasks.on("request", lambda r: r.url.endswith("/api/crm/tasks/") and r.method == "POST" and sent.append(r))
    title = f"{QA_PREFIX} {tag()}"
    form = new_task_form(tasks)
    name = form.get_by_placeholder(re.compile("^Необязательно"))
    name.fill(title)

    form.get_by_role("button", name="Отмена", exact=True).click()
    confirm = overlay(tasks, "Закрыть без сохранения?")
    expect(confirm).to_contain_text("Заполненное в форме не сохранится")
    confirm.get_by_role("button", name="Вернуться к форме").click()
    expect(name).to_have_value(title)

    form.get_by_role("button", name="Отмена", exact=True).click()
    overlay(tasks, "Закрыть без сохранения?").get_by_role("button", name="Закрыть", exact=True).click()
    expect(tasks.locator("div.fixed.inset-0")).to_have_count(0)
    assert not sent, "«Отмена» отправила задачу"


# Кейс: Карточка задачи открывается кликом и по ссылке, крестик убирает её из адреса
def test_card_open_link_close(tasks: Page):
    t = create_task(tasks, description=f"{QA_PREFIX} текст")
    reload_board(tasks)

    task_card(tasks, t["title"]).click()
    expect(tasks).to_have_url(re.compile(rf"[?&]task={t['id']}(&|$)"))
    card = overlay(tasks, t["title"])
    expect(card).to_contain_text(f"{QA_PREFIX} текст")
    expect(card).to_contain_text(me_name(tasks))

    tasks.reload()
    card = overlay(tasks, t["title"])
    expect(card.get_by_role("button", name="Инфо")).to_be_visible(timeout=20_000)
    card.get_by_role("button", name="Закрыть", exact=True).click()
    expect(tasks).not_to_have_url(re.compile(r"[?&]task="))
    expect(card).to_have_count(0)


# Кейс: Статус «В работе» из карточки — API и отметка «фактически начата»
def test_change_status(tasks: Page):
    t = create_task(tasks)
    card = open_card(tasks, t)

    card.get_by_role("button", name="Новая", exact=True).last.click()
    with tasks.expect_response(lambda r: r.url.endswith(f"/api/crm/tasks/{t['id']}") and r.request.method == "PUT") as resp:
        pick(tasks, "В работе")
    assert resp.value.ok, resp.value.text()

    expect(card.get_by_role("button", name="В работе", exact=True)).to_be_visible()
    saved = crm_get(tasks, f"/tasks/{t['id']}").json()
    assert saved["global_status"] == "in_progress", saved
    assert saved["started_at"], f"«Фактически начата» не поставилась сама: {saved}"


# Кейс: Приоритет из карточки сохраняется
def test_change_priority(tasks: Page):
    t = create_task(tasks)
    card = open_card(tasks, t)

    card.get_by_role("button", name="Средний", exact=True).last.click()
    with tasks.expect_response(lambda r: r.url.endswith(f"/api/crm/tasks/{t['id']}") and r.request.method == "PUT") as resp:
        pick(tasks, "Высокий")
    assert resp.value.ok, resp.value.text()

    expect(card.get_by_role("button", name="Высокий", exact=True)).to_be_visible()
    assert crm_get(tasks, f"/tasks/{t['id']}").json()["priority"] == "high"


# Кейс: «Завершить задачу» — статус «Завершена», дата завершения, кнопки больше нет
def test_finish_task(tasks: Page):
    t = create_task(tasks)
    card = open_card(tasks, t)

    with tasks.expect_response(lambda r: r.url.endswith(f"/api/crm/tasks/{t['id']}") and r.request.method == "PUT") as resp:
        card.get_by_role("button", name="Завершить задачу").click()
    assert resp.value.ok, resp.value.text()
    assert resp.value.request.post_data_json == {"global_status": "done"}

    expect(card.get_by_role("button", name="Завершить задачу")).to_have_count(0)
    expect(card.get_by_role("button", name="Завершена", exact=True)).to_be_visible()
    saved = crm_get(tasks, f"/tasks/{t['id']}").json()
    assert saved["global_status"] == "done" and saved["completed_at"] and saved["completed_on"], saved


# Кейс: Комментарий к задаче — API и вкладка «Активность»
def test_comment(tasks: Page):
    t = create_task(tasks)
    card = open_card(tasks, t)
    text = f"{QA_PREFIX} комментарий {tag()}"

    card.get_by_placeholder(re.compile("^Комментарий")).fill(text)
    with tasks.expect_response(lambda r: r.url.endswith(f"/tasks/{t['id']}/comments") and r.request.method == "POST") as resp:
        card.get_by_role("button", name="Отправить", exact=True).click()
    assert resp.value.ok, resp.value.text()

    comments = crm_get(tasks, f"/tasks/{t['id']}/comments").json()["items"]
    assert [(c["body"], c["created_by"]) for c in comments] == [(text, crm_me(tasks)["id"])], comments
    card.get_by_role("button", name="Активность").click()
    expect(card.get_by_text(text, exact=True)).to_be_visible()
    expect(card).to_contain_text("оставил(а) комментарий")


# Кейс: Подзадача из карточки — привязана к родителю и видна в блоке «Подзадачи»
def test_add_subtask(tasks: Page):
    parent = create_task(tasks)
    card = open_card(tasks, parent)
    title = f"{QA_PREFIX} подзадача {tag()}"

    card.get_by_role("button", name="Добавить подзадачу").click()
    form = overlay(tasks, "Новая подзадача")
    expect(form).to_contain_text(f"Подзадача для: «{parent['title']}»")
    form.get_by_placeholder(re.compile("^Необязательно")).fill(title)
    responsible = form.get_by_role("button", name="— Не назначен —")
    if responsible.count():
        responsible.click()
        pick(tasks, me_name(tasks))
    with task_post(tasks) as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    sub = track(tasks, resp.value.json())

    assert sub["parent_task_id"] == parent["id"], sub
    assert [s["id"] for s in crm_get(tasks, f"/tasks/{parent['id']}/subtasks").json()] == [sub["id"]]
    card = overlay(tasks, parent["title"])
    expect(card.get_by_text(re.compile(r"^Подзадачи \(\d+/1\)$"))).to_be_visible()
    expect(card.get_by_text(title, exact=True)).to_be_visible()


# Кейс: Задача в архив из карточки и обратно из «Списка» → «Архивные»
def test_archive_and_restore(tasks: Page):
    t = create_task(tasks)
    card = open_card(tasks, t)

    card.get_by_role("button", name="Архивировать").click()
    confirm = overlay(tasks, "Архивировать задачу?")
    expect(confirm).to_contain_text("вернуть можно из фильтра «Архивные»")
    with tasks.expect_response(lambda r: r.url.endswith(f"/api/crm/tasks/{t['id']}") and r.request.method == "DELETE") as resp:
        confirm.get_by_role("button", name="Продолжить").click()
    assert resp.value.ok, resp.value.text()
    assert crm_get(tasks, f"/tasks/{t['id']}").json()["is_archived"] is True
    reload_board(tasks)
    expect(task_card(tasks, t["title"])).to_have_count(0)

    # доска сроков архив не показывает — только «Список»
    tasks.get_by_role("button", name="Список").click()
    tasks.get_by_role("button", name="Архивные", exact=True).click()
    row = tasks.locator("main tbody tr").filter(has_text=t["title"])
    with tasks.expect_response(lambda r: r.url.endswith(f"/tasks/{t['id']}/restore") and r.request.method == "POST") as resp:
        row.get_by_title("Разархивировать").click()
    assert resp.value.ok, resp.value.text()
    expect(row).to_have_count(0)
    assert crm_get(tasks, f"/tasks/{t['id']}").json()["is_archived"] is False

    tasks.get_by_role("button", name="Активные", exact=True).click()
    expect(tasks.locator("main tbody tr").filter(has_text=t["title"])).to_be_visible()


# Кейс: «Отмена» в окне архива оставляет задачу
def test_archive_cancel(tasks: Page):
    t = create_task(tasks)
    card = open_card(tasks, t)

    card.get_by_role("button", name="Архивировать").click()
    overlay(tasks, "Архивировать задачу?").get_by_role("button", name="Отмена").click()

    expect(tasks.get_by_text("Архивировать задачу?")).to_have_count(0)
    expect(card.get_by_role("button", name="Архивировать")).to_be_visible()
    assert crm_get(tasks, f"/tasks/{t['id']}").json()["is_archived"] is False


# Кейс: «Удалить навсегда» — задачи больше нет
def test_delete_forever(tasks: Page):
    t = create_task(tasks)
    card = open_card(tasks, t)

    card.get_by_role("button", name="Удалить навсегда").click()
    confirm = overlay(tasks, "Удалить задачу навсегда?")
    expect(confirm).to_contain_text(f"«{t['title']}»")
    expect(confirm).to_contain_text("отменить нельзя")
    with tasks.expect_response(lambda r: r.url.endswith(f"/tasks/{t['id']}/permanent") and r.request.method == "DELETE") as resp:
        confirm.get_by_role("button", name="Удалить навсегда").click()
    assert resp.value.ok, resp.value.text()

    expect(tasks.get_by_text("Удалить задачу навсегда?")).to_have_count(0)
    reload_board(tasks)
    expect(task_card(tasks, t["title"])).to_have_count(0)
    assert crm_get(tasks, f"/tasks/{t['id']}").status == 404


# Кейс: Перетаскивание карточки в «Завтра» ставит срок на завтра
def test_drag_to_tomorrow(tasks: Page):
    t = create_task(tasks)
    tomorrow = (server_today(tasks) + dt.timedelta(days=1)).isoformat()
    reload_board(tasks)

    with tasks.expect_response(lambda r: f"/api/crm/tasks/{t['id']}" in r.url and r.request.method in ("PUT", "PATCH")) as resp:
        task_card(tasks, t["title"]).drag_to(column(tasks, "Завтра"))
    assert resp.value.ok, resp.value.text()

    expect(column(tasks, "Завтра").locator("[draggable=true]").filter(has_text=t["title"])).to_be_visible()
    assert crm_get(tasks, f"/tasks/{t['id']}").json()["due_date"] == tomorrow


# Кейс: Фильтр «Статус» — на доске только задачи с выбранным статусом, выбор в адресе
def test_status_filter(tasks: Page):
    fresh = create_task(tasks)
    working = create_task(tasks, global_status="in_progress")
    reload_board(tasks)
    expect(task_card(tasks, fresh["title"])).to_be_visible()

    tasks.get_by_role("button", name="Статус", exact=True).click()
    pick(tasks, "В работе")
    tasks.keyboard.press("Escape")

    expect(tasks).to_have_url(re.compile(r"[?&]global_status=in_progress"))
    expect(task_card(tasks, working["title"])).to_be_visible()
    expect(task_card(tasks, fresh["title"])).to_have_count(0)
    reload_board(tasks)
    expect(task_card(tasks, working["title"])).to_be_visible()
    expect(task_card(tasks, fresh["title"])).to_have_count(0)


# Кейс: «Мои» по умолчанию — только свои задачи; «Все» — задачи всей команды
def test_mine_and_all(tasks: Page):
    create_task(tasks)
    reload_board(tasks)
    expect(tasks.get_by_title("Включено «Мои» — показаны только ваши задачи")).to_be_visible()
    cards = tasks.locator("main [draggable=true]")
    expect(cards.first).to_be_visible()
    for owner in cards.locator("span.truncate").all_inner_texts():
        assert owner == me_name(tasks), f"в «Мои» чужая задача: {owner}"

    with tasks.expect_response(lambda r: "/tasks/due-board" in r.url) as resp:
        tasks.get_by_role("button", name="Все", exact=True).first.click()
    assert "responsible_id" not in resp.value.url, resp.value.url

    expect(tasks.get_by_title("Включено «Мои» — показаны только ваши задачи")).to_have_count(0)
    board = crm_get(tasks, "/tasks/due-board", {"limit": 50}).json()
    for bucket in board["buckets"]:
        expect(column_count(tasks, bucket["title"])).to_have_text(str(bucket["count"]))


# Кейс: Поиск по названию оставляет на доске только найденную задачу
def test_search(tasks: Page):
    found, other = create_task(tasks), create_task(tasks)
    reload_board(tasks)
    expect(task_card(tasks, other["title"])).to_be_visible()

    with tasks.expect_response(lambda r: "/tasks/due-board" in r.url and "search=" in r.url):
        tasks.get_by_placeholder("Поиск по названию...").fill(found["title"].split()[-1])

    expect(task_card(tasks, found["title"])).to_be_visible()
    expect(task_card(tasks, other["title"])).to_have_count(0)


# Кейс: «Список» — строка задачи совпадает с API
def test_list_view(tasks: Page):
    t = create_task(tasks, priority="high", global_status="in_progress")
    reload_board(tasks)

    tasks.get_by_role("button", name="Список").click()

    expect(tasks.locator("main thead")).to_contain_text("Ответственный")
    row = tasks.locator("main tbody tr").filter(has_text=t["title"])
    expect(row).to_contain_text("В работе")
    expect(row).to_contain_text("Высокий")
    expect(row).to_contain_text(me_name(tasks))
