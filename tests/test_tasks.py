"""Блок 9. Задачи: app.bord.one/admin/tasks.

Снято 22.10.2026:
- API: GET /api/tasks/?status=active|done|all&limit=100 → {items, total},
  /api/tasks/summary → {active, overdue, mine, unassigned} (счётчики-кнопки над
  списком), /api/tasks/meta → people, directions, statuses, priorities, me;
- «Новая задача» → окно (helpers/modal.py): «Что нужно сделать», «Подробности»,
  «Кому поручено», «Срок» (input[type=date]), «Направление», «Важность»,
  «Повторять» → «Поставить» → POST /api/tasks/ → 201; пустое название —
  «Название обязательно» без запроса;
- строка списка (main li): название, «Важно», направление, «Просрочена»,
  описание, исполнитель, «до ДД.ММ.ГГГГ», выпадашка статуса (Новая / В работе /
  Сделана / Отменена → PATCH {status}), «Изменить», «Удалить» → окно
  «Удалить задачу «…»?» (Отмена / Да) → DELETE /api/tasks/<id>;
- повтор: у задачи с repeat_rule «Сделана» порождает следующую (repeat_from_id,
  срок + период), в строке «раз в нед.»;
- карточка (клик по задаче): чек-лист (POST/PATCH /api/tasks/<id>/checklist),
  файлы («Приложить» → POST /api/tasks/<id>/files — 22.10 отвечал 500,
  ecom-100157; 27.10 уже 201), обсуждение;
- обсуждение (снято 27.10.2026): общий сервис комментариев — GET
  /api/comments/?entity_type=task&entity_id=<id>&threaded=false, «Написать по
  задаче» + Enter → POST /api/comments/ {entity_type, entity_id, content} → 201;
  в ленте «<автор> · ДД.ММ.ГГГГ, ЧЧ:ММ» и текст. Правки и удаления в интерфейсе
  нет; из одних пробелов кнопка отправки неактивна. Комментарии переживают
  удаление задачи — уборка удаляет их сама (DELETE /api/comments/<id>);
- «С чего начать» (/api/tasks/onboarding/checklist) считается по задачам
  организации: удалили задачи — пункты снова не сделаны.

Исполнитель в тестах — сама тест-учётка (живому человеку в BORD QA пришло бы
уведомление); «Поручить коллеге» — в test_colleague.py, на второй тест-учётке.
Свои задачи «QA Autotest …» удаляем в любом исходе.
"""
import datetime as dt
import re
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_delete, api_get, api_patch, api_post
from helpers.modal import close_modal, modal
from helpers.wait import settle
from helpers.ui import screen_button, screen_field

pytestmark = pytest.mark.requires_auth

API = "/api/tasks/"
NAME_PREFIX = "QA Autotest"
FILES_BUG = "ecom-100157: POST /api/tasks/<id>/files → 500, файл к задаче не приложить"


def title() -> str:
    return f"{NAME_PREFIX} задача {int(time.time() * 1000)}"


def ru_date(day: dt.date) -> str:
    return day.strftime("%d.%m.%Y")


def me(page: Page) -> dict:
    meta = api_get(page, f"{API}meta").json()
    return next(p for p in meta["people"] if p["id"] == meta["me"])


def create_task(page: Page, **fields) -> dict:
    data = {"title": title(), "priority": "normal", "assignee_id": me(page)["id"], **fields}
    resp = api_post(page, API, data)
    assert resp.status == 201, resp.text()
    return resp.json()


def task(page: Page, task_id: int) -> dict:
    return api_get(page, f"{API}{task_id}").json()


def summary(page: Page) -> dict:
    return api_get(page, f"{API}summary").json()


def delete_qa_tasks(page: Page):
    resp = api_get(page, f"{API}?status=all&limit=100")
    for item in resp.json()["items"] if resp.ok else []:
        if item["title"].startswith(NAME_PREFIX):
            # комментарии с задачей не удаляются — иначе копились бы сиротами
            for comment in comments(page, item["id"]):
                api_delete(page, f"{COMMENTS}{comment['id']}")
            api_delete(page, f"{API}{item['id']}")


