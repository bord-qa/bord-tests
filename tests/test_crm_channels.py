"""Блок 37. CRM — каналы (плагин «Каналы»): /admin/crm/channels и /admin/crm/inbox.

Снято в MCP-браузере 30.10.2026:
- Telegram, WhatsApp, Facebook, Instagram, Viber и 999.md требуют внешних токенов.
  «Чат на сайте» (webchat) — нет, поэтому переписку гоняем настоящую: посетитель
  пишет через публичные ручки виджета, оператор отвечает на экране;
- канал: POST /channels/settings/ {name, platform: webchat} → settings.site_key.
  Виджет: GET /public/webchat/<key>/config, POST …/session {name} → visitor_token,
  POST …/messages {visitor_token, text}, GET …/messages?visitor_token=&after_id=0;
- диалог заводится первым сообщением. «Все переписки»: вкладки Все / Открытые /
  Закрытые / Архив, «Без ответа», поиск, диалог открывается ?conv=<id> и становится
  прочитанным. Счётчики вкладок обновляются опросом, с задержкой. Выбор контакта на
  правой панели заводит сделку и привязывает её к диалогу;
- ответ: «Введите сообщение...» + «Отправить» → POST …/messages; «Закрыть» →
  «Диалог закрыт. Откройте заново…», на панели справа «Открыть» и «В архив»;
- у ответа оператора в webchat статус «не доставлено», хотя посетитель его получает —
  bord-20.

Плагин включается на время блока. Канал создаётся на блок и удаляется после него.
"""
import random
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.crm import CRM, QA_PREFIX, crm_delete, crm_get, crm_post, open_crm, plugins_on, sidebar

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/channels"
PUBLIC = f"{CRM}/api/crm/public/webchat"


@pytest.fixture(scope="module", autouse=True)
def channels_plugin(browser, auth_state, browser_context_args):
    yield from plugins_on(browser, auth_state, browser_context_args, "channels")


@pytest.fixture(scope="module")
def channel(browser, auth_state, browser_context_args, channels_plugin):
    ctx = browser.new_context(**browser_context_args, storage_state=auth_state)
    page = ctx.new_page()
    created = crm_post(page, "/channels/settings/", {"name": f"{QA_PREFIX} чат {tag()}", "platform": "webchat"})
    assert created.ok, created.text()
    data = created.json()
    yield {**data, "key": data["settings"]["site_key"]}
    page._crm_sso = None
    crm_delete(page, f"/channels/settings/{data['id']}")
    ctx.close()


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


class Visitor:
    """Посетитель сайта: только публичные ручки виджета, без токена CRM."""

    def __init__(self, page: Page, channel: dict):
        self.page, self.key = page, channel["key"]
        self.name = f"{QA_PREFIX} посетитель {tag()}"
        resp = page.request.post(f"{PUBLIC}/{self.key}/session", data={"name": self.name, "page_url": "https://example.com/"})
        assert resp.ok, resp.text()
        self.token = resp.json()["visitor_token"]

    def say(self, text: str) -> dict:
        resp = self.page.request.post(f"{PUBLIC}/{self.key}/messages", data={
            "visitor_token": self.token, "text": text, "page_url": "https://example.com/"})
        assert resp.ok, resp.text()
        return resp.json()

    def inbox(self) -> list:
        resp = self.page.request.get(f"{PUBLIC}/{self.key}/messages",
                                     params={"visitor_token": self.token, "after_id": 0})
        assert resp.ok, resp.text()
        return resp.json()["messages"]


def conversation(page: Page, dialog_id: int) -> dict:
    return crm_get(page, f"/channels/conversations/{dialog_id}").json()


@pytest.fixture
def chat(auth_page: Page, channel: dict):
    open_crm(auth_page, PAGE)
    yield auth_page
    for t in crm_get(auth_page, "/channels/templates/").json():
        if t["name"].startswith(QA_PREFIX):
            crm_delete(auth_page, f"/channels/templates/{t['id']}")


def dialog(page: Page, name: str):
    return page.locator("main button").filter(has_text=name).first


