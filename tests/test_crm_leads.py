"""Блок 35. CRM — лиды и формы сайта (плагин «Лиды»): /admin/crm/leads, настройки «Формы сайта».

Снято в MCP-браузере 30.10.2026:
- лид — это сделка на стадии с флагом is_lead; доска GET /api/crm/deals/pipeline?
  pipeline_id=<id>&stage=lead&mirrors=true. Воронка выбирается в адресе (?pipeline=),
  если стадий-лидов в ней нет — «Настройте этапы лидов в настройках CRM». Основную
  воронку не трогаем: тесты заводят свою и размечают стадии через
  POST /pipeline-statuses/pipelines/<id>/lead-stages {save: true, status_ids};
- «Новый лид» → окно как у сделки (Название, Воронка, Контакт, Ответственный),
  «Создать» неактивна без названия, POST /deals/ → 201;
- карточка: плашка «Лид», полоса «Этап лида» (стадии-лиды и проигрыш), кнопка
  «Конвертировать в сделку» → окно «Конвертировать лид в сделку?» (Продолжить) →
  PUT /deals/<id>/stage на первую стадию без флага, карточка уезжает в «Сделки»;
- «Формы сайта» — настройки ?tab=lead_forms. «Выдать ключ» (название, домены, «Куда
  попадают заявки») → POST /lead-forms → public_key. Заявка: POST /public/leads/<key>
  без токена → 201 {ok}; пустая — 400, чужой, сменённый или выключенный ключ — 404
  «Unknown form». Журнал GET /lead-forms/intake: card / no_card / pending;
- карточку заводит правило «Заявки с сайта → Создать лид» (trigger form/incoming,
  action create_lead_deal), стадия берётся из ключа. Без правила заявка ждёт в pending
  (bord-18), появление правила подбирает и ожидавшие — за полминуты.

Плагин включается на время блока, своя воронка удаляется после него, правило
выключается (насовсем не удаляется, bord-2) и переиспользуется.
"""
import random
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import (CRM, QA_PREFIX, crm_delete, crm_get, crm_me, crm_patch, crm_post, crm_put,
                         open_crm, plugins_on)

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/leads"
FORMS = "/admin/crm/settings?tab=lead_forms"
PIPELINE = f"{QA_PREFIX} лиды блока"


@pytest.fixture(scope="module", autouse=True)
def leads_plugin(browser, auth_state, browser_context_args):
    yield from plugins_on(browser, auth_state, browser_context_args, "leads")


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


def phone() -> str:
    return "+3736" + "".join(random.choices("0123456789", k=7))


def pipelines(page: Page) -> list:
    return crm_get(page, "/pipeline-statuses/pipelines").json()["items"]


def delete_pipeline_deals(page: Page, pipeline_id: int):
    for archived in ("false", "true"):
        resp = crm_get(page, "/deals/", {"pipeline_id": pipeline_id, "archived": archived, "limit": 100})
        for d in resp.json()["items"] if resp.ok else []:
            crm_delete(page, f"/deals/{d['id']}")


def clean_forms(page: Page):
    for f in crm_get(page, "/lead-forms").json()["items"]:
        if f["name"].startswith(QA_PREFIX):
            crm_delete(page, f"/lead-forms/{f['id']}")


def switch_rules_off(page: Page):
    resp = crm_get(page, "/auto-actions", {"is_active": "true"})
    data = resp.json() if resp.ok else []
    for r in data["items"] if isinstance(data, dict) else data:
        if r["name"].startswith(QA_PREFIX) and r["trigger_entity"] == "form":
            crm_delete(page, f"/auto-actions/{r['id']}")


@pytest.fixture(scope="module")
def pipeline(browser, auth_state, browser_context_args, leads_plugin):
    """Своя воронка: первые две стадии — лиды. Основную воронку не размечаем."""
    ctx = browser.new_context(**browser_context_args, storage_state=auth_state)
    page = ctx.new_page()
    created = crm_post(page, "/pipeline-statuses/pipelines", {"name": PIPELINE, "is_main": False, "sort": 98})
    assert created.status == 201, created.text()
    pipeline_id = created.json()["id"]
    full = next(p for p in pipelines(page) if p["id"] == pipeline_id)
    lead_ids = [s["id"] for s in full["statuses"][:2]]
    marked = crm_post(page, f"/pipeline-statuses/pipelines/{pipeline_id}/lead-stages",
                      {"save": True, "status_ids": lead_ids})
    assert marked.ok, marked.text()
    yield {**full, "lead_ids": lead_ids}
    page._crm_sso = None
    delete_pipeline_deals(page, pipeline_id)
    crm_delete(page, f"/pipeline-statuses/pipelines/{pipeline_id}")
    ctx.close()


