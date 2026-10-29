"""Блок 26. CRM — настройки, кроме воронок: скрипты, кастом-поля, валюта и курсы, доска.

Снято 28.10.2026 (crm.bord.one/admin/crm/settings):
- «Скрипты» (?tab=scripts): «Новый скрипт» — меню «Создать пустой» и три
  AI-варианта (из звонков / из примеров / из документа — тратят баланс ИИ, не
  нажимаем). Окно «Новый скрипт» (Название*, Описание, Подсказка AI) →
  POST /scripts/. Карточка скрипта: h3 с названием, «N пунктов», раскрывается
  кликом; меню «Действия» — «Редактировать» / «Архивировать» («Архивировать
  скрипт «…»?» → DELETE /scripts/{id}, скрипт уходит в архив, is_active: false),
  у архивного — «Восстановить» (PUT {is_active: true}). Переключатель «Только
  активные» ↔ «Включая архив», у архивного плашка «Архив». Насовсем скрипт не
  удаляется (повторный DELETE тоже 204, скрипт остаётся) — тестовые копятся в
  архиве. «Добавить пункт» → «Новый пункт»: Заголовок*, Кодовое имя
  («автоматически из заголовка»), Описание, Категория, Порядок, «Обязательный»
  (включён по умолчанию) → POST /scripts/{id}/items. Кодовое имя строится только
  из латиницы: у русского заголовка оно пустое, и второй такой пункт → 409
  (ecom-100260). В строке пункта — «Редактировать» («Редактировать пункт») и
  «Удалить» («Удалить пункт «…»?»);
- «Кастом-поля» (?tab=custom_fields): вкладки Клиенты / Компании / Сделки /
  Задачи / Объекты = entity_type contact / company / deal / task / object
  (в адрес не попадают). «Поле» → «Новое поле»: Название*, Тип (13 типов,
  «Список (один вариант)» даёт «Варианты» + кнопку «Вариант»), «Обязательное»,
  «Показывать в списке» (по умолчанию выключено) → POST /custom-fields/. В строке:
  «Переместить поле «…» выше/ниже» (PUT /custom-fields/reorder {field_ids}),
  «Редактировать» (окно «Поле», тип менять нельзя), «В архив…» (сразу,
  PUT {is_archived: true}, плашка «в архиве», «Вернуть из архива»),
  «Удалить безвозвратно» («Удалить поле «…»?»). Поле видно в карточке записи
  в «Дополнительных полях» (span[title=<поле>] + значение кликом), с «Показывать
  в списке» — колонкой в списке;
- «Валюта и курсы» (?tab=currency): «Валюта отчётов» (MDL / EUR / USD, на всю
  организацию) + «Сохранить» → PUT /settings/base-currency; итоги колонок доски
  сделок тогда «≈ 1 277,61 €» с подсказкой «Курс 20,176 MDL за 1 EUR на …».
  Курсы — общая книга платформы: GET /fx-rates?page&limit=50, «Всего курсов: N»,
  «1 / M», фильтры валюты и источника. Ручная правка — только у суперадмина
  (в «Действиях» у нас «—»); «Обновить курсы с НБМ» пишет в общую книгу — не жмём;
- «Статусы воронки» сверху: «Порядок карточек на доске по умолчанию»
  (role=option «По последнему событию» / «По задачам…» → PUT /settings/board-sort
  activity / task_due) и «Карточка на доске» → «Настроить»: переключатели
  role=switch «Скрыть «<блок>»» → PUT /settings/card-fields {entity_type:
  deal_card, hidden}; блок пропадает с карточки на доске.

Настройки организации (валюта, порядок, карточка) фикстура возвращает как было;
свои поля удаляются, скрипты уходят в архив, сделки удаляются, клиенты — в архив.
"""
import random
import re

import pytest
from playwright.sync_api import Page, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from helpers.crm import (CRM, QA_PREFIX, crm_delete, crm_get, crm_me, crm_post, crm_put, crm_ready, open_crm,
                         until_loaded)

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/settings"
ENTITIES = ("contact", "company", "deal", "task", "object")
CODENAME_BUG = "ecom-100260: у русского заголовка кодовое имя пустое, второй такой пункт → 409"


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


def title() -> str:
    return f"{QA_PREFIX} {tag()}"


