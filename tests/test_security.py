"""Блок 10. Реестр доступов: app.bord.one/admin/security.

Снято 22.10.2026:
- API: /api/security/meta (people, categories, criticalities, levels, me),
  /api/security/summary (счётчики рисков над вкладками), /api/security/systems
  (?archived=true — архив), /api/security/grants?state=active|revoked|all;
- «Системы» → «Добавить систему»: Название, Категория, Критичность,
  Ответственный, Адрес, Где лежит доступ, Примечание → POST …/systems → 201;
  пустое название — «Название обязательно»; в строке: «Изменить» (PATCH),
  «В архив» (PATCH {archived: true}, без подтверждения), «Удалить (только если
  доступы к ней не выдавали)» → окно «Удалить «…»?» → DELETE; с выданными
  доступами сервер отвечает 409 «…её можно только отправить в архив»;
- «Доступы» → «Выдать доступ»: Система, У кого (сотрудник / подрядчик),
  Уровень, 2FA (null / true / false), Учётная запись, Кто выдал, Когда выдан, Действует до,
  Примечание → POST …/grants → 201; «Отозван» → окно «Отметить, что доступ … »
  с полем «Что сделано» → POST …/grants/<id>/revoke;
- встроенная система «BORD» (builtin) ведётся сама: её доступы — участники
  организации, тесты её не трогают.

Снято 27.10.2026 (вкладки открываются и по адресу ?tab=offboardings|rotations):
- «Увольнения» (Идут / Закрытые → ?state=open|done): «Начать увольнение» →
  окно: Сотрудник (по умолчанию «— не сотрудник BORD (подрядчик) —»), Имя
  («как записан в реестре»), Причина → POST …/offboardings → 201. Карточка:
  «Живые доступы: N» с кнопкой «Отозван» у каждого (сразу, без окна), задачи
  «Увольнение: <имя>» (протокол, 8 пунктов, себе) и «Отозвать доступы: <имя>»
  (владельцу системы, пункт на доступ — галочка ставится сама при отзыве),
  «Закрыть увольнение» → POST …/<id>/complete (задачи остаются открытыми).
  Удаления увольнений в API нет — закрытые копятся в «Закрытых».
  «Заблокировать везде» (lockout: Google, сейф, учётка BORD) тесты НЕ нажимают;
- «Смена паролей»: «Общие логины» — логин, по которому больше одного
  действующего доступа; ушёл один из них — логин в «Пароли, которые надо
  сменить» с «Новый пароль сообщить: <остальные>»; «Сменил» → окно «Пароль
  сменён» с комментарием → POST …/rotations/done {kind: login, system_id,
  login, note} — закрывает только этот логин. ecom-100215: комментарий вида
  «пароль: …» принимается (в инцидентах — 400).

Ответственный и владелец доступа в тестах — только тест-учётка; уволить
можно только подрядчика (живых сотрудников BORD QA не трогаем). Уборка:
пустые системы «QA Autotest …» удаляются, у систем с доступами доступы
отзываются, а сама система уходит в архив (удалить её API не даёт); задачи,
которые поставило увольнение подрядчика «QA Autotest …», удаляются.
"""
import datetime as dt
import re
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_delete, api_get, api_patch, api_post
from helpers.modal import modal
from helpers.wait import settle

pytestmark = pytest.mark.requires_auth

API = "/api/security/"
NAME_PREFIX = "QA Autotest"
COUNTERS = {
    "departed_holders": "Доступ у ушедших",
    "expired": "Истёк, не отозван",
    "no_mfa_critical": "Без 2FA в критичных",
    "systems_without_owner": "Систем без ответственного",
    "active_grants": "Активных доступов",
    "systems": "Систем",
}
TAB_API = {
    "Сотрудники": "people",
    "По должностям": "position-access",
    "Увольнения": "offboardings",
    "Смена паролей": "rotations",
    "Входы в BORD": "sessions",
    "Интеграции": "integrations",
}


