"""Блок 30. CRM — мониторинг (журнал изменений): crm.bord.one/admin/crm/monitoring.

Снято 29.10.2026:
- лента — GET /api/crm/audit/feed?page=1&limit=50[&actor_type][&entity_type]
  [&action][&date_from][&date_to] → {items: [{id, entity_type, entity_id,
  action, changes|diff, user_id, actor_type, actor_label, created_at}], total};
  история одной записи — GET /audit/<entity_type>/<id>;
- источник: «Все / Руками / AI / Автоматизация / Внешняя система (API) /
  Система» → actor_type = — / manual / ai / automation / api / system;
  «Все сущности» → entity_type contact / company / deal / task / event /
  custom_field; «Все действия» → action create / update / delete /
  stage_change / merge; «С даты» / «По дату»;
- события сгруппированы по дням («Сегодня», «Вчера», …); строка — бейдж
  источника («Руками», «Авто»…), кто, «Сделка #<id>» (кнопка — открывает
  карточку сделки; у служебных сущностей вроде «org_settings #51» — просто
  текст), действие («создан», «изменён», «удалён»), изменения «Название:
  старое → новое», время по часам браузера;
- по 50 событий на страницу, внизу «N записей · стр. 1 / M» и стрелки
  (lucide chevron-left / chevron-right) → page=2;
- запросы тест-учётки к API (как и экран) пишутся как «Руками».

Ленту пополняют все, кто работает в BORD QA, поэтому тесты сверяют свои
события («QA Autotest» сделка, созданная и переименованная тестом), а
общую ленту — за прошедший день, который уже не меняется.
"""
import datetime as dt
import random
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import CRM, QA_PREFIX, crm_delete, crm_get, crm_me, crm_post, crm_put, open_crm
from helpers.ui import screen_button

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/monitoring"
ACTORS = {"Руками": "manual", "AI": "ai", "Автоматизация": "automation",
          "Внешняя система (API)": "api", "Система": "system"}
# бейдж в строке короче кнопки фильтра
BADGES = {"Автоматизация": "Авто"}
ENTITIES = {"Сделки": ("deal", "Сделка"), "Задачи": ("task", "Задача"), "Контакты": ("contact", "Контакт")}
ACTIONS = {"Создание": ("create", "создан"), "Изменение": ("update", "изменён"), "Удаление": ("delete", "удалён")}


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


def main_pipeline(page: Page) -> dict:
    return next(p for p in crm_get(page, "/pipeline-statuses/pipelines").json()["items"] if p["is_main"])


def make_deal(page: Page) -> dict:
    resp = crm_post(page, "/deals/", {"title": f"{QA_PREFIX} {tag()}", "pipeline_id": main_pipeline(page)["id"],
                                      "responsible_id": crm_me(page)["id"], "amount": random.randint(100, 999)})
    assert resp.status == 201, resp.text()
    page._qa_deals.append(resp.json()["id"])
    return resp.json()


def rename_deal(page: Page, deal: dict) -> str:
    new_title = f"{QA_PREFIX} {tag()}"
    resp = crm_put(page, f"/deals/{deal['id']}", {"title": new_title})
    assert resp.ok, resp.text()
    return new_title


def history(page: Page, entity_type: str, entity_id: int) -> list[dict]:
    return crm_get(page, f"/audit/{entity_type}/{entity_id}", {"limit": 100}).json()["items"]


def local_time(page: Page, iso: str) -> dt.datetime:
    """Время события по часам браузера — так его пишет лента."""
    offset = -page.evaluate("new Date().getTimezoneOffset()")
    return dt.datetime.fromisoformat(iso).astimezone(dt.timezone(dt.timedelta(minutes=offset)))


@pytest.fixture
def monitoring(auth_page: Page):
    auth_page._qa_deals = []
    yield auth_page
    for deal_id in auth_page._qa_deals:
        crm_delete(auth_page, f"/deals/{deal_id}")


def open_monitoring(page: Page):
    open_crm(page, PAGE)
    expect(screen_button(page, "Все сущности", exact=True)).to_be_visible()


def rows(page: Page):
    """Строки событий: белые карточки с бейджем источника (кнопка сущности есть не у всех)."""
    return page.locator("main div.flex.items-start.gap-3.rounded-lg")


def row_entity_ids(page: Page) -> list[int]:
    return [int(re.search(r"#(\d+)", text).group(1)) for text in rows(page).all_inner_texts()]