def api(path: str, method: str):
    return lambda r: path in r.url and r.request.method == method


def org_settings(page: Page) -> dict:
    return crm_get(page, "/settings").json()


# --- данные через API --------------------------------------------------------

def create_script(page: Page, **fields) -> dict:
    resp = crm_post(page, "/scripts/", {"name": title(), **fields})
    assert resp.status in (200, 201), resp.text()
    return resp.json()


def add_item(page: Page, script: dict, label: str, codename: str, order: int = 1) -> dict:
    resp = crm_post(page, f"/scripts/{script['id']}/items",
                    {"codename": codename, "label": label, "order": order, "is_required": True})
    assert resp.status in (200, 201), resp.text()
    return resp.json()


def script_items(page: Page, script: dict) -> list:
    return sorted(crm_get(page, f"/scripts/{script['id']}/items").json()["items"], key=lambda i: i["order"])


def scripts(page: Page, archived: bool = True) -> list:
    return crm_get(page, "/scripts/", {"include_inactive": "true"} if archived else None).json()["items"]


def create_field(page: Page, entity: str = "contact", **fields) -> dict:
    data = {"entity_type": entity, "name": title(), "field_type": "text", "show_in_list": True, **fields}
    resp = crm_post(page, "/custom-fields/", data)
    assert resp.status in (200, 201), resp.text()
    return resp.json()


def fields(page: Page, entity: str = "contact") -> list:
    return crm_get(page, "/custom-fields/", {"entity_type": entity, "include_archived": "true"}).json()["items"]


def create_contact(page: Page) -> dict:
    resp = crm_post(page, "/contacts/", {"first_name": f"{QA_PREFIX} {tag()}", "responsible_id": crm_me(page)["id"]})
    assert resp.status == 201, resp.text()
    return resp.json()


def create_deal(page: Page, **extra) -> dict:
    main = next(p for p in crm_get(page, "/pipeline-statuses/pipelines").json()["items"] if p["is_main"])
    resp = crm_post(page, "/deals/", {"title": title(), "pipeline_id": main["id"],
                                       "responsible_id": crm_me(page)["id"], **extra})
    assert resp.status == 201, resp.text()
    return resp.json()


def cleanup(page: Page, saved: dict):
    crm_put(page, "/settings/base-currency", {"base_currency": saved["base_currency"]})
    crm_put(page, "/settings/board-sort", {"board_sort": saved["board_sort"]})
    crm_put(page, "/settings/card-fields", {"entity_type": "deal_card",
                                            "hidden": saved["card_fields"]["deal_card"]["hidden"]})
    for entity in ENTITIES:
        for f in fields(page, entity):
            if f["name"].startswith(QA_PREFIX):
                crm_delete(page, f"/custom-fields/{f['id']}")
    for s in scripts(page, archived=False):
        if s["name"].startswith(QA_PREFIX):
            crm_delete(page, f"/scripts/{s['id']}")  # насовсем не удаляется — уходит в архив
    resp = crm_get(page, "/deals/", {"search": QA_PREFIX, "limit": 100})
    for d in resp.json()["items"] if resp.ok else []:
        if d["title"].startswith(QA_PREFIX):
            crm_delete(page, f"/deals/{d['id']}")
    resp = crm_get(page, "/contacts/", {"search": QA_PREFIX, "limit": 200})
    ids = [c["id"] for c in resp.json()["items"] if c["first_name"].startswith(QA_PREFIX)] if resp.ok else []
    if ids:
        crm_post(page, "/bulk/contacts/delete", {"contact_ids": ids})


@pytest.fixture
def settings(auth_page: Page):
    open_crm(auth_page, PAGE + "?tab=scripts")
    saved = org_settings(auth_page)
    yield auth_page
    cleanup(auth_page, saved)


def open_tab(page: Page, tab: str):
    page.goto(f"{CRM}{PAGE}?tab={tab}")
    crm_ready(page)


def overlay(page: Page, text: str):
    """Окна CRM — div.fixed.inset-0 (не role=dialog)."""
    return page.locator("div.fixed.inset-0").filter(has_text=text).last


def floating(page: Page):
    """Выпадашки CRM — плавающий div с position: fixed поверх страницы."""
    return page.locator("div[style*='position: fixed']")


# --- скрипты -----------------------------------------------------------------

