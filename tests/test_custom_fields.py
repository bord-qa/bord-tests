"""Блок 13. Кастомные поля: app.bord.one/admin/settings/custom-fields.

Снято 22.10.2026:
- вкладки модулей CRM / HRM / Финансы / Склад, «Тип сущности» —
  выпадашка; список: GET /api/custom-fields/definitions/?module=&entity_type=;
- «Добавить поле» → окно «Новое кастомное поле»: Название поля, Системное имя
  (подставляется из названия, пускает только латиницу/цифры/«_» — кириллица
  выбрасывается), Тип поля (12 типов), у «Выбор из списка» — варианты
  (Название / value), Placeholder, Значение по умолчанию, Обязательное,
  Видимое → POST …/definitions/ → 201; список без вариантов → 400, занятое
  системное имя → 409; без вариантов (и с одним пустым вариантом)
  форма сама пишет «Добавьте хотя бы один вариант» и запрос не шлёт;
- в строке: «Скрыть»/«Показать» (PUT {is_visible}), «Редактировать» (окно
  «Редактировать поле», системное имя не меняется, PUT), «Удалить» → окно
  «Удалить поле "…"? Все значения будут потеряны.» → DELETE ?force=true;
- 28.10.2026 вкладку «Тикеты» убрали (ecom-100158: сервер не знал модуль),
  пустой вариант списка теперь отсекает фронт (ecom-100159).

Поля общие на организацию и видны в модулях, поэтому тесты заводят только
«QA Autotest …» с системным именем qa_… у CRM (Лид, Контакт) и удаляют их.
"""
import re
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_delete, api_get, api_post
from helpers.modal import modal
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

API = "/api/custom-fields/definitions/"
ENTITIES = {
    "CRM": ("crm", ["Лид", "Контакт", "Компания", "Сделка", "Задача"]),
    "HRM": ("hrm", ["Сотрудник", "Кандидат", "Заявка на отпуск", "Обучение"]),
    "Финансы": ("finance", ["Транзакция", "Счёт", "Контрагент", "Платёж"]),
    "Склад": ("wms", ["Товар", "Заказ", "Склад", "Приёмка", "Отгрузка"]),
}
USED = [("crm", "lead"), ("crm", "contact")]


def sysname(tag: str = "f") -> str:
    return f"qa_{tag}_{int(time.time() * 1000)}"


def definitions(page: Page, module: str = "crm", entity: str = "lead") -> list:
    resp = api_get(page, f"{API}?module={module}&entity_type={entity}")
    assert resp.ok, resp.text()
    return resp.json()


def create_field(page: Page, entity: str = "lead", **fields) -> dict:
    data = {"module": "crm", "entity_type": entity, "field_name": sysname(), "field_label": "QA Autotest поле",
            "field_type": "text", "is_required": False, "is_visible": True, "options": [], **fields}
    resp = api_post(page, API, data)
    assert resp.status == 201, resp.text()
    return resp.json()


def cleanup(page: Page):
    for module, entity in USED:
        resp = api_get(page, f"{API}?module={module}&entity_type={entity}")
        for item in resp.json() if resp.ok else []:
            if item["field_name"].startswith("qa_"):
                api_delete(page, f"{API}{item['id']}?force=true")


@pytest.fixture
def cf(auth_page: Page):
    auth_page.goto("/admin/settings/custom-fields")
    expect(auth_page.locator("main").get_by_role("button", name="Добавить поле")).to_be_visible()
    settle(auth_page)
    yield auth_page
    cleanup(auth_page)


def reload(page: Page):
    page.reload()
    expect(page.locator("main").get_by_role("button", name="Добавить поле")).to_be_visible()
    settle(page)


def row(page: Page, name: str):
    return page.locator("main table tbody tr").filter(has_text=name)


def open_new(page: Page):
    page.locator("main").get_by_role("button", name="Добавить поле").click()
    return modal(page, "Новое кастомное поле")


def pick(page: Page, scope, current: str, option: str):
    scope.get_by_role("button", name=current, exact=True).click()
    page.get_by_role("option", name=option, exact=True).click()


def submit(page: Page, form, method: str = "POST"):
    with page.expect_response(lambda r: API in r.url and r.request.method == method) as resp:
        form.get_by_role("button", name=re.compile("^(Создать|Сохранить)$")).click()
    return resp.value