def name() -> str:
    return f"{NAME_PREFIX} система {int(time.time() * 1000)}"


def me(page: Page) -> int:
    return api_get(page, f"{API}meta").json()["me"]


def my_name(page: Page) -> str:
    meta = api_get(page, f"{API}meta").json()
    return next(p["name"] for p in meta["people"] if p["id"] == meta["me"])


def summary(page: Page) -> dict:
    return api_get(page, f"{API}summary").json()


def create_system(page: Page, **fields) -> dict:
    resp = api_post(page, f"{API}systems", {"name": name(), "category": "saas", "criticality": "normal", **fields})
    assert resp.status == 201, resp.text()
    return resp.json()


def create_grant(page: Page, system: dict, **fields) -> dict:
    data = {"system_id": system["id"], "holder_id": me(page), "level": "write", "login": "qa-login@example.com", **fields}
    resp = api_post(page, f"{API}grants", data)
    assert resp.status == 201, resp.text()
    return resp.json()


def grants_of(page: Page, system_id: int, state: str = "all") -> list:
    return [g for g in api_get(page, f"{API}grants?state={state}").json()["items"] if g["system"]["id"] == system_id]


def cleanup(page: Page):
    # Архивные «QA Autotest …» не трогаем: туда попадают только системы, которым
    # выдавали доступы, а такие API не удаляет никогда (409). Раньше уборка после
    # КАЖДОГО теста делала по ним DELETE + GET всех доступов — к 08.11 в архиве
    # накопилось 218 систем, ~440 запросов на тест, и блок упирался в таймаут.
    resp = api_get(page, f"{API}systems?archived=false")
    systems = [s for s in (resp.json()["items"] if resp.ok else []) if s["name"].startswith(NAME_PREFIX)]
    resp = api_get(page, f"{API}grants?state=active")
    active = resp.json()["items"] if resp.ok else []
    for system in systems:
        if api_delete(page, f"{API}systems/{system['id']}").status == 409:
            for grant in [g for g in active if g["system"]["id"] == system["id"]]:
                api_post(page, f"{API}grants/{grant['id']}/revoke", {"note": "уборка автотеста"})
            api_patch(page, f"{API}systems/{system['id']}", {"archived": True})
    # тест мог отправить систему в архив раньше, чем отозвал её доступы
    for grant in active:
        if grant["system"]["name"].startswith(NAME_PREFIX) and grant["system"]["id"] not in {s["id"] for s in systems}:
            api_post(page, f"{API}grants/{grant['id']}/revoke", {"note": "уборка автотеста"})
    # «Увольнение: QA Autotest …», «Отозвать доступы: QA Autotest …»
    resp = api_get(page, "/api/tasks/?status=all&limit=100")
    for task in resp.json()["items"] if resp.ok else []:
        if f": {NAME_PREFIX}" in task["title"]:
            api_delete(page, f"/api/tasks/{task['id']}")


@pytest.fixture
def security(auth_page: Page):
    auth_page.goto("/admin/security")
    expect(auth_page.locator("main table")).to_be_visible()
    settle(auth_page)
    yield auth_page
    cleanup(auth_page)


def reload(page: Page):
    page.reload()
    expect(page.locator("main table")).to_be_visible()
    settle(page)


def tab(page: Page, label: str):
    # у вкладок бывает счётчик в имени: «Смена паролей1», «Увольнения1» (без re.escape —
    # регулярка уходит в JS, а в подписях только буквы и пробелы)
    return page.locator("main").get_by_role("button", name=re.compile(rf"^{label}\d*$"))


def counter(page: Page, label: str):
    return page.locator("main button").filter(has_text=re.compile(rf"^\d+\s*{label}$"))


def rows(page: Page, text: str):
    return page.locator("main table tbody tr").filter(has_text=text)


def pick(page: Page, scope, current: str, option: str):
    scope.get_by_role("button", name=current, exact=True).click()
    page.get_by_role("option", name=option, exact=True).click()