COMMENTS = "/api/comments/"


def comments(page: Page, task_id: int) -> list:
    resp = api_get(page, f"{COMMENTS}?entity_type=task&entity_id={task_id}&threaded=false")
    return resp.json() if resp.ok else []


@pytest.fixture
def tasks(auth_page: Page):
    auth_page.goto("/admin/tasks")
    expect(screen_button(auth_page, "Новая задача")).to_be_visible()
    settle(auth_page)
    yield auth_page
    delete_qa_tasks(auth_page)


def reload(page: Page):
    page.reload()
    expect(screen_button(page, "Новая задача")).to_be_visible()
    settle(page)


def row(page: Page, text: str):
    return page.locator("main li").filter(has_text=text)


def counter(page: Page, label: str):
    return page.locator("main button").filter(has_text=re.compile(rf"^\d+\s*{label}$"))


def tab(page: Page, name: str):
    return screen_button(page, name, exact=True)


def form_input(form, label: str):
    return form.get_by_text(label, exact=True).locator("xpath=following-sibling::*[self::input or self::textarea][1]")


def pick(page: Page, scope, current: str, option: str):
    scope.get_by_role("button", name=current, exact=True).click()
    page.get_by_role("option", name=option, exact=True).click()


def set_status(page: Page, task_row, current: str, status: str):
    pick(page, task_row, current, status)


# Кейс: Счётчики над списком совпадают с API
@pytest.mark.smoke
def test_counters_match_summary(tasks: Page):
    yesterday = dt.date.today() - dt.timedelta(days=1)
    create_task(tasks, due_on=yesterday.isoformat())
    create_task(tasks, assignee_id=None)
    reload(tasks)

    data = summary(tasks)
    assert data["overdue"] >= 1 and data["unassigned"] >= 1
    for key, label in (("active", "Активных"), ("overdue", "Просрочено"), ("mine", "На мне"), ("unassigned", "Без исполнителя")):
        expect(counter(tasks, label), label).to_have_text(re.compile(rf"^{data[key]}\s*{label}$"))


# Кейс: Создание задачи через форму
@pytest.mark.writes_data
def test_create_task_via_form(tasks: Page):
    name = title()
    yesterday = dt.date.today() - dt.timedelta(days=1)
    before = summary(tasks)
    screen_button(tasks, "Новая задача").click()
    form = modal(tasks, "Новая задача")
    form_input(form, "Что нужно сделать").fill(name)
    form_input(form, "Подробности").fill("подробности автотеста")
    pick(tasks, form, "Не назначено", me(tasks)["name"])
    form.locator("input[type=date]").fill(yesterday.isoformat())
    pick(tasks, form, "Вне направлений", "Финансы")
    pick(tasks, form, "Обычный", "Высокий")

    with tasks.expect_response(lambda r: r.url.endswith(API) and r.request.method == "POST") as created:
        form.get_by_role("button", name="Поставить").click()

    assert created.value.status == 201, created.value.text()
    assert created.value.request.post_data_json == {
        "title": name, "description": "подробности автотеста", "priority": "high", "direction": "finance",
        "assignee_id": me(tasks)["id"], "due_on": yesterday.isoformat(), "repeat_rule": None, "repeat_every": 1,
    }
    saved = created.value.json()
    assert saved["status"] == "open" and saved["is_overdue"] is True
    expect(form).to_have_count(0)
    line = row(tasks, name)
    for text in ("Важно", "Финансы", "Просрочена", "подробности автотеста", me(tasks)["name"], f"до {ru_date(yesterday)}"):
        expect(line, text).to_contain_text(text)
    after = summary(tasks)
    assert (after["active"], after["overdue"], after["mine"]) == (before["active"] + 1, before["overdue"] + 1, before["mine"] + 1)
    expect(counter(tasks, "Просрочено")).to_have_text(re.compile(rf"^{after['overdue']}\s*Просрочено$"))