def script_card(page: Page, name: str):
    heading = page.get_by_role("heading", name=name, level=3, exact=True)
    return page.locator("main div.rounded-2xl").filter(has=heading)


def expand_script(page: Page, name: str):
    card = script_card(page, name)
    if not card.get_by_role("button", name="Добавить пункт").is_visible():
        card.get_by_role("heading", name=name, level=3).click()
    expect(card.get_by_role("button", name="Добавить пункт")).to_be_visible()
    return card


def item_row(card, label: str):
    return card.locator("div.group").filter(has_text=label)


def script_menu(page: Page, name: str):
    script_card(page, name).get_by_role("button", name="Действия").click()
    menu = page.locator("main div.absolute").filter(has=page.get_by_role("button", name="Редактировать"))
    expect(menu).to_be_visible()
    return menu


# Кейс: Скрипты на экране = API — название, описание, число пунктов, пункты по порядку
def test_scripts_match_api(settings: Page):
    script = create_script(settings, description=f"описание {tag()}")
    add_item(settings, script, "Greeted the client", "greeted", order=1)
    add_item(settings, script, "Asked about budget", "budget", order=2)
    open_tab(settings, "scripts")

    card = script_card(settings, script["name"])
    expect(card).to_contain_text(script["description"])
    expect(card).to_contain_text("2 пунктов")
    card = expand_script(settings, script["name"])
    items = script_items(settings, script)
    expect(card.locator("div.group")).to_have_count(len(items))
    for n, item in enumerate(items):
        expect(card.locator("div.group").nth(n)).to_contain_text(item["label"])
        expect(card.locator("div.group").nth(n)).to_contain_text(item["codename"])
    active = scripts(settings, archived=False)
    expect(settings.get_by_role("heading", name=re.compile(r"^Скрипты продаж"))).to_contain_text(f"({len(active)})")


# Кейс: Новый скрипт через форму — без названия «Создать» неактивна; скрипт появляется в списке
def test_create_script_via_form(settings: Page):
    open_tab(settings, "scripts")
    settings.locator("main").get_by_role("button", name="Новый скрипт").click()
    settings.locator("main div.absolute button").filter(has_text="Создать пустой").click()
    form = overlay(settings, "Новый скрипт")
    create = form.get_by_role("button", name="Создать", exact=True)
    expect(create).to_be_disabled()

    name, description = title(), f"описание {tag()}"
    form.locator("input").first.fill(name)
    form.get_by_placeholder("Короткое описание скрипта").fill(description)
    with settings.expect_response(api("/api/crm/scripts/", "POST")) as created:
        create.click()

    assert created.value.ok, created.value.text()
    saved = created.value.json()
    assert (saved["name"], saved["description"], saved["is_active"]) == (name, description, True), saved
    expect(form).to_be_hidden()
    expect(script_card(settings, name)).to_contain_text("0 пунктов")


# Кейс: Пункт скрипта через форму — кодовое имя из латинского заголовка, категория, «Обязательный» по умолчанию
def test_add_item_via_form(settings: Page):
    script = create_script(settings)
    open_tab(settings, "scripts")
    card = expand_script(settings, script["name"])
    card.get_by_role("button", name="Добавить пункт").click()
    form = overlay(settings, "Новый пункт")
    form.get_by_placeholder("Например: «Поздоровался и представился»").fill("Asked about budget")
    form.get_by_role("button", name="Другое").click()
    floating(settings).get_by_role("button", name="Приветствие", exact=True).click()
    expect(form.get_by_role("button", name="Приветствие")).to_be_visible()
    with settings.expect_response(api(f"/scripts/{script['id']}/items", "POST")) as created:
        form.get_by_role("button", name="Создать", exact=True).click()

    assert created.value.ok, created.value.text()
    expect(form).to_be_hidden()
    [item] = script_items(settings, script)
    assert (item["label"], item["codename"], item["is_required"]) == \
        ("Asked about budget", "asked_about_budget", True), item
    assert item["category"] != "other", item
    row = item_row(card, "Asked about budget")
    expect(row).to_contain_text("asked_about_budget")
    expect(row).to_contain_text("приветствие", ignore_case=True)
    expect(row).to_contain_text("обязательно", ignore_case=True)
    expect(script_card(settings, script["name"])).to_contain_text("1 пунктов")