def pick_by_label(page: Page, form, label: str, option: str):
    """Выпадашка по подписи поля: у «Системы» и «Сотрудника» одинаковое «Выберите…»."""
    form.get_by_text(label, exact=True).locator("xpath=following::button[@aria-haspopup='listbox'][1]").click()
    page.get_by_role("option", name=option, exact=True).click()


def field(form, label: str):
    return form.get_by_text(label, exact=True).locator("xpath=following-sibling::*[self::input or self::textarea][1]")


def open_systems(page: Page):
    tab(page, "Системы").click()
    expect(page.locator("main").get_by_role("button", name="Добавить систему")).to_be_visible()


# Кейс: Счётчики над списком совпадают с API
@pytest.mark.smoke
def test_counters_match_summary(security: Page):
    data = summary(security)

    for key, label in COUNTERS.items():
        expect(counter(security, label), label).to_have_text(re.compile(rf"^{data[key]}\s*{label}$"))


# Кейс: Встроенная система «BORD» показывает участников организации
def test_builtin_bord_system_lists_org_members(security: Page):
    """Встроенная «BORD» = участники организации, «авто», со ссылкой в «Команду»."""
    users = api_get(security, "/api/admin/auth/users").json()
    bord = [g for g in api_get(security, f"{API}grants?state=active").json()["items"] if g["system"]["name"] == "BORD"]

    assert sorted(g["login"] for g in bord) == sorted(u["email"] for u in users)
    for user in users:
        line = rows(security, user["email"])
        expect(line).to_contain_text("BORD")
        expect(line).to_contain_text("авто")
        # с 08.11 «Пользователи» влились в «Команду»
        expect(line.get_by_role("link", name="в «Команде»")).to_have_attribute("href", "/admin/team")


# Кейс: Добавление системы через форму
@pytest.mark.writes_data
def test_add_system_via_form(security: Page):
    before = summary(security)
    title = name()
    open_systems(security)
    security.locator("main").get_by_role("button", name="Добавить систему").click()
    form = modal(security, "Новая система")
    form.get_by_role("button", name="Сохранить").click()
    expect(form.get_by_text("Название обязательно", exact=True)).to_be_visible()

    form.get_by_placeholder("Банк MAIB, Google Workspace, хостинг DigitalOcean…").fill(title)
    pick(security, form, "Сервисы и программы", "Банк")
    pick(security, form, "Обычная", "Высокая")
    form.get_by_placeholder("https://…").fill("https://qa.example.com")
    field(form, "Где лежит доступ").fill("1Password → сейф «QA»")
    form.locator("textarea").fill("заметка автотеста")
    with security.expect_response(lambda r: r.url.endswith(f"{API}systems") and r.request.method == "POST") as created:
        form.get_by_role("button", name="Сохранить").click()

    assert created.value.status == 201, created.value.text()
    assert created.value.request.post_data_json == {
        "name": title, "category": "bank", "criticality": "high", "url": "https://qa.example.com",
        "vault_ref": "1Password → сейф «QA»", "notes": "заметка автотеста", "owner_id": None,
    }
    line = rows(security, title)
    for text in ("Банк", "Высокая", "не назначен", "1Password → сейф «QA»"):
        expect(line, text).to_contain_text(text)
    after = summary(security)
    assert after["systems"] == before["systems"] + 1
    assert after["systems_without_owner"] == before["systems_without_owner"] + 1
    expect(counter(security, "Систем без ответственного")).to_have_text(
        re.compile(rf"^{after['systems_without_owner']}\s*Систем без ответственного$"))


# Кейс: Назначение владельца закрывает риск
@pytest.mark.writes_data
def test_assign_owner_closes_risk(security: Page):
    system = create_system(security)
    before = summary(security)
    reload(security)
    open_systems(security)

    rows(security, system["name"]).get_by_role("button", name="Изменить").click()
    form = modal(security, "Изменить систему")
    pick(security, form, "Не назначен", my_name(security))
    with security.expect_response(lambda r: r.url.endswith(f"systems/{system['id']}") and r.request.method == "PATCH") as patched:
        form.get_by_role("button", name="Сохранить").click()

    assert patched.value.ok and patched.value.json()["owner"]["id"] == me(security)
    assert summary(security)["systems_without_owner"] == before["systems_without_owner"] - 1


