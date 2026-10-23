"""Блок 11. Инциденты и улики: app.bord.one/admin/security/incidents.

Снято 22.10.2026:
- API: /api/security/incidents (список {items}), …/meta (kinds, severities,
  statuses, action_kinds…), …/summary (счётчики над списком);
- «Новый инцидент» → окно: Название, Номер, Вид, Тяжесть, Началось, Заметили
  (datetime-local), Клиент, Ведёт, Что затронуто, Что случилось, Ущерб,
  Причина, Как устранили, Выводы → POST …/incidents → 201, переход на ?id=<id>;
  пустое название — «Нужно название»; текст вида «пароль: …» сервер не
  принимает (400 «Поле «…» похоже на секрет…»);
- карточка (?id=<id>): статусы-кнопки Открыт → Локализован → Устранён → Закрыт
  (POST …/<id>/status, без подтверждения); «Задачи по итогам» (POST …/actions,
  кружок «выполнено» → PATCH …/actions/<id> {done}); пока есть открытая —
  закрыть нельзя (409 «Нельзя закрыть: открытых задач по итогам — N…»);
  «Хронология» (POST …/events); «Улика» → окно: Имя файла, SHA-256 (64 hex,
  иначе 400), Где лежит, Что это, Кто собрал, «есть ключи или пароли» →
  POST …/evidence; «Удалить» есть только у незакрытого — закрытый API тоже не
  удаляет (409 «…это история»);
- «Хранилище улик» (/vault) открыто только хранителям — «Стать хранителем»
  тесты не нажимают (это выдача прав).

Ведёт инцидент и отвечает за задачи только тест-учётка. Уборка: свои
«QA Autotest …» удаляем; закрытые сначала возвращаем в «Устранён» (иначе API
не даёт удалить).
"""
import re
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_delete, api_get, api_patch, api_post
from helpers.modal import modal
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

API = "/api/security/incidents"
NAME_PREFIX = "QA Autotest"
COUNTERS = {
    "Идут сейчас": "active",
    "Из них SEV1–SEV2": "active_major",
    "Ждут закрытия": "awaiting_close",
    "Задач по итогам открыто": "open_actions",
}


def title() -> str:
    return f"{NAME_PREFIX} инцидент {int(time.time() * 1000)}"


def meta(page: Page) -> dict:
    return api_get(page, f"{API}/meta").json()


def my_name(page: Page) -> str:
    data = meta(page)
    return next(p["name"] for p in data["people"] if p["id"] == data["me"])


def summary(page: Page) -> dict:
    return api_get(page, f"{API}/summary").json()


def create_incident(page: Page, **fields) -> dict:
    data = {"title": title(), "kind": "security", "severity": "sev4", **fields}
    resp = api_post(page, API, data)
    assert resp.status == 201, resp.text()
    return resp.json()


def incident(page: Page, incident_id: int) -> dict:
    return api_get(page, f"{API}/{incident_id}").json()


def cleanup(page: Page):
    resp = api_get(page, API)
    for item in resp.json()["items"] if resp.ok else []:
        if not item["title"].startswith(NAME_PREFIX):
            continue
        if api_delete(page, f"{API}/{item['id']}").status == 409:
            api_post(page, f"{API}/{item['id']}/status", {"status": "resolved", "note": "уборка автотеста"})
            api_delete(page, f"{API}/{item['id']}")


@pytest.fixture
def incidents(auth_page: Page):
    auth_page.goto("/admin/security/incidents")
    expect(auth_page.locator("main").get_by_role("button", name="Новый инцидент")).to_be_visible()
    settle(auth_page)
    yield auth_page
    cleanup(auth_page)


def open_card(page: Page, item: dict):
    page.goto(f"/admin/security/incidents?id={item['id']}")
    expect(page.locator("main").get_by_text(item["title"], exact=True)).to_be_visible()
    settle(page)