# Кейс: Второй пункт с русским заголовком добавляется (кодовое имя «автоматически из заголовка»)
@pytest.mark.xfail(reason=CODENAME_BUG, strict=False)
def test_second_cyrillic_item(settings: Page):
    script = create_script(settings)
    add_item(settings, script, "Поздоровался и представился", "")  # так сохраняет первый пункт сам экран
    open_tab(settings, "scripts")
    card = expand_script(settings, script["name"])
    card.get_by_role("button", name="Добавить пункт").click()
    form = overlay(settings, "Новый пункт")
    form.get_by_placeholder("Например: «Поздоровался и представился»").fill("Выявил потребность")
    with settings.expect_response(api(f"/scripts/{script['id']}/items", "POST")) as created:
        form.get_by_role("button", name="Создать", exact=True).click()

    assert created.value.ok, f"{created.value.status} {created.value.text()}"
    expect(form).to_be_hidden()
    assert [i["label"] for i in script_items(settings, script)] == ["Поздоровался и представился", "Выявил потребность"]


# Кейс: Правка пункта — окно «Редактировать пункт» с текущими значениями
def test_edit_item(settings: Page):
    script = create_script(settings)
    item = add_item(settings, script, "Greeted the client", "greeted")
    open_tab(settings, "scripts")
    card = expand_script(settings, script["name"])
    item_row(card, item["label"]).get_by_role("button", name="Редактировать").click()
    form = overlay(settings, "Редактировать пункт")
    label = form.get_by_placeholder("Например: «Поздоровался и представился»")
    expect(label).to_have_value(item["label"])
    label.fill("Greeted and introduced")
    with settings.expect_response(api(f"/items/{item['id']}", "PUT")) as updated:
        form.get_by_role("button", name="Сохранить").click()

    assert updated.value.ok, updated.value.text()
    expect(form).to_be_hidden()
    expect(item_row(card, "Greeted and introduced")).to_be_visible()
    [saved] = script_items(settings, script)
    assert (saved["label"], saved["codename"]) == ("Greeted and introduced", "greeted"), saved


# Кейс: Удаление пункта — «Удалить пункт «…»?» → «Продолжить»
def test_delete_item(settings: Page):
    script = create_script(settings)
    keep = add_item(settings, script, "Greeted the client", "greeted", order=1)
    gone = add_item(settings, script, "Asked about budget", "budget", order=2)
    open_tab(settings, "scripts")
    card = expand_script(settings, script["name"])
    item_row(card, gone["label"]).get_by_role("button", name="Удалить").click()
    dialog = overlay(settings, f"Удалить пункт «{gone['label']}»?")
    with settings.expect_response(api(f"/items/{gone['id']}", "DELETE")) as deleted:
        dialog.get_by_role("button", name="Продолжить").click()

    assert deleted.value.ok, deleted.value.text()
    expect(item_row(card, gone["label"])).to_have_count(0)
    expect(item_row(card, keep["label"])).to_be_visible()
    assert [i["id"] for i in script_items(settings, script)] == [keep["id"]]


# Кейс: Переименование скрипта — «Действия» → «Редактировать»
def test_rename_script(settings: Page):
    script = create_script(settings)
    open_tab(settings, "scripts")
    script_menu(settings, script["name"]).get_by_role("button", name="Редактировать").click()
    form = overlay(settings, "Сохранить")
    expect(form.locator("input").first).to_have_value(script["name"])
    new_name = title()
    form.locator("input").first.fill(new_name)
    with settings.expect_response(api(f"/scripts/{script['id']}", "PUT")) as updated:
        form.get_by_role("button", name="Сохранить").click()

    assert updated.value.ok, updated.value.text()
    expect(script_card(settings, new_name)).to_be_visible()
    assert crm_get(settings, f"/scripts/{script['id']}").json()["name"] == new_name