# Кейс: Выдать доступ и отозвать
@pytest.mark.writes_data
def test_grant_access_and_revoke(security: Page):
    system = create_system(security)
    before = summary(security)
    reload(security)
    main = security.locator("main")

    main.get_by_role("button", name="Выдать доступ").click()
    form = modal(security, "Выдать доступ")
    pick_by_label(security, form, "Система", system["name"])
    holder = my_name(security)
    pick_by_label(security, form, "У кого доступ", holder)
    pick(security, form, "Работа", "Администратор")
    pick(security, form, "Не проверяли", "Включена")
    field(form, "Учётная запись").fill("qa-login@example.com")
    with security.expect_response(lambda r: r.url.endswith(f"{API}grants") and r.request.method == "POST") as created:
        form.get_by_role("button", name="Сохранить").click()

    assert created.value.status == 201, created.value.text()
    sent = created.value.request.post_data_json
    assert (sent["system_id"], sent["holder_id"], sent["level"], sent["mfa"], sent["login"]) == (
        system["id"], me(security), "admin", True, "qa-login@example.com")
    grant = created.value.json()
    line = rows(security, system["name"])
    for text in (holder, "Администратор", "qa-login@example.com", "бессрочно"):
        expect(line, text).to_contain_text(text)
    assert summary(security)["active_grants"] == before["active_grants"] + 1

    line.get_by_role("button", name="Отозван").click()
    dialog = security.get_by_role("dialog", name=re.compile("^Отметить, что доступ"))
    expect(dialog).to_contain_text(f"«{system['name']}» отозван в самой системе?")
    dialog.locator("input").fill("удалили учётку (автотест)")
    with security.expect_response(lambda r: r.url.endswith(f"grants/{grant['id']}/revoke")) as revoked:
        dialog.get_by_role("button", name="OK").click()

    assert revoked.value.ok and revoked.value.request.post_data_json == {"note": "удалили учётку (автотест)"}
    expect(rows(security, system["name"])).to_have_count(0)
    assert summary(security)["active_grants"] == before["active_grants"]
    pick(security, main, "Действующие", "Отозванные")
    expect(rows(security, system["name"])).to_contain_text(f"отозван {dt.date.today():%d.%m.%Y}")


# Кейс: Отзыв доступа можно отменить
@pytest.mark.writes_data
def test_revoke_can_be_cancelled(security: Page):
    system = create_system(security)
    grant = create_grant(security, system)
    reload(security)

    rows(security, system["name"]).get_by_role("button", name="Отозван").click()
    security.get_by_role("dialog", name=re.compile("^Отметить, что доступ")).get_by_role("button", name="Отмена").click()

    expect(rows(security, system["name"])).to_have_count(1)
    assert grants_of(security, system["id"], "active")[0]["id"] == grant["id"]


# Кейс: Доступ подрядчику
@pytest.mark.writes_data
def test_contractor_grant(security: Page):
    system = create_system(security)
    reload(security)

    security.locator("main").get_by_role("button", name="Выдать доступ").click()
    form = modal(security, "Выдать доступ")
    pick_by_label(security, form, "Система", system["name"])
    form.get_by_text("Нет в BORD (подрядчик)").click()
    contractor = f"{NAME_PREFIX} Подрядчик"
    form.locator("input:not([type])").first.fill(contractor)
    with security.expect_response(lambda r: r.url.endswith(f"{API}grants") and r.request.method == "POST") as created:
        form.get_by_role("button", name="Сохранить").click()

    assert created.value.status == 201, created.value.text()
    saved = created.value.json()
    assert saved["holder_id"] is None and saved["holder_name"] == contractor
    expect(rows(security, system["name"])).to_contain_text(contractor)


