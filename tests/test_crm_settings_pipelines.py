"""Блок 25. CRM — настройки воронок: crm.bord.one/admin/crm/settings.

Снято 27.10.2026:
- вкладки — кнопки, выбранная уходит в адрес: ?tab=pipeline / sources /
  loss_reasons / client_states;
- «Статусы воронки»: счётчик «N воронок», «Создать воронку» → окно «Новая
  воронка» (Название, переключатель «Основная воронка» — НЕ трогаем) →
  POST /pipeline-statuses/pipelines {name, is_main: false, sort}; новая воронка
  сразу получает 6 стандартных статусов и показана свёрнутой («6 статусов»),
  раскрывается кликом по названию. В шапке воронки кнопки по title: «Скрыть: …»
  («Скрыть воронку «…»?» → PUT {is_archive: true}), «Редактировать» (окно
  «Редактировать воронку» → PUT), «Удалить» («Удалить воронку "…" и N статусов?»
  → DELETE). Воронку с карточками API не удаляет и не скрывает (409 «В воронке
  «…» есть карточки — N шт. …», на экране всплывает этот текст);
- статусы воронки — div.space-y-1 > div.group: стрелки вверх/вниз (PUT order
  у двух соседей), «Редактировать» и «Удалить» («Удалить статус "…"?»).
  «Добавить» → окно «Новый статус» → POST /pipeline-statuses/ (кодовое имя
  из названия, order — в конец);
- «Источники»: счётчик «(N активных, M скрытых)», «Добавить» → «Новый источник»
  → POST /sources/; в строке кнопка «Активен» / «Скрыт» (PUT is_active),
  «Редактировать» («Редактировать источник»), «Удалить» («Удалить источник "…"?»).
  DELETE источник не стирает: он остаётся в списке скрытым (is_active: false),
  повторный DELETE тоже — поэтому тестовые источники копятся скрытыми;
- «Причины закрытия» — добавление в строке: «Например: Ребёнок или спам» +
  тип «Лиды / Сделки / Лиды и сделки» (applies_to lead / deal / all) +
  «Добавить» → POST /loss-reasons/. Правка тоже в строке (кнопки по title
  «Сохранить» / «Отмена»), удаление — «Удалить причину «…»?». Окно «Сделка
  проиграна» предлагает только причины для сделок и «Лиды и сделки»;
- «Состояния клиентов»: «Добавить состояние» → «Новое состояние» (Название,
  Кодовое имя из названия, Описание, …) → POST /client-states/; в строке
  «Редактировать» / «Удалить» («Удалить состояние "…"?») — удаляется насовсем.
  Переключатель «Имена клиентов только латиницей» на этой вкладке не трогаем.

Всё своё — «QA Autotest …»; основную воронку и её статусы не меняем.
"""
import random
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import CRM, QA_PREFIX, crm_delete, crm_get, crm_me, crm_post, crm_ready, open_crm

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/settings"
DEFAULT_STAGES = ["Новая", "В работе", "Предложение", "Переговоры", "Выиграна", "Проиграна"]


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


def title() -> str:
    return f"{QA_PREFIX} {tag()}"


def items(resp) -> list:
    data = resp.json()
    return data["items"] if isinstance(data, dict) else data


def pipelines(page: Page, archived: bool = True) -> list:
    return items(crm_get(page, "/pipeline-statuses/pipelines", {"include_archive": "true"} if archived else None))


def create_pipeline(page: Page) -> dict:
    resp = crm_post(page, "/pipeline-statuses/pipelines", {"name": title(), "is_main": False, "sort": 99})
    assert resp.status == 201, resp.text()
    return resp.json()


def stages(page: Page, pipeline: dict) -> list:
    data = crm_get(page, f"/pipeline-statuses/pipelines/{pipeline['id']}").json()
    return sorted(data["statuses"], key=lambda s: s["order"])


def create_deal(page: Page, pipeline_id: int) -> dict:
    resp = crm_post(page, "/deals/", {"title": title(), "pipeline_id": pipeline_id,
                                       "responsible_id": crm_me(page)["id"]})
    assert resp.status == 201, resp.text()
    page._qa_deals.append(resp.json()["id"])
    return resp.json()