# Кейс: Архив скрипта ↔ «Восстановить»; архив виден только при «Включая архив»
def test_archive_and_restore_script(settings: Page):
    script = create_script(settings)
    open_tab(settings, "scripts")
    script_menu(settings, script["name"]).get_by_role("button", name="Архивировать").click()
    dialog = overlay(settings, f"Архивировать скрипт «{script['name']}»?")
    with settings.expect_response(api(f"/scripts/{script['id']}", "DELETE")) as archived:
        dialog.get_by_role("button", name="Продолжить").click()

    assert archived.value.ok, archived.value.text()
    expect(script_card(settings, script["name"])).to_have_count(0)
    saved = next(s for s in scripts(settings) if s["id"] == script["id"])
    assert saved["is_active"] is False, saved

    settings.locator("main").get_by_role("button", name="Только активные").click()
    expect(settings.locator("main").get_by_role("button", name="Включая архив")).to_be_visible()
    expect(script_card(settings, script["name"])).to_contain_text("архив", ignore_case=True)
    script_menu(settings, script["name"]).get_by_role("button", name="Восстановить").click()
    # «Восстановить» без подтверждения
    expect(script_card(settings, script["name"])).not_to_contain_text("архив", ignore_case=True)
    assert crm_get(settings, f"/scripts/{script['id']}").json()["is_active"] is True


# --- кастом-поля -------------------------------------------------------------

FIELD_TABS = {"contact": "Клиенты", "company": "Компании", "deal": "Сделки", "task": "Задачи", "object": "Объекты"}


def open_fields(page: Page, entity: str = "contact"):
    """«Клиенты» открыты по умолчанию — список полей приходит вместе с вкладкой."""
    def action():
        open_tab(page, "custom_fields")
        if entity != "contact":
            page.locator("main").get_by_role("button", name=FIELD_TABS[entity], exact=True).click()

    until_loaded(page, action, lambda r: f"/custom-fields/?entity_type={entity}" in r.url)


def field_row(page: Page, name: str):
    return page.locator("main div").filter(has_text=name).filter(
        has=page.get_by_role("button", name="Редактировать")).last


def card_field(page: Page, name: str):
    """Значение своего поля в карточке записи — блок «Дополнительные поля»."""
    return page.locator(f"span[title='{name}'] + div")


# Кейс: Свои поля на каждой вкладке = API — название, номер поля
@pytest.mark.parametrize("entity", ["contact", "deal", "task"])
def test_fields_match_api(settings: Page, entity):
    mine = create_field(settings, entity)
    open_fields(settings, entity)

    expected = [f for f in fields(settings, entity) if not f.get("is_archived")]
    for f in expected:
        expect(field_row(settings, f["name"])).to_contain_text(f"ID {f['id']}")
    expect(field_row(settings, mine["name"])).to_contain_text("Текст")
    for other in set(FIELD_TABS) - {entity}:
        assert mine["id"] not in [f["id"] for f in fields(settings, other)]


# Кейс: Поле-список через форму — «Создать» без названия неактивна; колонка в списке клиентов и значение в карточке
def test_create_select_field_and_fill_in_card(settings: Page):
    open_fields(settings, "contact")
    settings.locator("main").get_by_role("button", name="Поле", exact=True).click()
    form = overlay(settings, "Новое поле")
    create = form.get_by_role("button", name="Создать", exact=True)
    expect(create).to_be_disabled()

    name = title()
    form.get_by_role("textbox").first.fill(name)
    form.get_by_role("button", name="Текст", exact=True).click()
    floating(settings).get_by_text("Список (один вариант)", exact=True).click()
    form.get_by_role("button", name="Вариант", exact=True).click()
    form.get_by_role("button", name="Вариант", exact=True).click()
    form.get_by_placeholder("Вариант 1").fill("Холодный")
    form.get_by_placeholder("Вариант 2").fill("Горячий")
    form.get_by_label("Показывать в списке").check()
    with settings.expect_response(api("/api/crm/custom-fields/", "POST")) as created:
        create.click()

    assert created.value.ok, created.value.text()
    field = created.value.json()
    assert (field["entity_type"], field["field_type"], field["show_in_list"]) == ("contact", "select", True), field
    assert [o["value"] for o in field["options"]] == ["Холодный", "Горячий"], field["options"]
    expect(form).to_be_hidden()
    expect(field_row(settings, name)).to_contain_text("Список (один вариант)")

    contact = create_contact(settings)
    settings.goto(f"{CRM}/admin/crm/contacts?id={contact['id']}")
    expect(settings.locator("main thead").get_by_text(name, exact=True)).to_be_visible(timeout=20_000)
    card_field(settings, name).click()
    with settings.expect_response(api(f"/custom-fields/values/contact/{contact['id']}", "PUT")) as saved:
        floating(settings).get_by_text("Горячий", exact=True).click()

    assert saved.value.ok, saved.value.text()
    expect(card_field(settings, name)).to_contain_text("Горячий")
    values = crm_get(settings, f"/custom-fields/values/contact/{contact['id']}").json()["items"]
    assert [v["value"] for v in values if v["definition_id"] == field["id"]] == ["Горячий"], values