# Кейс: Счётчики рисков следуют за доступами
@pytest.mark.writes_data
def test_risk_counters_follow_grants(security: Page):
    """Истёкший доступ → «Истёк, не отозван», без 2FA в критичной системе → «Без 2FA в критичных»."""
    before = summary(security)
    critical = create_system(security, criticality="high", owner_id=me(security))
    create_grant(security, critical, mfa=False)
    create_grant(security, critical, mfa=True, expires_on=(dt.date.today() - dt.timedelta(days=2)).isoformat())
    reload(security)

    after = summary(security)
    assert after["no_mfa_critical"] == before["no_mfa_critical"] + 1
    assert after["expired"] == before["expired"] + 1
    for key in ("no_mfa_critical", "expired"):
        label = COUNTERS[key]
        expect(counter(security, label)).to_have_text(re.compile(rf"^{after[key]}\s*{label}$"))
    expired = rows(security, critical["name"]).filter(has_text="истёк")
    expect(expired).to_have_count(1)


# Кейс: Поиск по доступам
@pytest.mark.writes_data
def test_search_grants(security: Page):
    system = create_system(security)
    create_grant(security, system, login="qa-search-login@example.com")
    reload(security)
    box = security.locator("main").get_by_placeholder("Кто, система или логин")

    for query in (system["name"], "qa-search-login"):
        box.fill(query)
        expect(rows(security, system["name"]), query).to_have_count(1)
        expect(rows(security, "turcan.maxim@alsodev.com"), query).to_have_count(0)
    box.fill("")
    expect(rows(security, "turcan.maxim@alsodev.com")).to_have_count(1)


# Кейс: Система в архив и обратно
@pytest.mark.writes_data
def test_archive_and_restore_system(security: Page):
    system = create_system(security)
    reload(security)
    open_systems(security)
    main = security.locator("main")

    with security.expect_response(lambda r: r.url.endswith(f"systems/{system['id']}") and r.request.method == "PATCH") as archived:
        rows(security, system["name"]).get_by_role("button", name="В архив").click()

    assert archived.value.request.post_data_json == {"archived": True}
    expect(rows(security, system["name"])).to_have_count(0)
    main.get_by_role("button", name="Архив", exact=True).click()
    with security.expect_response(lambda r: r.url.endswith(f"systems/{system['id']}") and r.request.method == "PATCH") as restored:
        rows(security, system["name"]).get_by_role("button", name="Вернуть из архива").click()

    assert restored.value.request.post_data_json == {"archived": False}
    main.get_by_role("button", name="Действующие", exact=True).click()
    expect(rows(security, system["name"])).to_have_count(1)


# Кейс: Удаление системы без доступов
@pytest.mark.writes_data
def test_delete_empty_system(security: Page):
    system = create_system(security)
    reload(security)
    open_systems(security)

    rows(security, system["name"]).get_by_role("button", name=re.compile("^Удалить")).click()
    dialog = security.get_by_role("dialog", name=f"Удалить «{system['name']}»?")
    with security.expect_response(lambda r: r.url.endswith(f"systems/{system['id']}") and r.request.method == "DELETE") as deleted:
        dialog.get_by_role("button", name="Да").click()

    assert deleted.value.ok
    expect(rows(security, system["name"])).to_have_count(0)
    names = [s["name"] for flag in ("false", "true") for s in api_get(security, f"{API}systems?archived={flag}").json()["items"]]
    assert system["name"] not in names


# Кейс: Систему с доступами удалить нельзя
@pytest.mark.writes_data
def test_system_with_grants_cannot_be_deleted(security: Page):
    system = create_system(security)
    grant = create_grant(security, system)
    api_post(security, f"{API}grants/{grant['id']}/revoke", {"note": "автотест"})
    reload(security)
    open_systems(security)

    # с 08.11 фронт не показывает «Удалить» у системы, которой выдавали доступы, — только «В архив»
    row = rows(security, system["name"])
    expect(row.get_by_role("button", name="В архив")).to_be_visible()
    expect(row.get_by_role("button", name=re.compile("^Удалить"))).to_have_count(0)
    # и сервер такую систему по-прежнему не удаляет
    deleted = api_delete(security, f"{API}systems/{system['id']}")
    assert deleted.status == 409, deleted.text()
    assert "только отправить в архив" in deleted.text()


