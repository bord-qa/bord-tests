"""Блок 28. CRM — автоматизации: crm.bord.one/admin/crm/automations.

Снято 29.10.2026:
- API: GET /api/crm/auto-actions?per_page=200[&is_active=…][&entity_type=deal]
  → {items, total}; POST/PUT/DELETE /auto-actions[/<id>]; справочник формы —
  /auto-actions/meta (триггеры, действия, воронки); «Выполнить сейчас» →
  POST /auto-actions/<id>/test; история — /auto-actions/runs?action_id=<id>;
- правило = {name, trigger_entity: deal, trigger_event (created /
  status_changed / …), trigger_status_id, trigger_config (условия: min_amount,
  max_amount, …), action_type (create_task / …), action_config, is_active};
- «Правило» (в шапке) → окно «Новая автоматизация»: название, «Когда
  сработает» (для «Сделка сменила стадию» — ещё «Воронка» и «Стадия»),
  условия «Сумма ≥ / ≤», «Тип действия» (недоступен, пока нет триггера);
  у «Поставить задачу по сделке» — «Название задачи» ({title}, {number}) и
  исполнитель «Ответственный за сделку». «Активно» по умолчанию снято —
  правило сохраняется выключенным;
- карточка правила: название, триггер, условия («сумма ≥ N · сумма ≤ M»;
  с 12.11 при N = M — одно «сумма N»),
  действие и его сводка, переключатель (role=switch, aria-checked),
  «Выполнить сейчас на последнем событии» («Это не проверка: действие
  выполнится по-настоящему» → «Продолжить» → «Правило сработало на событии
  №<id>»), «История запусков» (боковая панель), «Изменить», «Удалить»;
- с 15.11 правило — группа действий: форма «Создать» / «Сохранить» шлёт
  POST /auto-actions/group {rule_ids, name, trigger_…, actions: [...]} → 200
  {items: [правило на каждое действие]}; в карточке две «Изменить» — текстом
  «правило целиком» (title «Изменить правило целиком: …») и значком у действия.
  Переключатель по-прежнему PUT /auto-actions/<id>;
- фильтры «Все / Активные / Выключенные» → is_active в запросе, «Сделки» →
  entity_type=deal.

Правило срабатывает на любой сделке организации, поэтому тестовые правила
сужены условием «сумма ровно N» со случайным N, а сделки под них — свои
«QA Autotest». Действие — задача ответственному сделки (тест-учётке).
Не трогаем: «Шаблоны» (ставят наборы правил и этапов в организацию),
действия с внешним эффектом (рассылка, вебхук, Битрикс).

«Удалить» правило не удаляет, а выключает (DELETE → {"status":
"deactivated"}) — тестовые правила остаются; тесты переиспользуют
выключенные «QA Autotest» правила прошлых прогонов.
"""
import json
import random
import re
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import QA_PREFIX, crm_delete, crm_get, crm_me, crm_post, crm_put, open_crm, until_loaded

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/automations"


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


def unique_amount() -> int:
    return random.randint(900_000, 999_999)


def task_title_template() -> str:
    return f"{QA_PREFIX} звонок по {{title}}"


def amount_condition(low: int, high: int) -> str:
    """Условие суммы в карточке правила: с 12.11 равные границы пишутся одним «сумма N»."""
    return f"сумма {low}" if low == high else f"сумма ≥ {low} · сумма ≤ {high}"


def meta(page: Page) -> dict:
    return crm_get(page, "/auto-actions/meta").json()


def main_stage(page: Page, name: str) -> dict:
    pipeline = next(p for p in meta(page)["pipelines"] if p["is_main"])
    return next(s for s in pipeline["statuses"] if s["name"] == name)


def all_rules(page: Page, **params) -> list[dict]:
    resp = crm_get(page, "/auto-actions", {"per_page": 200, **params})
    assert resp.ok, resp.text()
    return resp.json()["items"]