# Кейс: Поле сделок видно в карточке сделки («Дополнительные поля»)
def test_deal_field_in_deal_card(settings: Page):
    field = create_field(settings, "deal")
    deal = create_deal(settings)
    card_title = re.compile(rf"^D-\d+ {re.escape(deal['title'])} · Сделки · BORD CRM$")
    for attempt in range(3):
        settings.goto(f"{CRM}/admin/crm/deals?id={deal['id']}")
        try:
            expect(settings).to_have_title(card_title, timeout=10_000)
            break
        except AssertionError:
            if attempt == 2:
                raise
            settings.wait_for_timeout(2_000)  # всплеск запросов карточки → 429, см. блок 22

    expect(settings.locator(f"span[title='{field['name']}']")).to_be_visible()
    expect(card_field(settings, field["name"])).to_contain_text("—")


# Кейс: Переименование поля — окно «Поле», тип после создания не меняется
def test_edit_field(settings: Page):
    field = create_field(settings)
    open_fields(settings)
    field_row(settings, field["name"]).get_by_role("button", name="Редактировать").click()
    form = overlay(settings, "Тип нельзя изменить после создания")
    name_input = form.get_by_role("textbox").first
    expect(name_input).to_have_value(field["name"])
    new_name = title()
    name_input.fill(new_name)
    with settings.expect_response(api(f"/custom-fields/{field['id']}", "PUT")) as updated:
        form.get_by_role("button", name="Сохранить").click()

    assert updated.value.ok, updated.value.text()
    expect(field_row(settings, new_name)).to_be_visible()
    saved = next(f for f in fields(settings) if f["id"] == field["id"])
    assert (saved["name"], saved["field_type"]) == (new_name, "text"), saved


# Кейс: Стрелка «ниже» меняет поля местами — порядок в API тот же
def test_reorder_fields(settings: Page):
    first = create_field(settings, sort=0)
    second = create_field(settings, sort=1)
    open_fields(settings)
    order_before = [f["id"] for f in fields(settings)]
    assert order_before.index(first["id"]) < order_before.index(second["id"]), order_before
    with settings.expect_response(api("/custom-fields/reorder", "PUT")) as reordered:
        settings.get_by_role("button", name=f"Переместить поле «{first['name']}» ниже").click()

    assert reordered.value.ok, reordered.value.text()
    ids = reordered.value.request.post_data_json["field_ids"]
    assert ids.index(second["id"]) + 1 == ids.index(first["id"]), ids
    order_after = [f["id"] for f in fields(settings)]
    assert order_after.index(second["id"]) < order_after.index(first["id"]), order_after
    expect(field_row(settings, second["name"])).to_be_visible()
    rows_text = settings.locator("main").inner_text()
    assert rows_text.index(second["name"]) < rows_text.index(first["name"])


# Кейс: Поле в архив — пропадает из карточки, «Вернуть из архива» возвращает
def test_archive_field_hides_from_card(settings: Page):
    field = create_field(settings)
    contact = create_contact(settings)
    open_fields(settings)
    row = field_row(settings, field["name"])
    with settings.expect_response(api(f"/custom-fields/{field['id']}", "PUT")) as archived:
        row.get_by_role("button", name=re.compile("^В архив")).click()

    assert archived.value.ok, archived.value.text()
    assert archived.value.request.post_data_json == {"is_archived": True}
    expect(row).to_contain_text("в архиве")
    settings.goto(f"{CRM}/admin/crm/contacts?id={contact['id']}")
    expect(settings.get_by_role("heading", name=contact["first_name"], level=2)).to_be_visible(timeout=20_000)
    expect(settings.locator(f"span[title='{field['name']}']")).to_have_count(0)

    open_fields(settings)
    with settings.expect_response(api(f"/custom-fields/{field['id']}", "PUT")) as restored:
        field_row(settings, field["name"]).get_by_role("button", name=re.compile("^Вернуть из архива")).click()
    assert restored.value.ok, restored.value.text()
    expect(field_row(settings, field["name"])).not_to_contain_text("в архиве")
    settings.goto(f"{CRM}/admin/crm/contacts?id={contact['id']}")
    expect(settings.locator(f"span[title='{field['name']}']")).to_be_visible(timeout=20_000)