def create_reason(page: Page, applies_to: str = "deal") -> dict:
    resp = crm_post(page, "/loss-reasons/", {"name": title(), "sort": 1, "applies_to": applies_to})
    assert resp.status == 201, resp.text()
    return resp.json()


def create_state(page: Page) -> dict:
    resp = crm_post(page, "/client-states/", {"name": title(), "order": 0, "is_manual_only": False,
                                               "is_default": False})
    assert resp.status == 201, resp.text()
    return resp.json()


def cleanup(page: Page):
    for deal_id in page._qa_deals:
        crm_delete(page, f"/deals/{deal_id}")
    for p in pipelines(page):
        if p["name"].startswith(QA_PREFIX):
            crm_delete(page, f"/pipeline-statuses/pipelines/{p['id']}")
    for path in ("/loss-reasons/", "/client-states/", "/sources/"):
        resp = crm_get(page, path, {"include_archived": "true", "limit": 200})
        for it in items(resp) if resp.ok else []:
            # источник DELETE только скрывает — повторно не трогаем уже скрытые
            if it["name"].startswith(QA_PREFIX) and it.get("is_active", True):
                crm_delete(page, f"{path}{it['id']}")


@pytest.fixture
def settings(auth_page: Page):
    auth_page._qa_deals = []
    open_crm(auth_page, PAGE + "?tab=pipeline")
    yield auth_page
    cleanup(auth_page)


def open_tab(page: Page, tab: str):
    page.goto(f"{CRM}{PAGE}?tab={tab}")
    crm_ready(page)


def overlay(page: Page, text: str):
    """Окна CRM — div.fixed.inset-0 (не role=dialog)."""
    return page.locator("div.fixed.inset-0").filter(has_text=text).last


def confirm(page: Page, question: str):
    dialog = overlay(page, question)
    expect(dialog).to_be_visible()
    return dialog


def api(path: str, method: str):
    return lambda r: path in r.url and r.request.method == method


# --- воронки ---------------------------------------------------------------

def pipeline_card(page: Page, name: str):
    return page.locator("main").get_by_text(name, exact=True).locator(
        "xpath=ancestor::div[contains(@class,'rounded-xl')][1]")


def expand(page: Page, name: str):
    card = pipeline_card(page, name)
    if not card.get_by_role("button", name="Добавить").is_visible():
        page.locator("main").get_by_text(name, exact=True).click()
    expect(card.get_by_role("button", name="Добавить")).to_be_visible()
    return card


def stage_rows(card):
    return card.locator("div.space-y-1 > div.group")


def stage_row(card, name: str):
    return stage_rows(card).filter(has=card.page.get_by_text(name, exact=True))


def counter(page: Page, n: int):
    return page.locator("main").get_by_text(f"{n} воронок", exact=True)


# Кейс: Воронки на экране = API — счётчик, названия, статусы основной воронки по порядку с отметками
def test_pipelines_match_api(settings: Page):
    all_pipelines = pipelines(settings)
    expect(counter(settings, len(all_pipelines))).to_be_visible()
    for p in all_pipelines:
        expect(pipeline_card(settings, p["name"])).to_be_visible()

    main = next(p for p in all_pipelines if p["is_main"])
    card = expand(settings, main["name"])
    expect(card.get_by_text("основная", exact=True)).to_be_visible()
    expected = stages(settings, main)
    expect(stage_rows(card)).to_have_text([re.compile(rf"^{re.escape(s['name'])}") for s in expected])
    for s in expected:
        marks = [m for flag, m in (("is_default", "умолч."), ("is_close", "закрыт."), ("is_lost", "проигр.")) if s[flag]]
        for mark in marks:
            expect(stage_row(card, s["name"])).to_contain_text(mark)