@pytest.fixture
def leads(auth_page: Page, pipeline: dict):
    open_crm(auth_page, f"{PAGE}?pipeline={pipeline['id']}")
    yield auth_page
    switch_rules_off(auth_page)
    clean_forms(auth_page)
    delete_pipeline_deals(auth_page, pipeline["id"])
    contacts = crm_get(auth_page, "/contacts/", {"search": QA_PREFIX, "limit": 200})
    ids = [c["id"] for c in contacts.json()["items"] if c["first_name"].startswith(QA_PREFIX)] if contacts.ok else []
    if ids:
        crm_post(auth_page, "/bulk/contacts/delete", {"contact_ids": ids})


def create_lead(page: Page, pipeline: dict, **fields) -> dict:
    data = {"title": f"{QA_PREFIX} {tag()}", "pipeline_id": pipeline["id"], "status_id": pipeline["lead_ids"][0],
            "responsible_id": crm_me(page)["id"], **fields}
    resp = crm_post(page, "/deals/", data)
    assert resp.status == 201, resp.text()
    return resp.json()


def board(page: Page, pipeline: dict) -> list:
    return crm_get(page, "/deals/pipeline", {"pipeline_id": pipeline["id"], "stage": "lead", "mirrors": "true",
                                             "limit": 50}).json()


def open_board(page: Page, pipeline: dict):
    page.goto(f"{CRM}{PAGE}?pipeline={pipeline['id']}")
    expect(page.get_by_role("button", name="Новый лид")).to_be_visible(timeout=20_000)
    expect(page.locator("main").get_by_text("Новая").first).to_be_visible(timeout=20_000)


def open_card(page: Page, pipeline: dict, lead: dict):
    page.goto(f"{CRM}{PAGE}?pipeline={pipeline['id']}&id={lead['id']}")
    expect(page).to_have_title(re.compile(rf"{re.escape(lead['title'])} · Лиды · BORD CRM$"), timeout=20_000)


def window(page: Page, title: str):
    return page.locator("div.fixed.inset-0").filter(has_text=title).last


def make_form(page: Page, **fields) -> dict:
    resp = crm_post(page, "/lead-forms", {"name": f"{QA_PREFIX} форма {tag()}", **fields})
    assert resp.status == 201, resp.text()
    return resp.json()


def submit(page: Page, key: str, **fields):
    """Заявка с сайта: без токена, как её шлёт страница."""
    return page.request.post(f"{CRM}/api/crm/public/leads/{key}", data=fields)


def make_rule(page: Page, pipeline: dict) -> dict:
    """Включённое правило «Заявки с сайта → Создать лид»; выключенное из прошлых запусков — заново."""
    body = {"name": f"{QA_PREFIX} заявка в лид", "trigger_entity": "form", "trigger_event": "incoming",
            "trigger_config": {}, "action_type": "create_lead_deal", "is_active": True,
            "action_config": {"pipeline_id": pipeline["id"], "lead_status_id": pipeline["lead_ids"][1]}}
    resp = crm_get(page, "/auto-actions", {"is_active": "false"})
    data = resp.json() if resp.ok else []
    spare = next((r for r in (data["items"] if isinstance(data, dict) else data)
                  if r["name"] == body["name"]), None)
    saved = crm_put(page, f"/auto-actions/{spare['id']}", body) if spare else crm_post(page, "/auto-actions", body)
    assert saved.ok, saved.text()
    return saved.json()


def intake_item(page: Page, form: dict, name: str) -> dict:
    items = crm_get(page, "/lead-forms/intake", {"form_id": form["id"]}).json()["items"]
    return next(i for i in items if i["name"] == name)


# Кейс: Основная воронка без стадий-лидов — подсказка настроить этапы
def test_no_lead_stages_hint(leads: Page):
    main = next(p for p in pipelines(leads) if p["is_main"])
    assert not any(s["is_lead"] for s in main["statuses"]), "в основной воронке появились стадии-лиды — обновить тест"

    leads.goto(f"{CRM}{PAGE}?pipeline={main['id']}")

    expect(leads.get_by_text("Настройте этапы лидов в настройках CRM")).to_be_visible(timeout=20_000)


# Кейс: Доска лидов = API: только стадии-лиды, счётчики и карточки
def test_board_matches_api(leads: Page, pipeline: dict):
    lead = create_lead(leads, pipeline)
    open_board(leads, pipeline)

    columns = board(leads, pipeline)
    assert [c["status"]["id"] for c in columns] == pipeline["lead_ids"]
    main = leads.locator("main")
    expect(main.get_by_text(lead["title"])).to_be_visible()
    for name in ("Предложение", "Переговоры", "Выиграна"):
        expect(main.get_by_text(name, exact=True)).to_have_count(0)