def section(page: Page, heading: str, button: str):
    """Блок карточки: заголовок и его кнопка («Добавить», «Улика»)."""
    return page.locator("main").get_by_text(heading, exact=True).locator(
        f"xpath=ancestor::*[.//button[normalize-space()='{button}']][1]")


def status_button(page: Page, status: str):
    # не \b: регулярка уходит в JS, а там граница слова не понимает кириллицу
    return page.locator("main").get_by_role("button", name=re.compile(rf"^{status}(\s|$)"))


def field(form, label: str):
    return form.get_by_text(label, exact=True).locator("xpath=following-sibling::*[self::input or self::textarea][1]")


def pick(page: Page, scope, current: str, option: str):
    scope.get_by_role("button", name=current, exact=True).click()
    page.get_by_role("option", name=option, exact=True).click()


def counter(page: Page, label: str):
    return page.locator("main button").filter(has_text=label)


@pytest.mark.smoke
def test_counters_match_summary(incidents: Page):
    data = summary(incidents)

    for label, key in COUNTERS.items():
        expect(counter(incidents, label), label).to_have_text(re.compile(rf"^{data[key]}\s*{label}$"))
    expect(counter(incidents, "Безопасность / сбои")).to_contain_text(f"{data['security']} / {data['outage']}")


@pytest.mark.writes_data
def test_create_incident_via_form(incidents: Page):
    name = title()
    before = summary(incidents)
    incidents.locator("main").get_by_role("button", name="Новый инцидент").click()
    form = modal(incidents, "Новый инцидент")
    form.get_by_role("button", name="Сохранить").click()
    expect(form.get_by_text("Нужно название", exact=True)).to_be_visible()

    field(form, "Название").fill(name)
    pick(incidents, form, "Безопасность", "Сбой")
    pick(incidents, form, "SEV3 — заметный", "SEV2 — серьёзный")
    form.locator("input[type=datetime-local]").nth(0).fill("2026-10-22T09:00")
    form.locator("input[type=datetime-local]").nth(1).fill("2026-10-22T09:30")
    field(form, "Клиент").fill("QA клиент")
    form.get_by_text("Ведёт", exact=True).locator("xpath=following::button[@aria-haspopup='listbox'][1]").click()
    incidents.get_by_role("option", name=my_name(incidents), exact=True).click()
    field(form, "Что затронуто").fill("qa.example.com")
    field(form, "Что случилось").fill("упал сайт автотеста")
    with incidents.expect_response(lambda r: r.url.endswith(API) and r.request.method == "POST") as created:
        form.get_by_role("button", name="Сохранить").click()

    assert created.value.status == 201, created.value.text()
    sent = created.value.request.post_data_json
    assert {k: sent[k] for k in ("title", "kind", "severity", "client", "affected", "lead_id", "summary")} == {
        "title": name, "kind": "outage", "severity": "sev2", "client": "QA клиент",
        "affected": "qa.example.com", "lead_id": meta(incidents)["me"], "summary": "упал сайт автотеста",
    }
    item = created.value.json()
    assert item["status"] == "open" and item["hours_to_detect"] == 0.5
    expect(incidents).to_have_url(re.compile(rf"\?id={item['id']}$"))
    main = incidents.locator("main")
    for text in ("SEV2 — серьёзный", "Сбой", "QA клиент", "qa.example.com", my_name(incidents), "22.10.2026, 09:00", "30 мин", "упал сайт автотеста"):
        expect(main, text).to_contain_text(text)
    expect(section(incidents, "Хронология", "Добавить")).to_contain_text(f"Инцидент заведён — {my_name(incidents)}")
    after = summary(incidents)
    assert (after["active"], after["active_major"], after["outage"]) == (
        before["active"] + 1, before["active_major"] + 1, before["outage"] + 1)