# Кейс: Новая воронка через форму — шесть стандартных статусов, счётчик +1
def test_create_pipeline_via_form(settings: Page):
    before = len(pipelines(settings))
    name = title()
    settings.locator("main").get_by_role("button", name="Создать воронку").click()
    form = overlay(settings, "Новая воронка")
    form.get_by_placeholder("Название воронки").fill(name)
    with settings.expect_response(api("/pipeline-statuses/pipelines", "POST")) as created:
        form.get_by_role("button", name="Создать", exact=True).click()

    assert created.value.status == 201, created.value.text()
    body = created.value.request.post_data_json
    assert (body["name"], body["is_main"]) == (name, False), body
    expect(form).to_be_hidden()
    expect(counter(settings, before + 1)).to_be_visible()
    expect(pipeline_card(settings, name)).to_contain_text("6 статусов")
    pipeline = created.value.json()
    assert [s["name"] for s in stages(settings, pipeline)] == DEFAULT_STAGES
    card = expand(settings, name)
    expect(stage_rows(card)).to_have_text([re.compile(rf"^{s}") for s in DEFAULT_STAGES])


# Кейс: Переименование воронки — окно «Редактировать воронку» с текущим названием
def test_rename_pipeline(settings: Page):
    pipeline = create_pipeline(settings)
    settings.reload()
    crm_ready(settings)
    pipeline_card(settings, pipeline["name"]).get_by_title("Редактировать").first.click()
    form = overlay(settings, "Редактировать воронку")
    expect(form.get_by_placeholder("Название воронки")).to_have_value(pipeline["name"])
    new_name = title()
    form.get_by_placeholder("Название воронки").fill(new_name)
    with settings.expect_response(api(f"/pipeline-statuses/pipelines/{pipeline['id']}", "PUT")) as updated:
        form.get_by_role("button", name="Сохранить").click()

    assert updated.value.ok, updated.value.text()
    expect(pipeline_card(settings, new_name)).to_be_visible()
    expect(settings.locator("main").get_by_text(pipeline["name"], exact=True)).to_have_count(0)
    saved = crm_get(settings, f"/pipeline-statuses/pipelines/{pipeline['id']}").json()
    assert (saved["name"], saved["is_main"]) == (new_name, False), saved


# Кейс: Удаление пустой воронки — «Удалить воронку "…" и 6 статусов?» → «Продолжить»
def test_delete_pipeline(settings: Page):
    pipeline = create_pipeline(settings)
    settings.reload()
    crm_ready(settings)
    before = len(pipelines(settings))
    pipeline_card(settings, pipeline["name"]).get_by_title("Удалить").first.click()
    dialog = confirm(settings, f'Удалить воронку "{pipeline["name"]}" и 6 статусов?')
    with settings.expect_response(api(f"/pipeline-statuses/pipelines/{pipeline['id']}", "DELETE")) as deleted:
        dialog.get_by_role("button", name="Продолжить").click()

    assert deleted.value.ok, deleted.value.text()
    expect(settings.locator("main").get_by_text(pipeline["name"], exact=True)).to_have_count(0)
    expect(counter(settings, before - 1)).to_be_visible()
    assert pipeline["id"] not in [p["id"] for p in pipelines(settings)]


# Кейс: Воронку с карточками удалить нельзя — объяснение на экране, воронка на месте
def test_delete_pipeline_with_deal_refused(settings: Page):
    pipeline = create_pipeline(settings)
    create_deal(settings, pipeline["id"])
    settings.reload()
    crm_ready(settings)
    pipeline_card(settings, pipeline["name"]).get_by_title("Удалить").first.click()
    dialog = confirm(settings, f'Удалить воронку "{pipeline["name"]}"')
    with settings.expect_response(api(f"/pipeline-statuses/pipelines/{pipeline['id']}", "DELETE")) as deleted:
        dialog.get_by_role("button", name="Продолжить").click()

    assert deleted.value.status == 409, f"{deleted.value.status} {deleted.value.text()}"
    expect(settings.get_by_text(re.compile(rf"В воронке «{re.escape(pipeline['name'])}» есть карточки — 1 шт"))).to_be_visible()
    assert pipeline["id"] in [p["id"] for p in pipelines(settings)]
    expect(pipeline_card(settings, pipeline["name"])).to_be_visible()