# Кейс: Сущности модуля загружаются
@pytest.mark.smoke
@pytest.mark.parametrize("tab", ENTITIES)
def test_module_entities_load(cf: Page, tab):
    module, labels = ENTITIES[tab]
    main = cf.locator("main")

    if tab == "CRM":  # открыта по умолчанию — клик по ней ничего не грузит
        main.get_by_role("button", name="HRM", exact=True).click()
    with cf.expect_response(lambda r: f"module={module}&" in r.url) as loaded:
        main.get_by_role("button", name=tab, exact=True).click()

    assert loaded.value.ok, f"{tab}: {loaded.value.status} {loaded.value.text()}"
    main.locator("button[aria-haspopup='listbox']").click()
    expect(cf.get_by_role("option")).to_have_text(labels)
    cf.keyboard.press("Escape")


# Кейс: Создание текстового поля
@pytest.mark.writes_data
def test_create_text_field(cf: Page):
    name = sysname("text")
    form = open_new(cf)
    form.get_by_placeholder("Например: ИНН").fill("QA Autotest ИНН")
    form.get_by_placeholder("inn", exact=True).fill(name)
    form.get_by_placeholder("Подсказка в поле ввода").fill("13 цифр")
    form.get_by_text("Обязательное", exact=True).click()

    resp = submit(cf, form)

    assert resp.status == 201, resp.text()
    sent = resp.request.post_data_json
    assert {k: sent[k] for k in ("module", "entity_type", "field_name", "field_label", "field_type", "is_required", "is_visible", "placeholder")} == {
        "module": "crm", "entity_type": "lead", "field_name": name, "field_label": "QA Autotest ИНН",
        "field_type": "text", "is_required": True, "is_visible": True, "placeholder": "13 цифр",
    }
    expect(form).to_have_count(0)
    line = row(cf, name)
    expect(line).to_contain_text("QA Autotest ИНН")
    expect(line).to_contain_text("Текст")
    assert any(d["field_name"] == name and d["is_required"] for d in definitions(cf))


# Кейс: Создание поля-списка с вариантами
@pytest.mark.writes_data
def test_create_select_field_with_options(cf: Page):
    name = sysname("sel")
    form = open_new(cf)
    form.get_by_placeholder("Например: ИНН").fill("QA Autotest Источник")
    form.get_by_placeholder("inn", exact=True).fill(name)
    pick(cf, form, "Текст", "Выбор из списка")
    for i, (label, value) in enumerate([("Сайт", "site"), ("Звонок", "call")]):
        form.get_by_role("button", name="Добавить вариант").click()
        form.get_by_placeholder("Название", exact=True).nth(i).fill(label)
        form.get_by_placeholder("value", exact=True).nth(i).fill(value)

    resp = submit(cf, form)

    assert resp.status == 201, resp.text()
    assert [(o["label"], o["value"]) for o in resp.request.post_data_json["options"]] == [("Сайт", "site"), ("Звонок", "call")]
    expect(row(cf, name)).to_contain_text("Выбор из списка")
    saved = next(d for d in definitions(cf) if d["field_name"] == name)
    assert saved["field_type"] == "select" and [o["value"] for o in saved["options"]] == ["site", "call"]


# Кейс: Список без вариантов не сохраняется
def test_select_without_options_blocked(cf: Page):
    posts = []
    cf.on("request", lambda r: posts.append(r.url) if r.method == "POST" and API in r.url else None)
    form = open_new(cf)
    form.get_by_placeholder("Например: ИНН").fill("QA Autotest пустой список")
    form.get_by_placeholder("inn", exact=True).fill(sysname("sel"))
    pick(cf, form, "Текст", "Выбор из списка")

    form.get_by_role("button", name="Создать").click()

    expect(form.get_by_text("Добавьте хотя бы один вариант")).to_be_visible()
    assert posts == []


# Кейс: Пустой вариант списка не принимается
def test_select_with_blank_option_rejected(cf: Page):
    """Пустую строку варианта фронт не считает вариантом (до ecom-100159 пропускал, отказывал сервер)."""
    posts = []
    cf.on("request", lambda r: posts.append(r.url) if r.method == "POST" and API in r.url else None)
    name = sysname("sel")
    form = open_new(cf)
    form.get_by_placeholder("Например: ИНН").fill("QA Autotest пустой вариант")
    form.get_by_placeholder("inn", exact=True).fill(name)
    pick(cf, form, "Текст", "Выбор из списка")
    form.get_by_role("button", name="Добавить вариант").click()

    form.get_by_role("button", name="Создать").click()

    expect(form.get_by_text("Добавьте хотя бы один вариант")).to_be_visible()
    assert posts == []
    assert all(d["field_name"] != name for d in definitions(cf))