def rule_body(name: str, amount: int, event: str = "status_changed", status_id: int | None = None,
              active: bool = False) -> dict:
    return {"name": name, "trigger_entity": "deal", "trigger_event": event, "trigger_status_id": status_id,
            "trigger_config": {"min_amount": amount, "max_amount": amount},
            "action_type": "create_task",
            "action_config": {"title": task_title_template(), "priority": "medium", "responsible": "deal"},
            "is_active": active}


def make_rule(page: Page, amount: int, **fields) -> dict:
    """Правило «QA Autotest …»: выключенное из прошлых прогонов под новые настройки, иначе новое.

    «Сделка сменила стадию» без стадии API принимает, но форма такое правило
    не сохранит («Выберите стадию») — по умолчанию ставим «В работе»."""
    if fields.get("event", "status_changed") == "status_changed":
        fields.setdefault("status_id", main_stage(page, "В работе")["id"])
    body = rule_body(f"{QA_PREFIX} {tag()}", amount, **fields)
    spare = next((r for r in all_rules(page, is_active="false")
                  if r["name"].startswith(QA_PREFIX) and r["id"] not in page._qa_rules), None)
    resp = crm_put(page, f"/auto-actions/{spare['id']}", body) if spare else crm_post(page, "/auto-actions", body)
    assert resp.ok, resp.text()
    rule = resp.json()
    page._qa_rules.append(rule["id"])
    return rule


def make_deal(page: Page, amount: int) -> dict:
    pipeline = next(p for p in meta(page)["pipelines"] if p["is_main"])
    resp = crm_post(page, "/deals/", {"title": f"{QA_PREFIX} {tag()}", "pipeline_id": pipeline["id"],
                                      "responsible_id": crm_me(page)["id"], "amount": amount})
    assert resp.status == 201, resp.text()
    page._qa_deals.append(resp.json()["id"])
    return resp.json()


def runs(page: Page, rule: dict) -> list[dict]:
    return crm_get(page, "/auto-actions/runs", {"action_id": rule["id"]}).json()["items"]


def wait_run(page: Page, rule: dict, deal: dict, timeout: float = 20) -> dict | None:
    """Запуск правила по сделке — правила выполняются в фоне, ждём до timeout секунд.
    Запись появляется сразу со status=pending (с 04.11) — ждём итоговый статус."""
    end = time.monotonic() + timeout
    while True:
        found = next((r for r in runs(page, rule) if r["entity_id"] == deal["id"]), None)
        if (found and found["status"] != "pending") or time.monotonic() > end:
            return found
        page.wait_for_timeout(1000)


def deal_tasks(page: Page, deal: dict) -> list[dict]:
    items = crm_get(page, "/tasks/", {"search": QA_PREFIX, "responsible_id": crm_me(page)["id"],
                                      "limit": 100}).json()["items"]
    return [t for t in items if t["entity_type"] == "deal" and t["entity_id"] == deal["id"]]


def cleanup(page: Page):
    for deal_id in page._qa_deals:
        for task in deal_tasks(page, {"id": deal_id}):
            crm_delete(page, f"/tasks/{task['id']}")
            crm_delete(page, f"/tasks/{task['id']}/permanent")
        crm_delete(page, f"/deals/{deal_id}")
    # свои — и брошенные упавшими прогонами включённые «QA Autotest»
    ids = set(page._qa_rules) | {r["id"] for r in all_rules(page, is_active="true") if r["name"].startswith(QA_PREFIX)}
    for rule_id in ids:
        crm_put(page, f"/auto-actions/{rule_id}", {"is_active": False})


@pytest.fixture
def automations(auth_page: Page):
    auth_page._qa_rules, auth_page._qa_deals = [], []
    yield auth_page
    cleanup(auth_page)


def open_automations(page: Page):
    open_crm(page, PAGE)
    expect(page.locator("main").get_by_role("button", name="Выключенные", exact=True)).to_be_visible()


def rule_card(page: Page, name: str):
    return page.locator("main div.rounded-xl.border").filter(has=page.get_by_text(name, exact=True))


def cards(page: Page):
    return page.locator("main div.rounded-xl.border").filter(has=page.get_by_role("switch"))