# Кейс: Скрыть воронку — пропадает из выбора на доске, в настройках остаётся
def test_hide_pipeline(settings: Page):
    pipeline = create_pipeline(settings)
    settings.reload()
    crm_ready(settings)
    card = pipeline_card(settings, pipeline["name"])
    card.get_by_title(re.compile("^Скрыть")).click()
    dialog = confirm(settings, f"Скрыть воронку «{pipeline['name']}»?")
    with settings.expect_response(api(f"/pipeline-statuses/pipelines/{pipeline['id']}", "PUT")) as hidden:
        dialog.get_by_role("button", name="Продолжить").click()

    assert hidden.value.ok, hidden.value.text()
    assert hidden.value.request.post_data_json == {"is_archive": True}
    expect(card.get_by_title(re.compile("^Скрыть"))).to_have_count(0)
    expect(card).to_be_visible()
    assert pipeline["id"] not in [p["id"] for p in pipelines(settings, archived=False)]
    assert pipeline["id"] in [p["id"] for p in pipelines(settings)]


# Кейс: Новый статус воронки через форму — встаёт в конец, кодовое имя из названия
def test_add_stage(settings: Page):
    pipeline = create_pipeline(settings)
    settings.reload()
    crm_ready(settings)
    card = expand(settings, pipeline["name"])
    name = title()
    card.get_by_role("button", name="Добавить").click()
    form = overlay(settings, "Новый статус")
    form.get_by_placeholder("Название статуса").fill(name)
    with settings.expect_response(api("/pipeline-statuses/", "POST")) as created:
        form.get_by_role("button", name="Создать", exact=True).click()

    assert created.value.status == 201, created.value.text()
    saved = created.value.json()
    assert (saved["name"], saved["pipeline_id"], saved["type"]) == (name, pipeline["id"], "deal"), saved
    assert saved["codename"] == name.lower().replace(" ", "_"), saved["codename"]
    expect(form).to_be_hidden()
    expect(stage_rows(card)).to_have_text([re.compile(rf"^{re.escape(s)}") for s in DEFAULT_STAGES + [name]])
    assert [s["name"] for s in stages(settings, pipeline)] == DEFAULT_STAGES + [name]


# Кейс: Переименование статуса — окно «Редактировать статус»
def test_edit_stage(settings: Page):
    pipeline = create_pipeline(settings)
    stage = stages(settings, pipeline)[1]
    settings.reload()
    crm_ready(settings)
    card = expand(settings, pipeline["name"])
    stage_row(card, stage["name"]).get_by_title("Редактировать").click()
    form = overlay(settings, "Редактировать статус")
    expect(form.get_by_placeholder("Название статуса")).to_have_value(stage["name"])
    new_name = title()
    form.get_by_placeholder("Название статуса").fill(new_name)
    with settings.expect_response(api(f"/pipeline-statuses/{stage['id']}", "PUT")) as updated:
        form.get_by_role("button", name="Сохранить").click()

    assert updated.value.ok, updated.value.text()
    expect(stage_rows(card).nth(1)).to_contain_text(new_name)
    saved = next(s for s in stages(settings, pipeline) if s["id"] == stage["id"])
    assert (saved["name"], saved["order"]) == (new_name, stage["order"]), saved


# Кейс: Стрелка «вверх» меняет статус местами с соседом
def test_move_stage_up(settings: Page):
    pipeline = create_pipeline(settings)
    settings.reload()
    crm_ready(settings)
    card = expand(settings, pipeline["name"])
    stage_row(card, "Переговоры").locator("button").first.click()

    moved = DEFAULT_STAGES[:2] + ["Переговоры", "Предложение"] + DEFAULT_STAGES[4:]
    expect(stage_rows(card)).to_have_text([re.compile(rf"^{s}") for s in moved])
    for _ in range(20):
        if [s["name"] for s in stages(settings, pipeline)] == moved:
            break
        settings.wait_for_timeout(250)
    assert [s["name"] for s in stages(settings, pipeline)] == moved


