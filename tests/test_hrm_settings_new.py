"""Блок 75. HRM — новые вкладки «Настроек» (появились после 03.11.2026): «Обязательные поля» и «Telegram-бот».

Снято 25.11.2026 в видимом MCP-браузере:
- «Обязательные поля»: «Без этих полей карточку сотрудника не создать.» — галочки по GET
  /employees/required-fields {required, fields:[{key,label,required,locked}]}; «Имя» отмечено и
  заблокировано (locked); клик → PUT /employees/required-fields {fields:[...ключи]}; после этого форма
  «Добавить сотрудника» пишет «<поле> *», а POST /employees без поля → 422 «Не заполнено: <поле>».
  Ниже на той же вкладке: «Не увольнять, пока за сотрудником числятся ТМЦ» (/employees/dismiss-settings
  {block_dismiss_with_tmc, can_force}) и «Хранить файлы сотрудников на Диске BORD» (без модуля
  «Документы» — заблокировано, «Модуль «Документы» не подключён — файлы хранятся в HRM.»);
- «Telegram-бот»: описание (бот пишет в рабочий чат об отпусках/больничных/отгулах и решениях по ним),
  «Бот: @<bot>. Команда /today…» = GET /telegram-bot/status {configured, bot_username, chats, policies};
  без чатов — «Чаты ещё не подключены»; «Подключить чат» → POST /telegram-bot/bind-code {code,
  deep_link, group_link, expires_in: 900} → ссылки «В рабочую группу» (startgroup) / «Себе в личку»
  (start), «Ссылка действует 15 минут», запасная команда «/start@<bot> <code>». Сам чат к Telegram тест
  не привязывает (нужен живой аккаунт Telegram) — код живёт 15 минут и пропадает сам.

Обязательные поля — настройка всей организации: пока включена лишняя, создание сотрудников без неё
(в т. ч. другими тестами) падает. Возвращаем в любом исходе; параллельно с другими HRM-тестами не гонять.
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (QA_PREFIX, clean_up, create_legal_entity, header_button, hrm_delete, hrm_get, hrm_json,
                         hrm_post, hrm_put, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth


def main(page: Page):
    return page.locator("main")


def open_tab(page: Page, tab: str, api: str):
    open_hrm(page, "/admin/settings")
    with page.expect_response(lambda r: f"/api/hrm{api}" in r.url and r.request.method == "GET") as resp:
        main(page).get_by_role("button", name=tab, exact=True).first.click()
    assert resp.value.ok, f"{api} → {resp.value.status}"
    return resp.value.json()


def checkbox(page: Page, label: str):
    return main(page).locator("label").filter(has_text=re.compile(rf"^\s*{re.escape(label)}")).locator(
        "input[type=checkbox]").first


@pytest.fixture
def required_back(auth_page: Page):
    before = hrm_json(hrm_get(auth_page, "/employees/required-fields"))["required"]
    yield before
    hrm_put(auth_page, "/employees/required-fields", {"fields": before})
    clean_up(auth_page)


# ───────────────────────── обязательные поля ─────────────────────────

# Кейс: «Обязательные поля» = API (подписи, отметки, «Имя» заблокировано); «Фамилия» → PUT → в форме
# «Фамилия *», без фамилии сотрудник не создаётся (422) → вернуть как было
@pytest.mark.writes_data
def test_required_fields(auth_page: Page, required_back):
    data = open_tab(auth_page, "Обязательные поля", "/employees/required-fields")
    expect(main(auth_page)).to_contain_text("Без этих полей карточку сотрудника не создать.")
    for f in data["fields"]:
        cb = checkbox(auth_page, f["label"])
        expect(cb, f["label"]).to_be_checked(checked=f["required"])
        (expect(cb, f["label"]).to_be_disabled if f["locked"] else expect(cb, f["label"]).to_be_enabled)()
    assert [f["key"] for f in data["fields"] if f["locked"]] == ["telegram_first_name"], data["fields"]
    if "last_name" in required_back:
        pytest.skip("«Фамилия» уже обязательна у организации — проверять нечего")

    with auth_page.expect_response(lambda r: "/employees/required-fields" in r.url and r.request.method == "PUT") as resp:
        checkbox(auth_page, "Фамилия").click()
    assert resp.value.ok, resp.value.text()[:200]
    assert sorted(resp.value.request.post_data_json["fields"]) == sorted(required_back + ["last_name"])
    expect(checkbox(auth_page, "Фамилия")).to_be_checked()

    open_hrm(auth_page, "/admin/employees")
    header_button(auth_page, "Добавить").click()
    form = auth_page.locator("div.fixed.inset-0:visible").filter(
        has=auth_page.get_by_text("Добавить сотрудника", exact=True)).last
    expect(form.get_by_text("Фамилия *", exact=True)).to_be_visible()
    expect(form.get_by_text("Отчество", exact=True)).to_be_visible()  # необязательное — без звёздочки
    auth_page.keyboard.press("Escape")

    le = create_legal_entity(auth_page)
    try:
        resp = hrm_post(auth_page, "/employees", {"telegram_first_name": f"{QA_PREFIX} Обяз {stamp()}",
                                                  "legal_entity_id": le["id"]})
        assert resp.status == 422, f"без обязательной фамилии: {resp.status} {resp.text()[:200]}"
        assert "Фамилия" in resp.json()["detail"], resp.text()[:200]
    finally:
        hrm_delete(auth_page, f"/legal-entities/{le['id']}")


# Кейс: «Не увольнять, пока за сотрудником числятся ТМЦ» и «Хранить файлы на Диске BORD» = API;
# переключение запрета увольнения сохраняется сразу → вернуть
@pytest.mark.writes_data
def test_dismiss_and_files_settings(auth_page: Page):
    open_tab(auth_page, "Обязательные поля", "/employees/required-fields")
    dismiss = hrm_json(hrm_get(auth_page, "/employees/dismiss-settings"))
    files = hrm_json(hrm_get(auth_page, "/employees/files-settings"))
    block = checkbox(auth_page, "Не увольнять, пока за сотрудником числятся ТМЦ")
    drive = checkbox(auth_page, "Хранить файлы сотрудников на Диске BORD")
    expect(block).to_be_checked(checked=dismiss["block_dismiss_with_tmc"])
    expect(drive).to_be_checked(checked=files["store_on_disk"])
    if not files["dms_enabled"]:  # без модуля «Документы» галочка заблокирована и объяснена
        expect(drive).to_be_disabled()
        expect(main(auth_page)).to_contain_text("Модуль «Документы» не подключён — файлы хранятся в HRM.")
    else:
        expect(drive).to_be_enabled()

    was = dismiss["block_dismiss_with_tmc"]
    try:
        with auth_page.expect_response(lambda r: "/employees/dismiss-settings" in r.url and r.request.method == "PUT") as resp:
            block.click()
        assert resp.value.ok, resp.value.text()[:200]
        expect(block).to_be_checked(checked=not was)
        assert hrm_json(hrm_get(auth_page, "/employees/dismiss-settings"))["block_dismiss_with_tmc"] is (not was)
    finally:
        hrm_put(auth_page, "/employees/dismiss-settings", {"block_dismiss_with_tmc": was})


# ───────────────────────── Telegram-бот ─────────────────────────

# Кейс: «Telegram-бот» = /telegram-bot/status (бот, чаты); «Подключить чат» → код привязки: ссылки
# «В рабочую группу» / «Себе в личку» и команда /start совпадают с ответом, срок — 15 минут
def test_telegram_bot_bind_code(auth_page: Page):
    status = open_tab(auth_page, "Telegram-бот", "/telegram-bot/status")
    assert status["configured"] and status["bot_username"], status
    bot = status["bot_username"]
    expect(main(auth_page)).to_contain_text(f"Бот: @{bot}.")
    expect(main(auth_page)).to_contain_text("/today")
    if not status["chats"]:
        expect(main(auth_page).get_by_text("Чаты ещё не подключены")).to_be_visible()

    with auth_page.expect_response(lambda r: "/telegram-bot/bind-code" in r.url) as resp:
        main(auth_page).get_by_role("button", name="Подключить чат").click()
    assert resp.value.ok, resp.value.text()[:200]
    code = resp.value.json()
    assert code["code"] and code["expires_in"] == 900, code
    assert code["group_link"] == f"https://t.me/{bot}?startgroup={code['code']}", code
    assert code["deep_link"] == f"https://t.me/{bot}?start={code['code']}", code
    group = main(auth_page).get_by_role("link", name="В рабочую группу")
    private = main(auth_page).get_by_role("link", name="Себе в личку")
    expect(group).to_have_attribute("href", code["group_link"])
    expect(private).to_have_attribute("href", code["deep_link"])
    expect(group).to_have_attribute("target", "_blank")
    expect(main(auth_page)).to_contain_text("Ссылка действует 15 минут")
    expect(main(auth_page)).to_contain_text(f"/start@{bot} {code['code']}")