def overlay(page: Page, text: str):
    """Окна CRM — div.fixed.inset-0 (не role=dialog)."""
    return page.locator("div.fixed.inset-0").filter(has_text=text).last


def pick(page: Page, option: str):
    """Вариант во всплывающем списке CRM (плавающий блок position: fixed)."""
    popup = page.locator("div.shadow-xl[style*='position: fixed']").last
    popup.get_by_role("option", name=option, exact=True).or_(
        popup.get_by_role("button", name=option, exact=True)).first.click()


def number_field(page: Page, form, label: str):
    return form.locator("div").filter(has=page.get_by_text(label, exact=True)).filter(
        has=page.get_by_role("spinbutton")).last.get_by_role("spinbutton")


def new_rule_form(page: Page):
    page.locator("header").get_by_role("button", name="Правило", exact=True).click()
    form = overlay(page, "Новая автоматизация")
    expect(form.get_by_role("button", name="Выберите событие")).to_be_visible()
    return form


def fill_stage_trigger(page: Page, form, stage: str, amount: int):
    form.get_by_role("button", name="Выберите событие").click()
    pick(page, "Сделка сменила стадию")
    form.get_by_role("button", name="Выберите воронку").click()
    pick(page, "Основная воронка · основная")
    form.get_by_role("button", name="Выберите стадию").click()
    pick(page, stage)
    number_field(page, form, "Сумма ≥").fill(str(amount))
    number_field(page, form, "Сумма ≤").fill(str(amount))


def edit_button(page: Page, name: str):
    """«Изменить» правила целиком — у каждого действия своя «Изменить» (значок, с 15.11)."""
    return rule_card(page, name).get_by_title(re.compile("^Изменить правило целиком"))


def group_save(page: Page):
    """Сохранение формы правила (создание и правка) — POST /auto-actions/group с 15.11."""
    return page.expect_response(lambda r: r.url.endswith("/api/crm/auto-actions/group")
                                and r.request.method == "POST")


def rule_put(page: Page, rule: dict):
    return page.expect_response(lambda r: r.url.endswith(f"/api/crm/auto-actions/{rule['id']}")
                                and r.request.method == "PUT")


# Кейс: Список правил совпадает с API — название, триггер, условия, действие, переключатель
def test_list_matches_api(automations: Page):
    amount = unique_amount()
    off = make_rule(automations, amount)
    on = make_rule(automations, amount, active=True)
    open_automations(automations)

    items = all_rules(automations)
    expect(cards(automations)).to_have_count(len(items))
    for rule in (off, on):
        card = rule_card(automations, rule["name"])
        expect(card).to_contain_text("Сделка сменила стадию")
        expect(card).to_contain_text(amount_condition(amount, amount))
        expect(card).to_contain_text("Поставить задачу по сделке")
        expect(card).to_contain_text(f"{task_title_template()} · ответственному сделки")
        expect(card.get_by_role("switch")).to_have_attribute("aria-checked", str(rule["is_active"]).lower())


# Кейс: «Создать» неактивна, пока не выбраны триггер и действие и не задано название задачи
def test_create_disabled_until_filled(automations: Page):
    open_automations(automations)
    form = new_rule_form(automations)
    create = form.get_by_role("button", name="Создать", exact=True)
    form.get_by_placeholder("Напр.: «Бронь оплачена» → сделки").fill(f"{QA_PREFIX} {tag()}")

    expect(create).to_be_disabled()
    expect(form.get_by_role("button", name="Сначала выберите триггер")).to_be_disabled()
    form.get_by_role("button", name="Выберите событие").click()
    pick(automations, "Сделка создана")
    expect(create).to_be_disabled()
    form.get_by_role("button", name="Выберите действие").click()
    pick(automations, "Поставить задачу по сделке")
    expect(create).to_be_disabled()
    form.get_by_placeholder("Связаться с клиентом").fill(task_title_template())
    expect(create).to_be_enabled()