# Кейс: Удаление статуса — «Удалить статус "…"?» → «Продолжить»
def test_delete_stage(settings: Page):
    pipeline = create_pipeline(settings)
    stage = stages(settings, pipeline)[2]
    settings.reload()
    crm_ready(settings)
    card = expand(settings, pipeline["name"])
    stage_row(card, stage["name"]).get_by_title("Удалить").click()
    dialog = confirm(settings, f'Удалить статус "{stage["name"]}"?')
    with settings.expect_response(api(f"/pipeline-statuses/{stage['id']}", "DELETE")) as deleted:
        dialog.get_by_role("button", name="Продолжить").click()

    assert deleted.value.ok, deleted.value.text()
    left = [s for s in DEFAULT_STAGES if s != stage["name"]]
    expect(stage_rows(card)).to_have_text([re.compile(rf"^{s}") for s in left])
    assert [s["name"] for s in stages(settings, pipeline)] == left


# --- источники ---------------------------------------------------------------

def source_row(page: Page, name: str):
    return page.locator("main").get_by_text(name, exact=True).locator("xpath=ancestor::*[.//button][1]")


def sources_counter(page: Page, active: int, hidden: int):
    return page.locator("main").get_by_text(f"Откуда приходят сделки ({active} активных, {hidden} скрытых)")


def source_counts(page: Page) -> tuple[int, int]:
    rows = items(crm_get(page, "/sources/", {"include_archived": "true", "limit": 200}))
    active = sum(1 for s in rows if s["is_active"])
    return active, len(rows) - active


# Кейс: Источник — создание, переименование, скрыть ↔ показать, удаление (уходит в скрытые)
def test_source_lifecycle(settings: Page):
    open_tab(settings, "sources")
    active, hidden = source_counts(settings)
    expect(sources_counter(settings, active, hidden)).to_be_visible()

    name = title()
    settings.locator("main").get_by_role("button", name="Добавить", exact=True).click()
    form = overlay(settings, "Новый источник")
    form.get_by_placeholder("Название источника").fill(name)
    with settings.expect_response(api("/api/crm/sources/", "POST")) as created:
        form.get_by_role("button", name="Создать", exact=True).click()
    assert created.value.status == 201, created.value.text()
    source = created.value.json()
    expect(source_row(settings, name).get_by_role("button", name="Активен")).to_be_visible()
    expect(sources_counter(settings, active + 1, hidden)).to_be_visible()

    source_row(settings, name).get_by_title("Редактировать").click()
    form = overlay(settings, "Редактировать источник")
    expect(form.get_by_placeholder("Название источника")).to_have_value(name)
    new_name = title()
    form.get_by_placeholder("Название источника").fill(new_name)
    with settings.expect_response(api(f"/sources/{source['id']}", "PUT")) as renamed:
        form.get_by_role("button", name="Сохранить").click()
    assert renamed.value.ok, renamed.value.text()
    row = source_row(settings, new_name)

    with settings.expect_response(api(f"/sources/{source['id']}", "PUT")) as toggled:
        row.get_by_role("button", name="Активен").click()
    assert toggled.value.request.post_data_json == {"is_active": False}
    expect(row.get_by_role("button", name="Скрыт")).to_be_visible()
    expect(sources_counter(settings, active, hidden + 1)).to_be_visible()
    with settings.expect_response(api(f"/sources/{source['id']}", "PUT")):
        row.get_by_role("button", name="Скрыт").click()
    expect(row.get_by_role("button", name="Активен")).to_be_visible()

    row.get_by_title("Удалить").click()
    dialog = confirm(settings, f'Удалить источник "{new_name}"?')
    with settings.expect_response(api(f"/sources/{source['id']}", "DELETE")) as deleted:
        dialog.get_by_role("button", name="Продолжить").click()
    assert deleted.value.ok, deleted.value.text()
    assert source["id"] not in [s["id"] for s in items(crm_get(settings, "/sources/", {"limit": 200}))]
    saved = crm_get(settings, f"/sources/{source['id']}").json()
    assert (saved["name"], saved["is_active"]) == (new_name, False), saved


# --- причины закрытия --------------------------------------------------------

def reason_row(page: Page, name: str):
    return page.locator("main").get_by_text(name, exact=True).locator("xpath=ancestor::*[.//button][1]")


def type_button(scope, label: str):
    """Кнопка типа причины. По роли с именем её не найти (в имя попадает
    иконка-стрелка), поэтому — по точному тексту."""
    return scope.locator("button", has_text=re.compile(rf"^\s*{re.escape(label)}\s*$"))