# Кейс: Сделка на стадии без флага в «Лиды» не попадает
def test_deal_not_on_lead_board(leads: Page, pipeline: dict):
    deal_stage = next(s for s in pipeline["statuses"] if s["id"] not in pipeline["lead_ids"])
    deal = create_lead(leads, pipeline, status_id=deal_stage["id"])
    lead = create_lead(leads, pipeline)
    open_board(leads, pipeline)

    expect(leads.locator("main").get_by_text(lead["title"])).to_be_visible()
    expect(leads.locator("main").get_by_text(deal["title"])).to_have_count(0)


# Кейс: «Создать» неактивна без названия, новый лид попадает на первую стадию
def test_create_lead_via_form(leads: Page, pipeline: dict):
    open_board(leads, pipeline)
    leads.get_by_role("button", name="Новый лид").click()
    form = window(leads, "Новый лид")
    create = form.get_by_role("button", name="Создать", exact=True)
    expect(create).to_be_disabled()
    title = f"{QA_PREFIX} {tag()}"

    form.get_by_placeholder("Название или поиск товара…").fill(title)
    expect(create).to_be_enabled()
    with leads.expect_response(lambda r: r.url.endswith("/api/crm/deals/") and r.request.method == "POST") as resp:
        create.click()
    assert resp.value.status == 201, resp.value.text()
    created = resp.value.json()

    expect(leads.locator("main").get_by_text(title)).to_be_visible()
    saved = crm_get(leads, f"/deals/{created['id']}").json()
    assert (saved["status_id"], saved["is_lead"], saved["pipeline_id"]) == \
        (pipeline["lead_ids"][0], True, pipeline["id"])


# Кейс: Карточка лида — плашка «Лид», этапы только лидов, номер в адресе
def test_lead_card(leads: Page, pipeline: dict):
    lead = create_lead(leads, pipeline)
    open_card(leads, pipeline, lead)

    expect(leads.get_by_text("ЭТАП ЛИДА", exact=False).first).to_be_visible()
    expect(leads.get_by_role("button", name="Конвертировать в сделку")).to_be_visible()
    stage_titles = leads.locator("button[title]").evaluate_all(
        "els => els.filter(b => b.offsetParent).map(b => b.title.split('\\n')[0])")
    assert "Предложение" not in stage_titles and "Выиграна" not in stage_titles, stage_titles


# Кейс: Смена этапа внутри лидов оставляет карточку лидом
def test_move_between_lead_stages(leads: Page, pipeline: dict):
    lead = create_lead(leads, pipeline)
    second = next(s for s in pipeline["statuses"] if s["id"] == pipeline["lead_ids"][1])
    open_card(leads, pipeline, lead)

    with leads.expect_response(lambda r: f"/deals/{lead['id']}/stage" in r.url and r.request.method == "PUT") as resp:
        leads.locator(f"button[title^='{second['name']}']").first.click()
    assert resp.value.ok, resp.value.text()

    saved = crm_get(leads, f"/deals/{lead['id']}").json()
    assert (saved["status_id"], saved["is_lead"]) == (second["id"], True)


# Кейс: Конвертация — лид уходит в «Сделки» на первую стадию без флага
def test_convert_to_deal(leads: Page, pipeline: dict):
    lead = create_lead(leads, pipeline)
    target = next(s for s in pipeline["statuses"] if s["id"] not in pipeline["lead_ids"])
    open_card(leads, pipeline, lead)

    leads.get_by_role("button", name="Конвертировать в сделку").click()
    question = window(leads, "Конвертировать лид в сделку?")
    expect(question).to_contain_text(f"на этап «{target['name']}»")
    with leads.expect_response(lambda r: f"/deals/{lead['id']}/stage" in r.url and r.request.method == "PUT") as resp:
        question.get_by_role("button", name="Продолжить").click()
    assert resp.value.ok, resp.value.text()

    saved = crm_get(leads, f"/deals/{lead['id']}").json()
    assert (saved["status_id"], saved["is_lead"]) == (target["id"], False)
    titles = [d["title"] for c in board(leads, pipeline) for d in c["deals"]]
    assert lead["title"] not in titles, "сконвертированный лид остался на доске лидов"


