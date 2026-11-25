"""Блок 79. Журнал действий — /admin/audit.

Снято 25.11.2026 в видимом MCP-браузере:
- таблица «Когда · Кто · Система · Действие · Что · Результат · Откуда» = GET /api/admin/audit-journal/events
  (новые сверху; at, actor_name, module_title, action_title, what, outcome_title, source_title);
- в шапке фильтры «Все люди» / «Все системы» / «Все действия» / «Откуда угодно» и «Обновить»; варианты фильтров =
  GET /api/admin/audit-journal/filters {people[{id,name}], modules{key:title}, actions{key:title}, sources{key:title}};
  выбор фильтра → events?actor_id= / module= / action= / source=.
Блок только читает.
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_get
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

EVENTS = "/api/admin/audit-journal/events"


@pytest.fixture
def audit(auth_page: Page) -> Page:
    with auth_page.expect_response(lambda r: EVENTS in r.url and r.request.method == "GET"):
        auth_page.goto("/admin/audit")
    expect(auth_page.locator("main table")).to_be_visible(timeout=20_000)
    settle(auth_page)
    return auth_page


def header_filter(page: Page, current: str):
    return page.locator("header button:visible").filter(has_text=re.compile(rf"^\s*{re.escape(current)}\s*$")).first


def rows(page: Page):
    return page.locator("main tbody tr")


# Кейс: Первые строки журнала = API (кто, система, действие, что, откуда)
@pytest.mark.smoke
def test_rows_match_api(audit: Page):
    events = api_get(audit, f"{EVENTS}?limit=5").json()["items"]
    assert events, "журнал пуст"
    expect(audit.locator("main thead")).to_contain_text(re.compile("Когда.*Кто.*Система.*Действие.*Что.*Результат.*Откуда"))
    # журнал живой (входы тестов пишутся каждую минуту) — сверяем, что каждое из последних событий есть среди строк
    texts = [t.replace("\n", " ") for t in rows(audit).all_inner_texts()[:40]]
    for ev in events[:3]:
        assert any(ev["actor_name"] in t and ev["module_title"] in t and ev["action_title"] in t
                   and ev["source_title"] in t for t in texts), f"нет строки события {ev['id']}: {ev}"


# Кейс: Фильтры в шапке — варианты = /filters
def test_filter_options_match_api(audit: Page):
    data = api_get(audit, "/api/admin/audit-journal/filters").json()
    for current, values in (("Все люди", [p["name"] for p in data["people"]]),
                            ("Все системы", list(data["modules"].values())),
                            ("Все действия", list(data["actions"].values())),
                            ("Откуда угодно", list(data["sources"].values()))):
        header_filter(audit, current).click()
        options = audit.get_by_role("option")
        expect(options.first).to_be_visible()
        shown = [o.strip() for o in options.all_inner_texts()]
        missing = [v for v in values if v not in shown]
        assert not missing, f"«{current}»: нет вариантов {missing} (на экране {shown})"
        audit.keyboard.press("Escape")


# Кейс: Фильтр «Все действия» → «Вход» — запрос с action=login, в таблице только входы
def test_filter_by_action(audit: Page):
    header_filter(audit, "Все действия").click()
    with audit.expect_response(lambda r: EVENTS in r.url and "action=login" in r.url) as resp:
        audit.get_by_role("option", name="Вход", exact=True).click()
    items = resp.value.json()["items"]
    assert items and {i["action"] for i in items} == {"login"}, items[:3]
    expect(rows(audit).first).to_contain_text("Вход")
    for text in rows(audit).all_inner_texts()[:20]:
        assert "Вход" in text, text