@pytest.mark.writes_data
def test_secret_in_text_rejected(incidents: Page):
    name = title()
    incidents.locator("main").get_by_role("button", name="Новый инцидент").click()
    form = modal(incidents, "Новый инцидент")
    field(form, "Название").fill(name)
    field(form, "Что случилось").fill("пароль: Qwerty123!")

    with incidents.expect_response(lambda r: r.url.endswith(API) and r.request.method == "POST") as created:
        form.get_by_role("button", name="Сохранить").click()

    assert created.value.status == 400
    expect(form.get_by_text(re.compile("похоже на секрет"))).to_be_visible()
    assert all(i["title"] != name for i in api_get(incidents, API).json()["items"])


@pytest.mark.writes_data
def test_close_blocked_until_actions_done(incidents: Page):
    item = create_incident(incidents, lead_id=meta(incidents)["me"])
    open_card(incidents, item)
    actions = section(incidents, "Задачи по итогам", "Добавить")

    with incidents.expect_response(lambda r: r.url.endswith(f"{item['id']}/status")) as contained:
        status_button(incidents, "Локализован").click()
    assert contained.value.request.post_data_json["status"] == "contained"

    actions.get_by_placeholder("Что сделать").fill("починить автотест")
    actions.get_by_role("button", name="Ответственный", exact=True).click()
    incidents.get_by_role("option", name=my_name(incidents), exact=True).click()
    with incidents.expect_response(lambda r: r.url.endswith(f"{item['id']}/actions") and r.request.method == "POST") as added:
        actions.get_by_role("button", name="Добавить").click()
    assert added.value.status == 201
    expect(actions).to_contain_text("починить автотест")
    assert summary(incidents)["open_actions"] >= 1

    with incidents.expect_response(lambda r: r.url.endswith(f"{item['id']}/status")) as refused:
        status_button(incidents, "Закрыт").click()
    assert refused.value.status == 409
    expect(incidents.locator("main").get_by_text("Нельзя закрыть: открытых задач по итогам — 1. Выполните их или снимите.")).to_be_visible()

    with incidents.expect_response(lambda r: "/actions/" in r.url and r.request.method == "PATCH") as ticked:
        actions.locator("li button:has(svg.lucide-circle)").click()
    assert ticked.value.request.post_data_json == {"done": True}
    expect(actions).to_contain_text("выполнено")
    with incidents.expect_response(lambda r: r.url.endswith(f"{item['id']}/status")) as closed:
        status_button(incidents, "Закрыт").click()

    assert closed.value.ok
    saved = incident(incidents, item["id"])
    assert saved["status"] == "closed" and saved["resolved_at"] and saved["closed_at"]
    timeline = section(incidents, "Хронология", "Добавить")
    expect(timeline).to_contain_text("Статус: Открыт → Локализован")
    expect(timeline).to_contain_text("Статус: Локализован → Закрыт")
    incidents.reload()
    expect(incidents.locator("main").get_by_role("button", name="Изменить")).to_be_visible()
    expect(incidents.locator("main").get_by_role("button", name="Удалить", exact=True)).to_have_count(0)


@pytest.mark.writes_data
def test_closed_incident_not_deleted_by_api(incidents: Page):
    item = create_incident(incidents)
    assert api_post(incidents, f"{API}/{item['id']}/status", {"status": "closed", "note": ""}).ok

    resp = api_delete(incidents, f"{API}/{item['id']}")

    assert resp.status == 409
    assert resp.json()["detail"] == "Закрытый инцидент не удаляется — это история"
    assert incident(incidents, item["id"])["status"] == "closed"


@pytest.mark.writes_data
def test_timeline_event(incidents: Page):
    item = create_incident(incidents)
    open_card(incidents, item)
    timeline = section(incidents, "Хронология", "Добавить")

    timeline.get_by_placeholder("Что произошло").fill("сообщили клиенту (автотест)")
    pick(incidents, timeline, "Заметка", "Сообщили")
    with incidents.expect_response(lambda r: r.url.endswith(f"{item['id']}/events")) as added:
        timeline.get_by_role("button", name="Добавить").click()

    assert added.value.status == 201
    assert added.value.request.post_data_json["kind"] == "communication"
    expect(timeline).to_contain_text(f"сообщили клиенту (автотест) — {my_name(incidents)}")