# Кейс: «Отмена» в окне конвертации оставляет лида
def test_convert_cancel(leads: Page, pipeline: dict):
    lead = create_lead(leads, pipeline)
    open_card(leads, pipeline, lead)

    leads.get_by_role("button", name="Конвертировать в сделку").click()
    window(leads, "Конвертировать лид в сделку?").get_by_role("button", name="Отмена").click()

    expect(leads.get_by_text("Конвертировать лид в сделку?")).to_have_count(0)
    assert crm_get(leads, f"/deals/{lead['id']}").json()["is_lead"] is True


# Кейс: «Выдать ключ» через форму — ключ и маршрут на стадию видны в списке
def test_issue_key_via_form(leads: Page, pipeline: dict):
    name = f"{QA_PREFIX} форма {tag()}"
    stage = next(s for s in pipeline["statuses"] if s["id"] == pipeline["lead_ids"][0])
    open_crm(leads, FORMS)

    leads.get_by_role("button", name="Выдать ключ").click()
    leads.get_by_placeholder("Напр. shop.example.md").fill(name)
    leads.get_by_role("button", name=re.compile("По правилу автоматизации")).click()
    leads.locator("div.shadow-xl[style*='position: fixed']").last.get_by_text(
        f"{PIPELINE} → {stage['name']}").click()
    with leads.expect_response(lambda r: r.url.endswith("/api/crm/lead-forms") and r.request.method == "POST") as resp:
        leads.get_by_role("button", name="Выдать", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    created = resp.value.json()

    assert created["lead_status_id"] == stage["id"] and created["public_key"]
    main = leads.locator("main")
    expect(main.get_by_text(name)).to_be_visible()
    expect(main.get_by_text(created["public_key"]).first).to_be_visible()
    expect(main).to_contain_text(f"{PIPELINE} → {stage['name']}")


# Кейс: Заявка принимается, журнал её показывает; пустая и с чужим ключом — нет
def test_public_submit_validation(leads: Page):
    form = make_form(leads)
    name = f"{QA_PREFIX} заявка {tag()}"

    ok = submit(leads, form["public_key"], name=name, phone=phone())
    assert (ok.status, ok.json()) == (201, {"ok": True})
    assert submit(leads, form["public_key"]).status == 400
    assert submit(leads, "no-such-key", name=name, phone=phone()).status == 404

    assert intake_item(leads, form, name)["form_name"] == form["name"]


# Кейс: Новый ключ сразу отменяет старый
def test_rotate_key(leads: Page):
    form = make_form(leads)

    rotated = crm_post(leads, f"/lead-forms/{form['id']}/rotate-key", {})
    assert rotated.ok, rotated.text()
    new_key = rotated.json()["public_key"]

    assert new_key != form["public_key"]
    assert submit(leads, form["public_key"], name="QA", phone=phone()).status == 404
    assert submit(leads, new_key, name=f"{QA_PREFIX} {tag()}", phone=phone()).status == 201


# Кейс: Выключенный ключ заявки не принимает
def test_disabled_key(leads: Page):
    form = make_form(leads)

    resp = crm_patch(leads, f"/lead-forms/{form['id']}", {"is_active": False})
    assert resp.ok, resp.text()

    assert submit(leads, form["public_key"], name="QA", phone=phone()).status == 404


# Кейс: С правилом заявка становится лидом на стадии из ключа, с контактом
def test_submit_with_rule_creates_lead(leads: Page, pipeline: dict):
    make_rule(leads, pipeline)
    form = make_form(leads, lead_status_id=pipeline["lead_ids"][0])
    name, tel = f"{QA_PREFIX} заявка {tag()}", phone()

    assert submit(leads, form["public_key"], name=name, phone=tel).status == 201

    item = None
    for _ in range(30):
        item = intake_item(leads, form, name)
        if item["state"] == "card":
            break
        leads.wait_for_timeout(1_000)
    assert item["state"] == "card", f"заявка не стала карточкой: {item}"
    deal = crm_get(leads, f"/deals/{item['deal_id']}").json()
    assert (deal["status_id"], deal["is_lead"]) == (pipeline["lead_ids"][0], True), "стадия не из ключа"
    contact = crm_get(leads, f"/contacts/{deal['contact_id']}").json()
    assert contact["phone"] == tel


# Кейс: Ключ со стадией заводит карточку без правила автоматизации
def test_route_works_without_rule(leads: Page, pipeline: dict):
    switch_rules_off(leads)
    form = make_form(leads, lead_status_id=pipeline["lead_ids"][0])
    name = f"{QA_PREFIX} заявка {tag()}"
    assert submit(leads, form["public_key"], name=name, phone=phone()).status == 201

    leads.wait_for_timeout(30_000)

    item = intake_item(leads, form, name)
    assert item["state"] == "card", f"у ключа выбрана стадия, а заявка осталась {item['state']}"