def pager(page: Page):
    return page.locator("main").get_by_text(re.compile(r"^стр\. \d+ / \d+$"))


def entity_rows(page: Page, label: str, entity_id: int):
    return rows(page).filter(has=page.get_by_role("button", name=f"{label} #{entity_id}", exact=True))


def feed_request(page: Page, param: str):
    return page.expect_request(lambda r: "/api/crm/audit/feed" in r.url and param in r.url)


def pick(page: Page, option: str):
    page.locator("div.shadow-xl[style*='position: fixed']").last.get_by_text(option, exact=True).click()


# Кейс: Своя сделка в ленте — «создан» и «изменён» с «Название: старое → новое», «Руками», кто и время = API
def test_own_deal_events(monitoring: Page):
    deal = make_deal(monitoring)
    new_title = rename_deal(monitoring, deal)
    open_monitoring(monitoring)

    events = history(monitoring, "deal", deal["id"])
    assert [e["action"] for e in events] == ["update", "create"], events
    assert {e["actor_type"] for e in events} == {"manual"}, events
    mine = entity_rows(monitoring, "Сделка", deal["id"])
    expect(mine).to_have_count(2)
    updated, created = mine.nth(0), mine.nth(1)
    for row, event in ((updated, events[0]), (created, events[1])):
        expect(row).to_contain_text("Руками")
        expect(row).to_contain_text(event["actor_label"])
        expect(row).to_contain_text(f"{local_time(monitoring, event['created_at']):%H:%M}")
    expect(updated).to_contain_text("изменён")
    expect(updated).to_contain_text(f"Название: {deal['title']} → {new_title}")
    expect(created).to_contain_text("создан")
    expect(created).to_contain_text(f"Название: {deal['title']}")


# Кейс: Удалённая сделка — событие «удалён» со старым названием
def test_deleted_deal_event(monitoring: Page):
    deal = make_deal(monitoring)
    assert crm_delete(monitoring, f"/deals/{deal['id']}").ok
    open_monitoring(monitoring)

    assert history(monitoring, "deal", deal["id"])[0]["action"] == "delete"
    deleted = entity_rows(monitoring, "Сделка", deal["id"]).filter(has_text="удалён")
    expect(deleted).to_have_count(1)
    expect(deleted).to_contain_text(f"Название: {deal['title']}")


# Кейс: «Сделка #id» в ленте открывает карточку этой сделки
def test_entity_opens_deal_card(monitoring: Page):
    deal = make_deal(monitoring)
    open_monitoring(monitoring)

    entity_rows(monitoring, "Сделка", deal["id"]).first.get_by_role(
        "button", name=f"Сделка #{deal['id']}", exact=True).click()
    expect(monitoring).to_have_url(re.compile(rf"/admin/crm/deals\?.*\bid={deal['id']}\b"))
    expect(monitoring.get_by_role("heading", name=deal["title"], level=2)).to_be_visible()


# Кейс: Фильтр источника — actor_type в запросе, у всех строк бейдж этого источника
@pytest.mark.parametrize("label", list(ACTORS))
def test_actor_filter(monitoring: Page, label: str):
    open_monitoring(monitoring)

    with feed_request(monitoring, f"actor_type={ACTORS[label]}"):
        screen_button(monitoring, label, exact=True).first.click()
    total = crm_get(monitoring, "/audit/feed", {"actor_type": ACTORS[label], "limit": 50}).json()["total"]
    if not total:
        expect(rows(monitoring)).to_have_count(0)
        return
    expect(rows(monitoring).first).to_be_visible()
    badges = rows(monitoring).locator("xpath=./span[1]").all_inner_texts()
    assert badges and {b.strip() for b in badges} == {BADGES.get(label, label)}, set(badges)


# Кейс: «Руками» + своя правка видна; «Автоматизация» её не показывает
def test_own_event_only_under_manual(monitoring: Page):
    deal = make_deal(monitoring)
    open_monitoring(monitoring)

    with feed_request(monitoring, "actor_type=manual"):
        screen_button(monitoring, "Руками", exact=True).first.click()
    expect(entity_rows(monitoring, "Сделка", deal["id"])).to_have_count(1)
    with feed_request(monitoring, "actor_type=automation"):
        screen_button(monitoring, "Автоматизация", exact=True).first.click()
    expect(entity_rows(monitoring, "Сделка", deal["id"])).to_have_count(0)