@pytest.mark.writes_data
def test_evidence_sha_checked_and_saved(incidents: Page):
    item = create_incident(incidents)
    open_card(incidents, item)
    incidents.locator("main").get_by_role("button", name="Улика").click()
    form = modal(incidents, "Улика")
    field(form, "Имя файла").fill("qa-logs.7z")
    field(form, "SHA-256").fill("abc")
    field(form, "Где лежит").fill("Google Drive / QA")

    with incidents.expect_response(lambda r: r.url.endswith(f"{item['id']}/evidence")) as refused:
        form.get_by_role("button", name="Сохранить").click()
    assert refused.value.status == 400
    expect(form.get_by_text("SHA-256 — 64 шестнадцатеричных знака")).to_be_visible()

    field(form, "SHA-256").fill("ab" * 32)
    form.locator("textarea").fill("логи веб-сервера (автотест)")
    with incidents.expect_response(lambda r: r.url.endswith(f"{item['id']}/evidence")) as saved:
        form.get_by_role("button", name="Сохранить").click()

    assert saved.value.status == 201
    sent = saved.value.request.post_data_json
    assert (sent["name"], sent["sha256"], sent["location"]) == ("qa-logs.7z", "ab" * 32, "Google Drive / QA")
    expect(form).to_have_count(0)
    expect(incidents.locator("main").get_by_text("qa-logs.7z")).to_be_visible()


@pytest.mark.writes_data
def test_delete_open_incident(incidents: Page):
    item = create_incident(incidents)
    open_card(incidents, item)

    incidents.locator("main").get_by_role("button", name="Удалить", exact=True).click()
    dialog = incidents.get_by_role("dialog", name=re.compile("Удалить"))
    with incidents.expect_response(lambda r: r.url.endswith(f"{API}/{item['id']}") and r.request.method == "DELETE") as deleted:
        dialog.get_by_role("button", name=re.compile("^(Да|OK)$")).click()

    assert deleted.value.ok
    assert api_get(incidents, f"{API}/{item['id']}").status == 404


@pytest.mark.writes_data
def test_list_filters_and_search(incidents: Page):
    sec = create_incident(incidents, kind="security", severity="sev1")
    out = create_incident(incidents, kind="outage", severity="sev4")
    incidents.reload()
    main = incidents.locator("main")
    rows = lambda text: main.locator("table tbody tr").filter(has_text=text)
    expect(rows(sec["title"])).to_contain_text("SEV1 — критический")

    pick(incidents, main, "Все виды", "Сбой")
    expect(rows(out["title"])).to_have_count(1)
    expect(rows(sec["title"])).to_have_count(0)
    pick(incidents, main, "Сбой", "Все виды")

    pick(incidents, main, "Любая тяжесть", "SEV1 — критический")
    expect(rows(sec["title"])).to_have_count(1)
    expect(rows(out["title"])).to_have_count(0)
    pick(incidents, main, "SEV1 — критический", "Любая тяжесть")

    main.get_by_placeholder("Номер, название, клиент…").fill(out["title"])
    expect(rows(out["title"])).to_have_count(1)
    expect(rows(sec["title"])).to_have_count(0)

    rows(out["title"]).click()
    expect(incidents).to_have_url(re.compile(rf"\?id={out['id']}$"))


def test_vault_only_for_custodians(incidents: Page):
    incidents.goto("/admin/security/incidents/vault")

    main = incidents.locator("main")
    expect(main.get_by_text("Хранилище улик открыто только хранителям")).to_be_visible()
    expect(main.get_by_role("button", name="Стать хранителем")).to_be_visible()