# Кейс: Повторное системное имя не принимается
@pytest.mark.writes_data
def test_duplicate_sysname_rejected(cf: Page):
    existing = create_field(cf)
    reload(cf)
    form = open_new(cf)
    form.get_by_placeholder("Например: ИНН").fill("QA Autotest дубль")
    form.get_by_placeholder("inn", exact=True).fill(existing["field_name"])

    resp = submit(cf, form)

    assert resp.status == 409
    expect(form.get_by_text(resp.json()["detail"])).to_be_visible()
    assert [d["id"] for d in definitions(cf) if d["field_name"] == existing["field_name"]] == [existing["id"]]


# Кейс: Системное имя — только латиница
def test_sysname_only_latin(cf: Page):
    form = open_new(cf)
    form.get_by_placeholder("Например: ИНН").fill("QA Autotest поле")
    auto = form.get_by_placeholder("inn", exact=True)
    expect(auto).to_have_value(re.compile(r"^qa_autotest"))

    auto.fill("")
    auto.press_sequentially("Поле с пробелом-1")

    assert re.fullmatch(r"[a-z0-9_]*", auto.input_value()), auto.input_value()
    form.get_by_role("button", name="Отмена").click()


# Кейс: Скрыть и снова показать поле
@pytest.mark.writes_data
def test_hide_and_show_field(cf: Page):
    item = create_field(cf)
    reload(cf)
    line = row(cf, item["field_name"])

    with cf.expect_response(lambda r: r.url.endswith(f"{API}{item['id']}") and r.request.method == "PUT") as hidden:
        line.get_by_role("button", name="Скрыть").click()
    assert hidden.value.request.post_data_json == {"is_visible": False}
    expect(line.get_by_role("button", name="Показать")).to_be_visible()

    with cf.expect_response(lambda r: r.url.endswith(f"{API}{item['id']}") and r.request.method == "PUT") as shown:
        line.get_by_role("button", name="Показать").click()
    assert shown.value.request.post_data_json == {"is_visible": True}
    assert next(d for d in definitions(cf) if d["id"] == item["id"])["is_visible"] is True


# Кейс: Изменение поля
@pytest.mark.writes_data
def test_edit_field(cf: Page):
    item = create_field(cf)
    reload(cf)

    row(cf, item["field_name"]).get_by_role("button", name="Редактировать").click()
    form = modal(cf, "Редактировать поле")
    expect(form.get_by_placeholder("Например: ИНН")).to_have_value(item["field_label"])
    expect(form.get_by_placeholder("inn", exact=True)).not_to_be_editable()
    form.get_by_placeholder("Например: ИНН").fill("QA Autotest поле изм.")
    resp = submit(cf, form, "PUT")

    assert resp.ok, resp.text()
    saved = next(d for d in definitions(cf) if d["id"] == item["id"])
    assert (saved["field_label"], saved["field_name"]) == ("QA Autotest поле изм.", item["field_name"])
    expect(row(cf, item["field_name"])).to_contain_text("QA Autotest поле изм.")


# Кейс: Удаление поля
@pytest.mark.writes_data
def test_delete_field(cf: Page):
    item = create_field(cf)
    reload(cf)

    row(cf, item["field_name"]).get_by_role("button", name="Удалить").click()
    dialog = cf.get_by_role("dialog", name=f'Удалить поле "{item["field_label"]}"? Все значения будут потеряны.')
    with cf.expect_response(lambda r: f"{API}{item['id']}" in r.url and r.request.method == "DELETE") as deleted:
        dialog.get_by_role("button", name="Да").click()

    assert deleted.value.ok
    expect(row(cf, item["field_name"])).to_have_count(0)
    assert all(d["id"] != item["id"] for d in definitions(cf))


# Кейс: Удаление поля можно отменить
@pytest.mark.writes_data
def test_delete_can_be_cancelled(cf: Page):
    item = create_field(cf)
    reload(cf)

    row(cf, item["field_name"]).get_by_role("button", name="Удалить").click()
    cf.get_by_role("dialog", name=re.compile("^Удалить поле")).get_by_role("button", name="Отмена").click()

    expect(row(cf, item["field_name"])).to_have_count(1)
    assert any(d["id"] == item["id"] for d in definitions(cf))


# Кейс: Поле видно только у своей сущности
@pytest.mark.writes_data
def test_field_belongs_to_its_entity(cf: Page):
    lead = create_field(cf, entity="lead")
    contact = create_field(cf, entity="contact")
    reload(cf)
    expect(row(cf, lead["field_name"])).to_have_count(1)
    expect(row(cf, contact["field_name"])).to_have_count(0)

    with cf.expect_response(lambda r: "entity_type=contact" in r.url):
        pick(cf, cf.locator("main"), "Лид", "Контакт")

    expect(row(cf, contact["field_name"])).to_have_count(1)
    expect(row(cf, lead["field_name"])).to_have_count(0)