# Кейс: Вкладка реестра открывается
@pytest.mark.parametrize("label", TAB_API)
def test_tab_opens(security: Page, label):
    with security.expect_response(lambda r: f"{API}{TAB_API[label]}" in r.url) as loaded:
        tab(security, label).click()

    assert loaded.value.ok, f"{label}: {loaded.value.status}"
    expect(security.locator("main").get_by_text("Ошибка 500")).to_have_count(0)


# --- Увольнения и смена паролей -------------------------------------------

SECRET_BUG = "ecom-100215: комментарий «Сменил» принимает «пароль: …» и хранит его"


def contractor(tag: str) -> str:
    return f"{NAME_PREFIX} Подрядчик {tag} {int(time.time() * 1000)}"


def shared_login_system(page: Page) -> tuple[dict, str, str, str]:
    """Система тест-учётки и общий логин у двух подрядчиков."""
    system = create_system(page, owner_id=me(page))
    login = f"shared-{int(time.time() * 1000)}@example.com"
    first, second = contractor("A"), contractor("B")
    for holder in (first, second):
        create_grant(page, system, holder_id=None, holder_name=holder, login=login)
    return system, login, first, second


def open_tab(page: Page, key: str):
    page.goto(f"/admin/security?tab={key}")
    settle(page)


def offboarding_card(page: Page, holder: str):
    return page.locator("main div").filter(has=page.get_by_text(holder, exact=True)).filter(
        has=page.get_by_role("heading", name="Задачи")).last


def rotation_row(page: Page, login: str):
    return page.locator("main div").filter(has_text=f"логин {login}").filter(
        has=page.get_by_role("button", name="Сменил")).last


def revoke_via_api(page: Page, system: dict, holder: str):
    grant = next(g for g in grants_of(page, system["id"], "active") if g["holder_name"] == holder)
    assert api_post(page, f"{API}grants/{grant['id']}/revoke", {"note": "автотест"}).ok


# Кейс: Увольнение подрядчика: задачи, отзыв доступа, закрытие
@pytest.mark.writes_data
def test_offboarding_contractor(security: Page):
    system, login, leaving, _ = shared_login_system(security)
    open_tab(security, "offboardings")
    main = security.locator("main")

    main.get_by_role("button", name="Начать увольнение").click()
    form = modal(security, "Начать увольнение")
    expect(form.get_by_role("button", name="— не сотрудник BORD (подрядчик) —")).to_be_visible()
    form.get_by_text("Имя", exact=True).locator("xpath=following-sibling::input[1]").fill(leaving)
    form.locator("textarea").fill("уход автотеста")
    with security.expect_response(lambda r: r.url.endswith(f"{API}offboardings") and r.request.method == "POST") as started:
        form.get_by_role("button", name="Сохранить").click()

    assert started.value.status == 201, started.value.text()
    card = offboarding_card(security, leaving)
    expect(card.get_by_role("heading", name="Живые доступы: 1")).to_be_visible()
    expect(card).to_contain_text(system["name"])
    expect(card).to_contain_text("уход автотеста")
    protocol = card.get_by_role("link", name=re.compile(f"^Увольнение: {re.escape(leaving)}"))
    revoke_task = card.get_by_role("link", name=re.compile(f"^Отозвать доступы: {re.escape(leaving)}"))
    expect(protocol).to_contain_text(re.compile(r"0/\d+ · Новая"))
    expect(revoke_task).to_contain_text("0/1 · Новая")

    with security.expect_response(lambda r: "/revoke" in r.url and r.request.method == "POST") as revoked:
        card.get_by_role("button", name="Отозван").click()
    assert revoked.value.ok, revoked.value.text()
    expect(card.get_by_text("Живых доступов нет")).to_be_visible()
    expect(revoke_task).to_contain_text("1/1")
    assert [g["holder_name"] for g in grants_of(security, system["id"], "active")] != [] and all(
        g["holder_name"] != leaving for g in grants_of(security, system["id"], "active"))

    with security.expect_response(lambda r: r.url.endswith("/complete") and r.request.method == "POST") as completed:
        card.get_by_role("button", name="Закрыть увольнение").click()
    assert completed.value.ok, completed.value.text()
    expect(main.get_by_text(leaving, exact=True)).to_have_count(0)
    # у закрытой карточки задачи без заголовка-роли — ищем по имени и пометке
    closed = main.locator("div").filter(has=security.get_by_text(leaving, exact=True)).filter(
        has=security.get_by_text("закрыто", exact=True)).last
    # сразу после закрытия список перечитывается сам (тот же state=done) и клик
    # по «Закрытым» может потеряться — жмём, пока карточка не появится
    for _ in range(4):
        main.get_by_role("button", name="Закрытые", exact=True).click()
        try:
            expect(closed).to_be_visible(timeout=4_000)
            break
        except AssertionError:
            continue
    expect(closed).to_be_visible()
    expect(closed).to_contain_text(f"закрыл {my_name(security)}")