# Кейс: Удаление поля — «Удалить поле «…»?», поле пропадает из списка и API
def test_delete_field(settings: Page):
    field = create_field(settings)
    open_fields(settings)
    field_row(settings, field["name"]).get_by_role("button", name="Удалить безвозвратно").click()
    dialog = overlay(settings, f"Удалить поле «{field['name']}»?")
    expect(dialog).to_contain_text("Все его значения у всех записей будут удалены безвозвратно")
    with settings.expect_response(api(f"/custom-fields/{field['id']}", "DELETE")) as deleted:
        dialog.get_by_role("button", name="Продолжить").click()

    assert deleted.value.ok, deleted.value.text()
    expect(settings.locator("main").get_by_text(field["name"])).to_have_count(0)
    assert field["id"] not in [f["id"] for f in fields(settings)]


# --- валюта и курсы ----------------------------------------------------------

def rate_text(r: dict) -> str:
    """«1 EUR = 20,176 MDL» — курс как в API, с запятой."""
    return f"1 {r['currency']} = {format(r['rate'], 'g').replace('.', ',')} MDL"


def ru_date(iso: str) -> str:
    y, m, d = iso[:10].split("-")
    return f"{d}.{m}.{y}"


def rate_rows(page: Page):
    return page.locator("main table tbody tr")


def rates(page: Page, number: int = 1, **params) -> dict:
    return crm_get(page, "/fx-rates", {"page": number, "limit": 50, **params}).json()


# Кейс: Книга курсов = API — первые строки, «Всего курсов», число страниц; ручной правки нет
def test_rates_table_matches_api(settings: Page):
    open_tab(settings, "currency")
    data = rates(settings)
    expect(rate_rows(settings)).to_have_count(len(data["items"]))
    for n, r in enumerate(data["items"][:10]):
        row = rate_rows(settings).nth(n)
        expect(row).to_contain_text(ru_date(r["rate_date"]))
        expect(row).to_contain_text(rate_text(r))
    expect(settings.get_by_text(f"Всего курсов: {data['total']}", exact=True)).to_be_visible()
    expect(settings.locator("main").get_by_text(f"1 / {data['pages']}", exact=True)).to_be_visible()
    # курс общий для платформы — правка только у суперадмина, у нас в «Действиях» прочерк
    expect(rate_rows(settings).locator("td").last.get_by_role("button")).to_have_count(0)


# Кейс: Фильтр по валюте — в таблице только EUR, итог = API
def test_rates_filter_by_currency(settings: Page):
    open_tab(settings, "currency")
    settings.get_by_role("button", name="Фильтр по валюте").click()
    with settings.expect_response(lambda r: "/fx-rates?" in r.url and "currency=EUR" in r.url):
        floating(settings).get_by_role("button", name="EUR", exact=True).click()

    data = rates(settings, currency="EUR")
    expect(settings.get_by_text(f"Всего курсов: {data['total']}", exact=True)).to_be_visible()
    expect(rate_rows(settings)).to_have_count(len(data["items"]))
    for n, r in enumerate(data["items"][:5]):
        expect(rate_rows(settings).nth(n)).to_contain_text(rate_text(r))
    texts = rate_rows(settings).locator("td:nth-child(2)").all_inner_texts()
    assert all(t.startswith("1 EUR = ") for t in texts), texts[:5]


# Кейс: Следующая страница курсов — «2 / N», строки = API, page=2
def test_rates_next_page(settings: Page):
    open_tab(settings, "currency")
    first = rates(settings)
    with settings.expect_response(lambda r: "/fx-rates?" in r.url and "page=2" in r.url):
        settings.get_by_role("button", name="Следующая страница").click()

    second = rates(settings, 2)
    expect(settings.locator("main").get_by_text(f"2 / {first['pages']}", exact=True)).to_be_visible()
    expect(rate_rows(settings).first).to_contain_text(rate_text(second["items"][0]))
    expect(rate_rows(settings).first).to_contain_text(ru_date(second["items"][0]["rate_date"]))
    expect(settings.get_by_role("button", name="Предыдущая страница")).to_be_enabled()