# Кейс: Действие недоступного плагина в списке выключено («нужен плагин «Проекты»»)
def test_unavailable_action_disabled(automations: Page):
    # плагин «Проекты» в BORD QA бывает включён вручную — на время теста выключаем и возвращаем
    plugins = {p["plugin_key"]: p["is_enabled"] for p in crm_get(automations, "/plugins/").json()}
    if plugins.get("projects"):
        assert crm_put(automations, "/plugins/projects", {"is_enabled": False}).ok
        try:
            _check_unavailable_action(automations)
        finally:
            assert crm_put(automations, "/plugins/projects", {"is_enabled": True}).ok, "плагин «Проекты» не вернулся"
    else:
        _check_unavailable_action(automations)


def _check_unavailable_action(automations: Page):
    open_automations(automations)
    form = new_rule_form(automations)
    form.get_by_role("button", name="Выберите событие").click()
    pick(automations, "Сделка создана")
    form.get_by_role("button", name="Выберите действие").click()

    popup = automations.locator("div.shadow-xl[style*='position: fixed']").last
    expect(popup.get_by_role("button", name="Создать проект по шаблону — нужен плагин «Проекты»")).to_be_disabled()
    expect(popup.get_by_role("button", name="Поставить задачу по сделке", exact=True)).to_be_enabled()


# Кейс: Правило через форму — стадия, условие по сумме, задача; сохраняется выключенным
def test_create_rule_via_form(automations: Page):
    amount, name = unique_amount(), f"{QA_PREFIX} {tag()}"
    stage = main_stage(automations, "В работе")
    open_automations(automations)

    form = new_rule_form(automations)
    form.get_by_placeholder("Напр.: «Бронь оплачена» → сделки").fill(name)
    fill_stage_trigger(automations, form, "В работе", amount)
    form.get_by_role("button", name="Выберите действие").click()
    pick(automations, "Поставить задачу по сделке")
    form.get_by_placeholder("Связаться с клиентом").fill(task_title_template())
    expect(form.get_by_role("checkbox", name=re.compile("^Активно"))).not_to_be_checked()
    with group_save(automations) as resp:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    assert json.loads(resp.value.request.post_data)["rule_ids"] == []
    (rule,) = resp.value.json()["items"]  # одно действие — одно правило
    automations._qa_rules.append(rule["id"])

    assert (rule["name"], rule["trigger_event"], rule["trigger_status_id"], rule["trigger_config"],
            rule["action_type"], rule["action_config"]["title"], rule["action_config"]["responsible"],
            rule["is_active"]) == (
        name, "status_changed", stage["id"], {"min_amount": amount, "max_amount": amount},
        "create_task", task_title_template(), "deal", False), rule
    expect(form).to_have_count(0)
    card = rule_card(automations, name)
    expect(card).to_contain_text(amount_condition(amount, amount))
    expect(card.get_by_role("switch")).to_have_attribute("aria-checked", "false")


# Кейс: Переключатель включает и выключает правило — API и подпись переключателя
def test_toggle_rule(automations: Page):
    rule = make_rule(automations, unique_amount())
    open_automations(automations)
    switch = rule_card(automations, rule["name"]).get_by_role("switch")
    expect(switch).to_have_attribute("title", "Выключено — включить")

    with rule_put(automations, rule) as resp:
        switch.click()
    assert resp.value.ok, resp.value.text()
    expect(switch).to_have_attribute("aria-checked", "true")
    assert crm_get(automations, f"/auto-actions/{rule['id']}").json()["is_active"] is True

    with rule_put(automations, rule) as resp:
        switch.click()
    assert resp.value.ok, resp.value.text()
    expect(switch).to_have_attribute("aria-checked", "false")
    assert crm_get(automations, f"/auto-actions/{rule['id']}").json()["is_active"] is False