# Кейс: Фильтр сущности — entity_type в запросе, у всех строк сущность этого типа
@pytest.mark.parametrize("option", list(ENTITIES))
def test_entity_filter(monitoring: Page, option: str):
    entity_type, label = ENTITIES[option]
    open_monitoring(monitoring)

    screen_button(monitoring, "Все сущности", exact=True).click()
    with feed_request(monitoring, f"entity_type={entity_type}"):
        pick(monitoring, option)
    if not crm_get(monitoring, "/audit/feed", {"entity_type": entity_type, "limit": 1}).json()["total"]:
        expect(rows(monitoring)).to_have_count(0)
        return
    expect(rows(monitoring).first).to_be_visible()
    names = [re.search(r"\S+ #\d+", t).group(0) for t in rows(monitoring).all_inner_texts()]
    assert names and all(n.startswith(f"{label} #") for n in names), names


# Кейс: Фильтр действия — action в запросе, у всех строк это действие
@pytest.mark.parametrize("option", list(ACTIONS))
def test_action_filter(monitoring: Page, option: str):
    action, word = ACTIONS[option]
    make_deal(monitoring)  # хотя бы одно «создан» сегодня
    open_monitoring(monitoring)

    screen_button(monitoring, "Все действия", exact=True).click()
    with feed_request(monitoring, f"action={action}"):
        pick(monitoring, option)
    if not crm_get(monitoring, "/audit/feed", {"action": action, "limit": 1}).json()["total"]:
        expect(rows(monitoring)).to_have_count(0)
        return
    expect(rows(monitoring).first).to_be_visible()
    for row in rows(monitoring).all()[:20]:
        expect(row).to_contain_text(word)


# Кейс: Период «С даты / По дату» — даты в запросе, лента за прошедший день совпадает с API
def test_date_filter_matches_api(monitoring: Page):
    open_monitoring(monitoring)
    day = dt.date.today() - dt.timedelta(days=1)

    with feed_request(monitoring, f"date_from={day.isoformat()}"):
        monitoring.get_by_role("textbox", name="С даты").fill(day.isoformat())
    with feed_request(monitoring, f"date_to={day.isoformat()}"):
        monitoring.get_by_role("textbox", name="По дату").fill(day.isoformat())

    data = crm_get(monitoring, "/audit/feed", {"date_from": day.isoformat(), "date_to": day.isoformat(),
                                               "page": 1, "limit": 50}).json()
    expect(rows(monitoring)).to_have_count(len(data["items"]))
    expect(monitoring.locator("main h2")).to_have_text(["Вчера"], ignore_case=True)
    assert row_entity_ids(monitoring) == [i["entity_id"] for i in data["items"]]


# Кейс: Пагинация — «N записей · стр. 1 / M» = API, стрелка вперёд грузит page=2 со следующими событиями
def test_pagination(monitoring: Page):
    open_monitoring(monitoring)
    day = dt.date.today() - dt.timedelta(days=1)
    with feed_request(monitoring, f"date_to={day.isoformat()}"):
        monitoring.get_by_role("textbox", name="С даты").fill(day.isoformat())
        monitoring.get_by_role("textbox", name="По дату").fill(day.isoformat())
    params = {"date_from": day.isoformat(), "date_to": day.isoformat(), "limit": 50}
    total = crm_get(monitoring, "/audit/feed", {**params, "page": 1}).json()["total"]
    if total <= 50:
        pytest.skip(f"за {day} всего {total} событий — одна страница")
    pages = -(-total // 50)

    expect(monitoring.locator("main")).to_contain_text(f"{total} записей")
    expect(pager(monitoring)).to_have_text(f"стр. 1 / {pages}")
    arrows = pager(monitoring).locator("xpath=ancestor::div[.//button][1]")
    expect(arrows.locator("button:has(svg.lucide-chevron-left)")).to_be_disabled()
    with feed_request(monitoring, "page=2"):
        arrows.locator("button:has(svg.lucide-chevron-right)").click()
    expect(pager(monitoring)).to_have_text(f"стр. 2 / {pages}")
    second = crm_get(monitoring, "/audit/feed", {**params, "page": 2}).json()["items"]
    expect(rows(monitoring)).to_have_count(len(second))
    assert row_entity_ids(monitoring) == [i["entity_id"] for i in second]