def open_dialog(page: Page, dialog_id: int):
    page.goto(f"{CRM}{PAGE}?conv={dialog_id}")
    expect(page.get_by_placeholder("Поиск по имени или сообщению...")).to_be_visible(timeout=20_000)


def composer(page: Page):
    return page.get_by_placeholder("Введите сообщение...")


def send_reply(page: Page, dialog_id: int, text: str):
    expect(composer(page)).to_be_visible(timeout=20_000)
    composer(page).fill(text)
    with page.expect_response(lambda r: f"/conversations/{dialog_id}/messages" in r.url
                              and r.request.method == "POST") as resp:
        page.locator("main button[title='Отправить'], main button[aria-label='Отправить']").click()
    assert resp.value.ok, resp.value.text()


# Кейс: Плагин включён — в меню «Входящие» и «Все переписки»
def test_menu_items(chat: Page):
    menu = sidebar(chat)
    expect(menu.get_by_role("link", name="Входящие", exact=True)).to_be_visible()
    expect(menu.get_by_role("link", name="Все переписки", exact=True)).to_be_visible()


# Кейс: Настройки виджета — название канала и приветствие; чужой ключ отбивается
def test_widget_config(chat: Page, channel: dict):
    config = chat.request.get(f"{PUBLIC}/{channel['key']}/config")
    assert config.ok, config.text()
    assert config.json()["title"] == channel["name"]
    assert config.json()["greeting"]

    assert chat.request.get(f"{PUBLIC}/{channel['key']}/widget.js").ok
    assert chat.request.get(f"{PUBLIC}/no-such-key/config").status == 404


# Кейс: Открытие окна чата диалог не заводит — только первое сообщение
def test_dialog_starts_with_first_message(chat: Page, channel: dict):
    name = f"{QA_PREFIX} посетитель {tag()}"

    opened = chat.request.post(f"{PUBLIC}/{channel['key']}/session", data={"name": name, "lazy": True})

    assert opened.ok, opened.text()
    assert opened.json()["visitor_token"] is None
    assert crm_get(chat, "/channels/conversations/", {"search": name}).json()["total"] == 0

    visitor = Visitor(chat, channel)
    sent = visitor.say(f"{QA_PREFIX} {tag()}")
    assert conversation(chat, sent["dialog_id"])["contact_name"] == visitor.name


# Кейс: Сообщение посетителя видно в «Все переписки» как непрочитанное
def test_message_appears_unread(chat: Page, channel: dict):
    visitor = Visitor(chat, channel)
    text = f"{QA_PREFIX} {tag()}"
    sent = visitor.say(text)

    chat.reload()
    line = dialog(chat, visitor.name)
    expect(line).to_contain_text(text, timeout=20_000)
    expect(line).to_contain_text("Чат на сайте")
    saved = conversation(chat, sent["dialog_id"])
    assert (saved["status"], saved["is_replied"], saved["unread_count"]) == ("open", False, 1)


# Кейс: Открытие диалога сбрасывает непрочитанное
def test_open_marks_read(chat: Page, channel: dict):
    visitor = Visitor(chat, channel)
    sent = visitor.say(f"{QA_PREFIX} {tag()}")

    chat.reload()
    dialog(chat, visitor.name).click()

    expect(chat).to_have_url(f"{CRM}{PAGE}?conv={sent['dialog_id']}")
    expect(composer(chat)).to_be_visible()
    chat.wait_for_timeout(1_000)
    assert conversation(chat, sent["dialog_id"])["unread_count"] == 0


# Кейс: Ответ оператора доходит до посетителя
def test_reply_reaches_visitor(chat: Page, channel: dict):
    visitor = Visitor(chat, channel)
    sent = visitor.say(f"{QA_PREFIX} {tag()}")
    reply = f"{QA_PREFIX} ответ {tag()}"
    open_dialog(chat, sent["dialog_id"])

    send_reply(chat, sent["dialog_id"], reply)

    expect(chat.locator("main").get_by_text(reply).first).to_be_visible()
    outgoing = [m["text"] for m in visitor.inbox() if not m["is_incoming"]]
    assert reply in outgoing
    saved = conversation(chat, sent["dialog_id"])
    assert (saved["is_replied"], saved["last_message_text"]) == (True, reply)