# Кейс: Фильтры «Активные / Выключенные / Все» и «Сделки» — запрос и видимые правила
def test_filters(automations: Page):
    amount = unique_amount()
    off = make_rule(automations, amount)
    on = make_rule(automations, amount, active=True)
    open_automations(automations)
    main = automations.locator("main")

    def choose(button: str, param: str):
        # при 429 список остаётся прежним — открываем экран заново и жмём фильтр ещё раз
        def action():
            if attempt[0]:
                open_automations(automations)
            attempt[0] += 1
            main.get_by_role("button", name=button, exact=True).click()

        attempt = [0]
        until_loaded(automations, action, lambda r: "/api/crm/auto-actions?" in r.url and param in r.url)

    choose("Активные", "is_active=true")
    expect(rule_card(automations, on["name"])).to_be_visible()
    expect(rule_card(automations, off["name"])).to_have_count(0)
    expect(cards(automations)).to_have_count(len(all_rules(automations, is_active="true")))

    choose("Выключенные", "is_active=false")
    expect(rule_card(automations, off["name"])).to_be_visible()
    expect(rule_card(automations, on["name"])).to_have_count(0)

    choose("Все", "per_page=200")
    expect(rule_card(automations, off["name"])).to_be_visible()
    expect(rule_card(automations, on["name"])).to_be_visible()

    choose("Сделки", "entity_type=deal")
    expect(cards(automations)).to_have_count(len(all_rules(automations, entity_type="deal")))
    expect(rule_card(automations, on["name"])).to_be_visible()


# Кейс: Включённое правило срабатывает при смене стадии — задача по сделке; сделка мимо условия — нет
def test_rule_fires_on_stage_change(automations: Page):
    amount = unique_amount()
    stage = main_stage(automations, "В работе")
    rule = make_rule(automations, amount, status_id=stage["id"], active=True)
    hit, miss = make_deal(automations, amount), make_deal(automations, amount + 1)

    for deal in (hit, miss):
        resp = crm_put(automations, f"/deals/{deal['id']}/stage", {"status_id": stage["id"]})
        assert resp.ok, resp.text()

    run = wait_run(automations, rule, hit)
    assert run and run["status"] == "success", f"правило не сработало на сделке {hit['id']}: {runs(automations, rule)}"
    tasks = deal_tasks(automations, hit)
    assert [(t["title"], t["responsible_id"]) for t in tasks] == [
        (f"{QA_PREFIX} звонок по {hit['title']}", crm_me(automations)["id"])], tasks
    assert not [r for r in runs(automations, rule) if r["entity_id"] == miss["id"]], "сработало на сделке мимо условия"
    assert not deal_tasks(automations, miss)


