"""Блок 83. CRM — «Свободные задачи» (/admin/crm/tasks/free, пункт ядра с ~20.11).

Снято 25.11.2026 в видимом MCP-браузере:
- «Задачи без ответственного. Их видит вся команда — возьмите ту, которую сделаете, и она станет вашей.»;
  пусто — «Свободных задач нет — у каждой есть ответственный.»;
- карточка: название, приоритет («Средний»), «поставил(а): <автор>», «Взять себе», «Открыть задачу»;
- «Взять себе» → POST /tasks/<id>/take → responsible_id = я, задача уходит из списка.
Задача своя «QA Autotest …» (POST /tasks/ без responsible_id), после теста — DELETE /tasks/<id>/permanent.
"""
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import CRM, QA_PREFIX, crm_delete, crm_get, crm_me, crm_post, crm_ready

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/tasks/free"


@pytest.fixture
def free_task(auth_page: Page):
    auth_page.goto(CRM + PAGE)
    crm_ready(auth_page)
    task = crm_post(auth_page, "/tasks/", {"title": f"{QA_PREFIX} свободная {int(time.time() * 1000) % 10**8}"}).json()
    assert task["responsible_id"] is None, task
    yield task
    crm_delete(auth_page, f"/tasks/{task['id']}/permanent")


def card(page: Page, title: str):
    return page.locator("main div").filter(has_text=title).filter(
        has=page.get_by_role("button", name="Взять себе")).last


# Кейс: Задача без ответственного видна в «Свободных» с автором; «Взять себе» делает её моей и убирает из списка
@pytest.mark.writes_data
def test_take_free_task(auth_page: Page, free_task):
    auth_page.reload()
    crm_ready(auth_page)
    main = auth_page.locator("main")
    expect(main).to_contain_text("Задачи без ответственного")
    box = card(auth_page, free_task["title"])
    expect(box).to_be_visible(timeout=15_000)
    me = crm_me(auth_page)
    expect(box).to_contain_text(f"поставил(а): {me.get('name') or me.get('full_name') or ''}".rstrip())

    with auth_page.expect_response(lambda r: r.url.endswith(f"/tasks/{free_task['id']}/take")
                                   and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Взять себе").click()
    assert resp.value.ok, resp.value.text()
    expect(main.get_by_text(free_task["title"], exact=True)).to_have_count(0)
    task = crm_get(auth_page, f"/tasks/{free_task['id']}").json()
    assert task["responsible_id"] == me["id"], task


# Кейс: Задача с ответственным в «Свободные» не попадает
@pytest.mark.writes_data
def test_assigned_task_not_listed(auth_page: Page, free_task):
    me = crm_me(auth_page)
    mine = crm_post(auth_page, "/tasks/", {"title": f"{QA_PREFIX} моя {int(time.time() * 1000) % 10**8}",
                                           "responsible_id": me["id"]}).json()
    try:
        auth_page.reload()
        crm_ready(auth_page)
        expect(card(auth_page, free_task["title"])).to_be_visible(timeout=15_000)
        expect(auth_page.locator("main").get_by_text(mine["title"], exact=True)).to_have_count(0)
    finally:
        crm_delete(auth_page, f"/tasks/{mine['id']}/permanent")