# Кейс: Задача без названия не создаётся
def test_empty_title_blocked(tasks: Page):
    posts = []
    tasks.on("request", lambda r: posts.append(r.url) if r.method == "POST" and r.url.endswith(API) else None)
    screen_button(tasks, "Новая задача").click()
    form = modal(tasks, "Новая задача")

    form.get_by_role("button", name="Поставить").click()

    expect(form.get_by_text("Название обязательно", exact=True)).to_be_visible()
    assert posts == []


# Кейс: «Сделана» переносит задачу во вкладку сделанных
@pytest.mark.writes_data
def test_done_moves_task_to_done_tab(tasks: Page):
    item = create_task(tasks)
    reload(tasks)

    with tasks.expect_response(lambda r: r.url.endswith(f"{API}{item['id']}") and r.request.method == "PATCH") as patched:
        set_status(tasks, row(tasks, item["title"]), "Новая", "Сделана")

    assert patched.value.request.post_data_json == {"status": "done"}
    assert task(tasks, item["id"])["status"] == "done"
    expect(row(tasks, item["title"])).to_have_count(0)
    tab(tasks, "Сделанные").click()
    expect(row(tasks, item["title"]).get_by_role("button", name="Сделана", exact=True)).to_be_visible()
    tab(tasks, "Все").click()
    expect(row(tasks, item["title"])).to_have_count(1)


# Кейс: Статус «В работе»
@pytest.mark.writes_data
def test_in_progress_status(tasks: Page):
    item = create_task(tasks)
    reload(tasks)

    set_status(tasks, row(tasks, item["title"]), "Новая", "В работе")

    expect(row(tasks, item["title"]).get_by_role("button", name="В работе", exact=True)).to_be_visible()
    assert task(tasks, item["id"])["status"] == "in_progress"
    reload(tasks)
    expect(row(tasks, item["title"]).get_by_role("button", name="В работе", exact=True)).to_be_visible()


# Кейс: Повторяющаяся задача порождает следующую
@pytest.mark.writes_data
def test_repeating_task_spawns_next(tasks: Page):
    due = dt.date.today() + dt.timedelta(days=3)
    item = create_task(tasks, due_on=due.isoformat(), repeat_rule="weekly", repeat_every=1)
    reload(tasks)
    expect(row(tasks, item["title"])).to_contain_text("раз в нед.")

    set_status(tasks, row(tasks, item["title"]), "Новая", "Сделана")

    next_due = due + dt.timedelta(days=7)
    expect(row(tasks, item["title"])).to_contain_text(f"до {ru_date(next_due)}")
    spawned = [t for t in api_get(tasks, f"{API}?status=active&limit=100").json()["items"] if t.get("repeat_from_id") == item["id"]]
    assert len(spawned) == 1
    assert (spawned[0]["title"], spawned[0]["due_on"], spawned[0]["repeat_rule"]) == (item["title"], next_due.isoformat(), "weekly")
    assert task(tasks, item["id"])["status"] == "done"


# Кейс: Изменение задачи
@pytest.mark.writes_data
def test_edit_task(tasks: Page):
    item = create_task(tasks)
    new_name = f"{item['title']} изм."
    reload(tasks)
    row(tasks, item["title"]).get_by_role("button", name="Изменить").click()

    form = tasks.locator("div.fixed.inset-0").filter(has=tasks.get_by_text("Что нужно сделать", exact=True))
    expect(form_input(form, "Что нужно сделать")).to_have_value(item["title"])
    form_input(form, "Что нужно сделать").fill(new_name)
    pick(tasks, form, "Вне направлений", "Продажи")
    with tasks.expect_response(lambda r: r.url.endswith(f"{API}{item['id']}") and r.request.method == "PATCH") as patched:
        form.get_by_role("button", name=re.compile("^(Сохранить|Поставить)$")).click()

    assert patched.value.ok, patched.value.text()
    saved = task(tasks, item["id"])
    assert (saved["title"], saved["direction"]) == (new_name, "sales")
    expect(row(tasks, new_name)).to_contain_text("Продажи")