def run_now(page: Page, rule: dict):
    rule_card(page, rule["name"]).get_by_role("button", name="Выполнить сейчас").click()
    confirm = overlay(page, "на последнем подходящем событии")
    expect(confirm).to_contain_text("Это не проверка: действие выполнится по-настоящему.")
    with page.expect_response(lambda r: r.url.endswith(f"/auto-actions/{rule['id']}/test")) as resp:
        confirm.get_by_role("button", name="Продолжить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    return resp.value.json()


# Кейс: «Выполнить сейчас» — предупреждение «по-настоящему», задача по последней подходящей сделке, «История запусков»
def test_run_now_and_history(automations: Page):
    amount = unique_amount()
    # «Сделка создана» срабатывает и само — фоновым свипом через ~20 с (bord-1); если свип
    # успеет раньше «Выполнить сейчас», повтор задачу не задвоит
    rule = make_rule(automations, amount, event="created", active=True)
    deal = make_deal(automations, amount)
    open_automations(automations)
    card = rule_card(automations, rule["name"])

    run_now(automations, rule)
    expect(automations.get_by_text(f"Правило сработало на событии №{deal['id']}").first).to_be_visible()

    tasks = deal_tasks(automations, deal)
    assert [t["title"] for t in tasks] == [f"{QA_PREFIX} звонок по {deal['title']}"], tasks
    run = next(r for r in runs(automations, rule) if r["entity_id"] == deal["id"])
    assert run["status"] == "success", run

    panel = overlay(automations, "История запусков")
    if not panel.is_visible():
        card.get_by_role("button", name="История запусков").click()
    expect(panel).to_contain_text(rule["name"])
    expect(panel).to_contain_text("Успех")
    expect(panel).to_contain_text(f"#{deal['id']}")
    expect(panel).to_contain_text(run["response_body"])
    automations.keyboard.press("Escape")
    expect(panel).to_have_count(0)


# Кейс: «Выполнить сейчас» у выключенного правила — проверка перед включением, как советует форма
def test_run_now_inactive_rule(automations: Page):
    amount = unique_amount()
    rule = make_rule(automations, amount, event="created")
    deal = make_deal(automations, amount)
    open_automations(automations)

    result = run_now(automations, rule)
    expect(automations.get_by_text("rule inactive")).to_have_count(0)
    assert result["result"]["status"] == "success", result
    assert [t["title"] for t in deal_tasks(automations, deal)] == [f"{QA_PREFIX} звонок по {deal['title']}"]


# Кейс: Изменить правило — окно с текущими значениями, новое название и сумма в карточке и в API
def test_edit_rule(automations: Page):
    amount, new_amount = unique_amount(), unique_amount()
    rule = make_rule(automations, amount)
    new_name = f"{QA_PREFIX} {tag()}"
    open_automations(automations)

    edit_button(automations, rule["name"]).click()
    form = overlay(automations, "Изменить автоматизацию")
    name = form.get_by_placeholder("Напр.: «Бронь оплачена» → сделки")
    expect(name).to_have_value(rule["name"])
    expect(number_field(automations, form, "Сумма ≥")).to_have_value(str(amount))
    expect(form.get_by_placeholder("Связаться с клиентом")).to_have_value(task_title_template())
    name.fill(new_name)
    number_field(automations, form, "Сумма ≥").fill(str(min(amount, new_amount)))
    number_field(automations, form, "Сумма ≤").fill(str(max(amount, new_amount)))
    with group_save(automations) as resp:
        form.get_by_role("button", name="Сохранить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    assert json.loads(resp.value.request.post_data)["rule_ids"] == [rule["id"]]
    assert [r["id"] for r in resp.value.json()["items"]] == [rule["id"]]  # правка, а не новое правило

    low, high = min(amount, new_amount), max(amount, new_amount)
    expect(rule_card(automations, new_name)).to_contain_text(amount_condition(low, high))
    expect(rule_card(automations, rule["name"])).to_have_count(0)
    saved = crm_get(automations, f"/auto-actions/{rule['id']}").json()
    assert (saved["name"], saved["trigger_config"], saved["is_active"]) == (
        new_name, {"min_amount": low, "max_amount": high}, False), saved


# Кейс: Включённое правило «Сделка создана» ставит задачу по новой сделке
def test_rule_fires_on_deal_created(automations: Page):
    amount = unique_amount()
    rule = make_rule(automations, amount, event="created", active=True)
    deal = make_deal(automations, amount)

    # «Сделка создана» рождает фоновый свип (bord-1, починено 03.11), а не сама форма: ~20 с, бывает дольше
    run = wait_run(automations, rule, deal, timeout=90)
    assert run and run["status"] == "success", f"правило не сработало на новой сделке {deal['id']}"
    assert [t["title"] for t in deal_tasks(automations, deal)] == [f"{QA_PREFIX} звонок по {deal['title']}"]


# Кейс: «Удалить» правило — подтверждение «Действие необратимо», правило пропадает из списка и API
def test_delete_rule(automations: Page):
    rule = make_rule(automations, unique_amount())
    open_automations(automations)

    rule_card(automations, rule["name"]).get_by_role("button", name="Удалить", exact=True).click()
    confirm = overlay(automations, f"Удалить правило «{rule['name']}»?")
    expect(confirm).to_contain_text("Действие необратимо.")
    with automations.expect_response(lambda r: r.url.endswith(f"/api/crm/auto-actions/{rule['id']}")
                                     and r.request.method == "DELETE") as resp:
        confirm.get_by_role("button", name="Продолжить", exact=True).click()
    assert resp.value.ok, resp.value.text()

    expect(rule_card(automations, rule["name"])).to_have_count(0)
    assert rule["id"] not in [r["id"] for r in all_rules(automations)], f"после «Удалить»: {resp.value.text()}"