# Кейс: Дошедший ответ не помечается «не доставлено»
def test_reply_not_marked_failed(chat: Page, channel: dict):
    visitor = Visitor(chat, channel)
    sent = visitor.say(f"{QA_PREFIX} {tag()}")
    open_dialog(chat, sent["dialog_id"])

    send_reply(chat, sent["dialog_id"], f"{QA_PREFIX} ответ {tag()}")

    chat.wait_for_timeout(1_500)
    expect(chat.locator("main").get_by_text("не доставлено")).to_have_count(0, timeout=1_000)


# Кейс: Закрыть и открыть диалог заново
def test_close_and_reopen(chat: Page, channel: dict):
    visitor = Visitor(chat, channel)
    sent = visitor.say(f"{QA_PREFIX} {tag()}")
    open_dialog(chat, sent["dialog_id"])
    expect(composer(chat)).to_be_visible(timeout=20_000)

    with chat.expect_response(lambda r: f"/conversations/{sent['dialog_id']}/close" in r.url) as resp:
        chat.locator("main").get_by_role("button", name="Закрыть", exact=True).first.click()
    assert resp.value.ok, resp.value.text()
    expect(chat.get_by_text("Диалог закрыт. Откройте заново")).to_be_visible()
    expect(composer(chat)).to_have_count(0)
    assert conversation(chat, sent["dialog_id"])["status"] == "closed"

    with chat.expect_response(lambda r: f"/conversations/{sent['dialog_id']}/reopen" in r.url) as resp:
        chat.locator("main").get_by_role("button", name="Открыть", exact=True).click()
    assert resp.value.ok, resp.value.text()
    expect(composer(chat)).to_be_visible()
    assert conversation(chat, sent["dialog_id"])["status"] == "open"


# Кейс: Закрытый диалог уходит в архив и виден на вкладке «Архив»
def test_archive_dialog(chat: Page, channel: dict):
    visitor = Visitor(chat, channel)
    sent = visitor.say(f"{QA_PREFIX} {tag()}")
    assert crm_post(chat, f"/channels/conversations/{sent['dialog_id']}/close", {}).ok
    open_dialog(chat, sent["dialog_id"])

    with chat.expect_response(lambda r: f"/conversations/{sent['dialog_id']}/archive" in r.url) as resp:
        chat.locator("main").get_by_role("button", name="В архив").click()
    assert resp.value.ok, resp.value.text()

    assert conversation(chat, sent["dialog_id"])["status"] == "archived"
    chat.goto(CRM + PAGE)
    chat.locator("main").get_by_role("button", name=re.compile("^Архив")).click()
    expect(dialog(chat, visitor.name)).to_be_visible(timeout=20_000)


# Кейс: Вкладки и «Без ответа» разделяют диалоги
def test_tabs_filter(chat: Page, channel: dict):
    waiting, closed = Visitor(chat, channel), Visitor(chat, channel)
    waiting.say(f"{QA_PREFIX} {tag()}")
    done = closed.say(f"{QA_PREFIX} {tag()}")
    assert crm_post(chat, f"/channels/conversations/{done['dialog_id']}/close", {}).ok
    chat.reload()
    main = chat.locator("main")

    main.get_by_role("button", name=re.compile("^Закрытые")).click()
    expect(dialog(chat, closed.name)).to_be_visible(timeout=20_000)
    expect(main.get_by_text(waiting.name)).to_have_count(0)

    main.get_by_role("button", name=re.compile("^Открытые")).click()
    expect(dialog(chat, waiting.name)).to_be_visible(timeout=20_000)
    expect(main.get_by_text(closed.name)).to_have_count(0)


# Кейс: Поиск по тексту сообщения находит диалог
def test_search(chat: Page, channel: dict):
    target, other = Visitor(chat, channel), Visitor(chat, channel)
    mark = tag()
    target.say(f"{QA_PREFIX} поиск {mark}")
    other.say(f"{QA_PREFIX} {tag()}")
    chat.reload()
    expect(dialog(chat, target.name)).to_be_visible(timeout=20_000)

    chat.get_by_placeholder("Поиск по имени или сообщению...").fill(mark)

    expect(dialog(chat, target.name)).to_be_visible()
    expect(chat.locator("main").get_by_text(other.name)).to_have_count(0)