# Кейс: Удаление задачи
@pytest.mark.writes_data
def test_delete_task(tasks: Page):
    item = create_task(tasks)
    reload(tasks)

    row(tasks, item["title"]).get_by_role("button", name="Удалить").click()
    dialog = tasks.get_by_role("dialog", name=f"Удалить задачу «{item['title']}»?")
    with tasks.expect_response(lambda r: r.url.endswith(f"{API}{item['id']}") and r.request.method == "DELETE") as deleted:
        dialog.get_by_role("button", name="Да").click()

    assert deleted.value.ok
    expect(row(tasks, item["title"])).to_have_count(0)
    assert api_get(tasks, f"{API}{item['id']}").status == 404


# Кейс: Удаление задачи можно отменить
@pytest.mark.writes_data
def test_delete_can_be_cancelled(tasks: Page):
    item = create_task(tasks)
    reload(tasks)

    row(tasks, item["title"]).get_by_role("button", name="Удалить").click()
    tasks.get_by_role("dialog", name=re.compile("Удалить задачу")).get_by_role("button", name="Отмена").click()

    expect(row(tasks, item["title"])).to_have_count(1)
    assert api_get(tasks, f"{API}{item['id']}").ok


# Кейс: Поиск и фильтры
@pytest.mark.writes_data
def test_search_and_filters(tasks: Page):
    finance = create_task(tasks, direction="finance")
    nobody = create_task(tasks, assignee_id=None)
    reload(tasks)
    main = tasks.locator("main")

    with tasks.expect_response(lambda r: API in r.url and "q=" in r.url):
        screen_field(tasks, "Поиск по названию").fill(finance["title"])
    expect(row(tasks, finance["title"])).to_have_count(1)
    expect(row(tasks, nobody["title"])).to_have_count(0)
    screen_field(tasks, "Поиск по названию").fill(NAME_PREFIX)
    expect(row(tasks, nobody["title"])).to_have_count(1)

    pick(tasks, tasks.locator("header"), "Все направления", "Финансы")
    expect(row(tasks, finance["title"])).to_have_count(1)
    expect(row(tasks, nobody["title"])).to_have_count(0)
    pick(tasks, tasks.locator("header"), "Финансы", "Все направления")

    pick(tasks, tasks.locator("header"), "Все исполнители", me(tasks)["name"])
    expect(row(tasks, finance["title"])).to_have_count(1)
    expect(row(tasks, nobody["title"])).to_have_count(0)


# Кейс: Чек-лист в карточке задачи
@pytest.mark.writes_data
def test_card_checklist(tasks: Page):
    item = create_task(tasks, description="подробности автотеста")
    reload(tasks)

    row(tasks, item["title"]).get_by_role("button").first.click()
    card = modal(tasks, item["title"])
    expect(card).to_contain_text("подробности автотеста")
    with tasks.expect_response(lambda r: r.url.endswith(f"{API}{item['id']}/checklist") and r.request.method == "POST") as added:
        card.get_by_placeholder("Добавить пункт").fill("пункт QA")
        card.get_by_placeholder("Добавить пункт").press("Enter")
    assert added.value.status == 201
    point = added.value.json()
    with tasks.expect_response(lambda r: r.url.endswith(f"checklist/{point['id']}") and r.request.method == "PATCH") as ticked:
        card.locator("input[type=checkbox]").check()
    assert ticked.value.request.post_data_json == {"is_done": True}
    expect(card.get_by_role("heading", level=3, name=re.compile("Чек-лист · 1 из 1", re.I))).to_be_visible()
    items = api_get(tasks, f"{API}{item['id']}/checklist").json()
    assert [(i["text"], i["is_done"]) for i in items] == [("пункт QA", True)]
    close_modal(tasks, item["title"])


