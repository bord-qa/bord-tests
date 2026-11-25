"""Блок 82. Запросы в BORD — /admin/feature-requests.

Снято 25.11.2026 в видимом MCP-браузере:
- список = GET /api/admin/feature-requests {total, items[{id, created_at, author_name, kind, kind_label, title,
  status, status_label, request_number, answer, plan, last_action…}]};
- над таблицей счётчики «Все N» / «Открытые N» / «Отклонён N» (по статусам), фильтры «Все типы» (Новая
  возможность / Улучшение / Интеграция / Ошибка / Сообщение), «Все системы» (hub · N, crm · N), даты «с» / «по»;
- таблица «Когда · Кто · Тип · Запрос · Номер · Статус · План/срок · Ответ BORD · Действия», номер — «№306»;
- в шапке «Все сотрудники», «Ссылка», «Обновить».
Блок только читает: новый запрос не создаём (он уходит команде BORD).
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_get
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

API = "/api/admin/feature-requests"


@pytest.fixture
def requests_page(auth_page: Page) -> tuple[Page, dict]:
    with auth_page.expect_response(lambda r: API in r.url and r.request.method == "GET") as resp:
        auth_page.goto("/admin/feature-requests")
    assert resp.value.ok, resp.value.status
    expect(auth_page.locator("main table")).to_be_visible(timeout=20_000)
    settle(auth_page)
    return auth_page, api_get(auth_page, f"{API}?limit=200").json()


# Кейс: Счётчики и строки = API (номер, тип, статус, ответ BORD)
@pytest.mark.smoke
def test_list_matches_api(requests_page):
    page, data = requests_page
    items = data["items"]
    main = page.locator("main")
    expect(main.get_by_role("button", name=re.compile(rf"^Все\s*{data['total']}$"))).to_be_visible()
    by_status = {}
    for i in items:
        by_status[i["status_label"]] = by_status.get(i["status_label"], 0) + 1
    for label, n in by_status.items():
        expect(main.get_by_role("button", name=re.compile(rf"^{re.escape(label)}\s*{n}$")), label).to_be_visible()
    for i in items[:10]:
        row = main.locator("tbody tr").filter(has_text=f"№{i['request_number']}")
        expect(row, str(i["id"])).to_have_count(1)
        expect(row).to_contain_text(i["kind_label"])
        expect(row).to_contain_text(i["status_label"])
        if i["answer"]:
            expect(row).to_contain_text(i["answer"][:40])


# Кейс: Вкладка статуса — в таблице только запросы этого статуса
def test_status_tab_filters(requests_page):
    page, data = requests_page
    if not data["items"]:
        pytest.skip("в BORD QA нет запросов")
    label = data["items"][0]["status_label"]
    main = page.locator("main")
    main.get_by_role("button", name=re.compile(rf"^{re.escape(label)}\s*\d+$")).click()
    settle(page)
    expected = [i for i in data["items"] if i["status_label"] == label]
    expect(main.locator("tbody tr").filter(has_text="№")).to_have_count(len(expected))