def choose_type(page: Page, scope, current: str, new: str):
    type_button(scope, current).first.click()
    type_button(page, new).last.click()


# Кейс: Новая причина закрытия для сделок — «Добавить» неактивна без текста, тип «Сделки»
def test_loss_reason_create(settings: Page):
    open_tab(settings, "loss_reasons")
    main = settings.locator("main")
    expect(main.get_by_role("button", name="Добавить", exact=True)).to_be_disabled()

    name = title()
    main.get_by_placeholder("Например: Ребёнок или спам").fill(name)
    choose_type(settings, main, "Лиды", "Сделки")
    with settings.expect_response(api("/api/crm/loss-reasons/", "POST")) as created:
        main.get_by_role("button", name="Добавить", exact=True).click()

    assert created.value.status == 201, created.value.text()
    saved = created.value.json()
    assert (saved["name"], saved["applies_to"]) == (name, "deal"), saved
    expect(reason_row(settings, name)).to_contain_text("Сделки")
    expect(main.get_by_placeholder("Например: Ребёнок или спам")).to_have_value("")


# Кейс: Правка причины в строке — новое название и тип «Лиды и сделки»
def test_loss_reason_edit(settings: Page):
    reason = create_reason(settings, "deal")
    open_tab(settings, "loss_reasons")
    reason_row(settings, reason["name"]).get_by_title("Редактировать").click()
    editing = settings.locator("main div").filter(has=settings.locator("button[title='Сохранить']")).last
    expect(editing.locator("input")).to_have_value(reason["name"])

    new_name = title()
    editing.locator("input").fill(new_name)
    choose_type(settings, editing, "Сделки", "Лиды и сделки")
    with settings.expect_response(api(f"/loss-reasons/{reason['id']}", "PUT")) as updated:
        editing.get_by_title("Сохранить").click()

    assert updated.value.ok, updated.value.text()
    expect(reason_row(settings, new_name)).to_contain_text("Лиды и сделки")
    saved = next(r for r in items(crm_get(settings, "/loss-reasons/")) if r["id"] == reason["id"])
    assert (saved["name"], saved["applies_to"]) == (new_name, "all"), saved


# Кейс: Удаление причины — «Удалить причину «…»?» → «Продолжить»
def test_loss_reason_delete(settings: Page):
    reason = create_reason(settings, "lead")
    open_tab(settings, "loss_reasons")
    reason_row(settings, reason["name"]).get_by_title("Удалить").click()
    dialog = confirm(settings, f"Удалить причину «{reason['name']}»?")
    expect(dialog).to_contain_text("У уже закрытых карточек причина останется текстом.")
    with settings.expect_response(api(f"/loss-reasons/{reason['id']}", "DELETE")) as deleted:
        dialog.get_by_role("button", name="Продолжить").click()

    assert deleted.value.ok, deleted.value.text()
    expect(settings.locator("main").get_by_text(reason["name"], exact=True)).to_have_count(0)
    assert reason["id"] not in [r["id"] for r in items(crm_get(settings, "/loss-reasons/"))]


def open_deal(page: Page, deal: dict):
    """Карточка сделки по ссылке; повтор — см. 429 в README."""
    expected = re.compile(rf"^D-\d+ {re.escape(deal['title'])} · Сделки · BORD CRM$")
    for attempt in range(3):
        page.goto(f"{CRM}/admin/crm/deals?id={deal['id']}")
        try:
            expect(page).to_have_title(expected, timeout=10_000)
            return
        except AssertionError:
            if attempt == 2:
                raise
            page.wait_for_timeout(2_000)