# Кейс: Валюта отчётов EUR — сохраняется на организацию, итоги доски сделок в € с курсом в подсказке
def test_report_currency_eur(settings: Page):
    create_deal(settings, amount=777)
    open_tab(settings, "currency")
    save = settings.locator("main").get_by_role("button", name="Сохранить")
    expect(save).to_be_disabled()
    settings.get_by_role("button", name="Валюта отчётов").click()
    floating(settings).get_by_role("button", name="EUR", exact=True).click()
    expect(save).to_be_enabled()
    with settings.expect_response(api("/settings/base-currency", "PUT")) as updated:
        save.click()

    assert updated.value.ok, updated.value.text()
    assert updated.value.request.post_data_json == {"base_currency": "EUR"}
    assert org_settings(settings)["base_currency"] == "EUR"

    for attempt in range(3):  # доска после всплеска запросов может получить 429 — см. блок 22
        try:
            with settings.expect_response(lambda r: "/api/crm/deals/pipeline?" in r.url and r.ok, timeout=10_000):
                settings.goto(f"{CRM}/admin/crm/deals")
            break
        except PlaywrightTimeoutError:
            settings.wait_for_timeout(2_000)
    head = settings.locator("main span[title='Новая']").locator(
        "xpath=ancestor::div[contains(@class,'justify-between')][1]/..")
    expect(head).to_contain_text("€")
    expect(head.locator("[title^='Курс ']").first).to_have_attribute(
        "title", re.compile(r"^Курс [\d\s,]+ MDL за 1 EUR на \d{2}\.\d{2}\.\d{4}$"))


# --- доска сделок: порядок и карточка ----------------------------------------

# Кейс: Порядок карточек по умолчанию «По задачам» сохраняется на организацию
def test_board_sort_default(settings: Page):
    open_tab(settings, "pipeline")
    picker = settings.get_by_role("button", name="Порядок карточек на доске по умолчанию")
    picker.click()
    with settings.expect_response(api("/settings/board-sort", "PUT")) as updated:
        settings.get_by_role("option", name="По задачам: просроченные и ближайшие сверху").click()

    assert updated.value.ok, updated.value.text()
    assert updated.value.request.post_data_json == {"board_sort": "task_due"}
    expect(picker).to_contain_text("По задачам")
    assert org_settings(settings)["board_sort"] == "task_due"
    settings.reload()
    crm_ready(settings)
    expect(picker).to_contain_text("По задачам")


# Кейс: «Карточка на доске» — скрытая «Сумма» пропадает с карточки сделки на доске и возвращается
def test_card_fields_hide_amount(settings: Page):
    deal = create_deal(settings, amount=777)
    open_tab(settings, "pipeline")
    settings.get_by_role("button", name=re.compile("^Карточка на доске")).click()
    switch = settings.get_by_role("switch", name="Скрыть «Сумма»")
    with settings.expect_response(api("/settings/card-fields", "PUT")) as updated:
        switch.click()

    assert updated.value.ok, updated.value.text()
    assert updated.value.request.post_data_json == {"entity_type": "deal_card", "hidden": ["amount"]}
    assert org_settings(settings)["card_fields"]["deal_card"]["hidden"] == ["amount"]
    row = settings.locator("main div").filter(has=settings.get_by_role("switch", name=re.compile("«Сумма»$"))).last
    expect(row).to_contain_text("скрыто")

    def board_card():
        for attempt in range(3):  # 429 на всплеске — см. блок 22
            try:
                with settings.expect_response(lambda r: "/api/crm/deals/pipeline?" in r.url and r.ok,
                                              timeout=10_000):
                    settings.goto(f"{CRM}/admin/crm/deals")
                break
            except PlaywrightTimeoutError:
                settings.wait_for_timeout(2_000)
        link = settings.get_by_role("link", name=deal["title"], exact=True)
        return settings.locator("main [draggable=true]").filter(has=link)

    card = board_card()
    expect(card).to_be_visible()
    expect(card).not_to_contain_text("777")

    crm_put(settings, "/settings/card-fields", {"entity_type": "deal_card", "hidden": []})
    card = board_card()
    expect(card).to_contain_text("777")