# Кейс: Общий логин: ушёл один — пароль в «надо сменить», «Сменил» закрывает
@pytest.mark.writes_data
def test_shared_login_rotation(security: Page):
    system, login, leaving, staying = shared_login_system(security)
    open_tab(security, "rotations")
    shared = security.locator("main table tbody tr").filter(has_text=login)
    expect(shared).to_contain_text(system["name"])
    expect(shared).to_contain_text(leaving)
    expect(shared).to_contain_text(staying)
    expect(rotation_row(security, login)).to_have_count(0)

    revoke_via_api(security, system, leaving)
    open_tab(security, "rotations")
    row = rotation_row(security, login)
    expect(row).to_contain_text(f"{leaving}: доступ отозван")
    expect(row).to_contain_text(f"Новый пароль сообщить: {staying}")
    expect(shared).to_have_count(0)

    row.get_by_role("button", name="Сменил").click()
    dialog = modal(security, "Пароль сменён")
    expect(dialog).to_contain_text(f"пароль логина {login} в «{system['name']}»")
    dialog.locator("textarea, input").first.fill("сменили, новый в сейфе")
    with security.expect_response(lambda r: r.url.endswith(f"{API}rotations/done")) as done:
        dialog.get_by_role("button", name="Сохранить").click()

    assert done.value.ok, done.value.text()
    sent = done.value.request.post_data_json
    assert (sent["kind"], sent["system_id"], sent["login"]) == ("login", system["id"], login)
    expect(rotation_row(security, login)).to_have_count(0)
    left = api_get(security, f"{API}rotations").json()["to_rotate"]
    assert all(item.get("login") != login for item in left)


# Кейс: Пароль в комментарии «Сменил» не принимается
@pytest.mark.writes_data
def test_rotation_note_rejects_secret(security: Page):
    system, login, leaving, _ = shared_login_system(security)
    revoke_via_api(security, system, leaving)
    open_tab(security, "rotations")

    rotation_row(security, login).get_by_role("button", name="Сменил").click()
    dialog = modal(security, "Пароль сменён")
    dialog.locator("textarea, input").first.fill("пароль: Qa-Fake-123!")
    with security.expect_response(lambda r: r.url.endswith(f"{API}rotations/done")) as done:
        dialog.get_by_role("button", name="Сохранить").click()

    if done.value.ok:
        pytest.xfail(SECRET_BUG)
    assert done.value.status == 400
    expect(dialog).to_contain_text("похоже на секрет")
    expect(rotation_row(security, login)).to_have_count(1)