# Кейс: «Сделка проиграна» предлагает причины для сделок и общие, но не лидовые; выбранная сохраняется
def test_loss_reasons_in_lost_dialog(settings: Page):
    for_deal, for_lead, for_all = create_reason(settings, "deal"), create_reason(settings, "lead"), create_reason(settings, "all")
    main = next(p for p in pipelines(settings, archived=False) if p["is_main"])
    deal = create_deal(settings, main["id"])
    open_deal(settings, deal)

    settings.locator("xpath=//button[starts-with(@title, 'Проиграна\n')]").click()
    dialog = overlay(settings, "Сделка проиграна")
    dialog.get_by_role("button", name="-- Не указана --").click()
    expect(settings.get_by_text(for_deal["name"], exact=True)).to_be_visible()
    expect(settings.get_by_text(for_all["name"], exact=True)).to_be_visible()
    expect(settings.get_by_text(for_lead["name"], exact=True)).to_have_count(0)

    settings.get_by_text(for_deal["name"], exact=True).click()
    with settings.expect_response(lambda r: r.url.endswith(f"/deals/{deal['id']}/stage")) as closed:
        dialog.get_by_role("button", name="Закрыть как проигранную").click()
    assert closed.value.ok, closed.value.text()
    assert closed.value.request.post_data_json["loss_reason_id"] == for_deal["id"]
    saved = crm_get(settings, f"/deals/{deal['id']}").json()
    # причина из справочника — loss_reason_id + её текст в lost_reason; closure_reason — для комментария
    assert (saved["result"], saved["loss_reason_id"], saved["lost_reason"]) == \
        ("lost", for_deal["id"], for_deal["name"]), saved


# --- состояния клиентов -------------------------------------------------------

def state_row(page: Page, name: str):
    return page.locator("main").get_by_text(name, exact=True).locator("xpath=ancestor::*[.//button][1]")


# Кейс: Новое состояние клиента через форму — кодовое имя из названия, описание
def test_client_state_create(settings: Page):
    open_tab(settings, "client_states")
    before = len(items(crm_get(settings, "/client-states/")))
    expect(settings.locator("main").get_by_text(f"Жизненные состояния клиента ({before})")).to_be_visible()

    name = title()
    settings.locator("main").get_by_role("button", name="Добавить состояние").click()
    form = overlay(settings, "Новое состояние")
    form.get_by_placeholder("Например: Холодный").fill(name)
    form.get_by_placeholder("Краткое описание состояния").fill("Описание от автотеста")
    with settings.expect_response(api("/api/crm/client-states/", "POST")) as created:
        form.get_by_role("button", name="Создать", exact=True).click()

    assert created.value.status == 201, created.value.text()
    saved = created.value.json()
    codename = name.lower().replace(" ", "_")
    assert (saved["name"], saved["codename"], saved["description"]) == (name, codename, "Описание от автотеста"), saved
    expect(form).to_be_hidden()
    expect(state_row(settings, name)).to_contain_text(codename)
    expect(settings.locator("main").get_by_text(f"Жизненные состояния клиента ({before + 1})")).to_be_visible()


# Кейс: Правка состояния клиента — окно «Редактировать состояние»
def test_client_state_edit(settings: Page):
    state = create_state(settings)
    open_tab(settings, "client_states")
    state_row(settings, state["name"]).get_by_title("Редактировать").click()
    form = overlay(settings, "Редактировать состояние")
    expect(form.get_by_placeholder("Например: Холодный")).to_have_value(state["name"])
    new_name = title()
    form.get_by_placeholder("Например: Холодный").fill(new_name)
    with settings.expect_response(api(f"/client-states/{state['id']}", "PUT")) as updated:
        form.get_by_role("button", name="Сохранить").click()

    assert updated.value.ok, updated.value.text()
    expect(settings.locator("main").get_by_text(new_name, exact=True)).to_be_visible()
    saved = next(s for s in items(crm_get(settings, "/client-states/")) if s["id"] == state["id"])
    assert saved["name"] == new_name, saved


# Кейс: Удаление состояния клиента — «Удалить состояние "…"?» → «Продолжить», удаляется насовсем
def test_client_state_delete(settings: Page):
    state = create_state(settings)
    open_tab(settings, "client_states")
    state_row(settings, state["name"]).get_by_title("Удалить").click()
    dialog = confirm(settings, f'Удалить состояние "{state["name"]}"?')
    with settings.expect_response(api(f"/client-states/{state['id']}", "DELETE")) as deleted:
        dialog.get_by_role("button", name="Продолжить").click()

    assert deleted.value.ok, deleted.value.text()
    expect(settings.locator("main").get_by_text(state["name"], exact=True)).to_have_count(0)
    assert state["id"] not in [s["id"] for s in items(crm_get(settings, "/client-states/"))]