# Кейс: Шаблон ответа: без названия не создаётся, виден в API, удаляется
def test_reply_template(chat: Page):
    refused = crm_post(chat, "/channels/templates/", {"name": "", "text": ""})
    assert refused.status == 400, refused.text()
    name = f"{QA_PREFIX} шаблон {tag()}"

    created = crm_post(chat, "/channels/templates/", {"name": name, "text": "Здравствуйте!", "shortcut": f"/{tag()}"})

    assert created.ok, created.text()
    assert name in [t["name"] for t in crm_get(chat, "/channels/templates/").json()]
    assert crm_delete(chat, f"/channels/templates/{created.json()['id']}").ok
    assert name not in [t["name"] for t in crm_get(chat, "/channels/templates/").json()]


# Кейс: Пустое сообщение и чужой токен посетителя не проходят
def test_public_messages_validation(chat: Page, channel: dict):
    visitor = Visitor(chat, channel)
    empty = chat.request.post(f"{PUBLIC}/{channel['key']}/messages", data={"visitor_token": visitor.token, "text": ""})
    foreign = chat.request.post(f"{PUBLIC}/{channel['key']}/messages",
                                data={"visitor_token": "1.deadbeef", "text": "QA"})

    assert empty.status >= 400, f"пустое сообщение принято: {empty.status}"
    assert foreign.status in (401, 403, 404), f"чужой токен принят: {foreign.status} {foreign.text()[:200]}"


# Кейс: Выключенный канал не принимает посетителей
def test_inactive_channel(chat: Page):
    created = crm_post(chat, "/channels/settings/", {"name": f"{QA_PREFIX} чат {tag()}", "platform": "webchat"})
    assert created.ok, created.text()
    data = created.json()
    try:
        assert crm_post(chat, f"/channels/settings/{data['id']}/deactivate", {}).ok
        key = data["settings"]["site_key"]
        assert chat.request.post(f"{PUBLIC}/{key}/session", data={"name": "QA"}).status >= 400
    finally:
        crm_delete(chat, f"/channels/settings/{data['id']}")


# Кейс: «Входящие» показывают новое сообщение и фильтр непрочитанных
def test_inbox_shows_message(chat: Page, channel: dict):
    visitor = Visitor(chat, channel)
    visitor.say(f"{QA_PREFIX} {tag()}")

    chat.goto(f"{CRM}/admin/crm/inbox")

    expect(chat.locator("main").get_by_text(visitor.name).first).to_be_visible(timeout=20_000)
    assert crm_get(chat, "/inbox/unread-summary/").json()["messages_unread"] >= 1


# Кейс: Контакт из правой панели — создаётся сделка и привязывается к диалогу
def test_deal_from_dialog(chat: Page, channel: dict):
    person = crm_post(chat, "/contacts/", {"first_name": f"{QA_PREFIX} {tag()}",
                                           "phone": "+3736" + "".join(random.choices("0123456789", k=7))}).json()
    visitor = Visitor(chat, channel)
    sent = visitor.say(f"{QA_PREFIX} {tag()}")
    deal_id = None
    try:
        open_dialog(chat, sent["dialog_id"])
        chat.get_by_placeholder("Найти контакт (имя / телефон)…").press_sequentially(person["first_name"])
        chat.locator("main button", has_text=person["first_name"]).first.click()

        expect(chat.get_by_role("button", name="Отвязать")).to_be_visible()
        deal_id = conversation(chat, sent["dialog_id"])["deal_id"]
        assert deal_id, "диалог остался без сделки"
        expect(chat.locator("main").get_by_text(f"Сделка #{deal_id}")).to_be_visible()
        assert crm_get(chat, f"/deals/{deal_id}").json()["contact_id"] == person["id"]
    finally:
        if deal_id:
            crm_delete(chat, f"/deals/{deal_id}")
        crm_post(chat, "/bulk/contacts/delete", {"contact_ids": [person["id"]]})