# Кейс: Комментарий в обсуждении задачи
@pytest.mark.writes_data
def test_card_comment(tasks: Page):
    item = create_task(tasks)
    reload(tasks)
    row(tasks, item["title"]).get_by_role("button").first.click()
    card = modal(tasks, item["title"])
    box = card.get_by_placeholder("Написать по задаче")
    send = box.locator("xpath=following-sibling::button[1]")

    box.fill("   ")
    expect(send).to_be_disabled()

    text = f"Комментарий QA {int(time.time())}"
    with tasks.expect_response(lambda r: r.url.endswith(COMMENTS) and r.request.method == "POST") as posted:
        box.fill(text)
        box.press("Enter")

    assert posted.value.status == 201, posted.value.text()
    assert posted.value.request.post_data_json == {"entity_type": "task", "entity_id": item["id"], "content": text}
    author = me(tasks)["name"]
    entry = card.locator("li").filter(has_text=text)
    expect(entry).to_have_count(1)
    expect(entry).to_contain_text(re.compile(rf"{re.escape(author)} · \d{{2}}\.\d{{2}}\.\d{{4}}, \d{{2}}:\d{{2}}"))
    expect(box).to_have_value("")
    saved = comments(tasks, item["id"])
    assert [(c["content"], c["author_name"]) for c in saved] == [(text, author)]

    # лента читается с сервера, а не только из памяти формы
    close_modal(tasks, item["title"])
    reload(tasks)
    row(tasks, item["title"]).get_by_role("button").first.click()
    expect(modal(tasks, item["title"]).locator("li").filter(has_text=text)).to_have_count(1)


# Кейс: Файл в карточке задачи
@pytest.mark.writes_data
def test_card_file_upload(tasks: Page, tmp_path):
    item = create_task(tasks)
    reload(tasks)
    row(tasks, item["title"]).get_by_role("button").first.click()
    card = modal(tasks, item["title"])
    file = tmp_path / "qa-autotest.txt"
    file.write_text("файл автотеста", encoding="utf-8")

    with tasks.expect_response(lambda r: r.url.endswith(f"{API}{item['id']}/files") and r.request.method == "POST") as uploaded:
        # «Приложить» — подпись к скрытому input[type=file]
        card.locator("input[type=file]").set_input_files(str(file))

    if uploaded.value.status == 500:
        pytest.xfail(FILES_BUG)
    assert uploaded.value.ok, uploaded.value.text()
    expect(card.get_by_text("qa-autotest.txt")).to_be_visible()
    expect(card.get_by_text("ничего не приложено")).to_have_count(0)
    assert "qa-autotest.txt" in api_get(tasks, f"{API}{item['id']}/files").text()


# Кейс: «С чего начать» следует за задачами
@pytest.mark.writes_data
def test_onboarding_checklist_follows_tasks(tasks: Page):
    checklist = api_get(tasks, f"{API}onboarding/checklist").json()
    if checklist["dismissed"]:
        pytest.skip("блок «С чего начать» скрыт в организации")
    item = create_task(tasks, due_on=(dt.date.today() + dt.timedelta(days=5)).isoformat())
    assert api_patch(tasks, f"{API}{item['id']}", {"status": "done"}).ok
    reload(tasks)

    done = {i["key"]: i["done"] for i in api_get(tasks, f"{API}onboarding/checklist").json()["items"]}
    assert done["first_task"] and done["due"] and done["done"], done
    region = tasks.get_by_role("region", name="С чего начать")
    for point in ("Поставьте первую задачу", "Поставьте срок", "Закройте задачу"):
        expect(region.get_by_role("link", name=re.compile(rf"^Сделано {point}")), point).to_be_visible()
