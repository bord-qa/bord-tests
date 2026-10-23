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

Ответственный и владелец доступа в тестах — только тест-учётка. Уборка:
пустые системы «QA Autotest …» удаляются, у систем с доступами доступы
отзываются, а сама система уходит в архив (удалить её API не даёт).
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
    systems = []
    for archived in ("false", "true"):
        resp = api_get(page, f"{API}systems?archived={archived}")
        systems += resp.json()["items"] if resp.ok else []
    for system in systems:
        if not system["name"].startswith(NAME_PREFIX):
            continue
        if api_delete(page, f"{API}systems/{system['id']}").status == 409:
            for grant in grants_of(page, system["id"], "active"):
                api_post(page, f"{API}grants/{grant['id']}/revoke", {"note": "уборка автотеста"})
            if not system["archived"]:
                api_patch(page, f"{API}systems/{system['id']}", {"archived": True})


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
    return page.locator("main").get_by_role("button", name=label, exact=True)


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


@pytest.mark.smoke
def test_counters_match_summary(security: Page):
    data = summary(security)

    for key, label in COUNTERS.items():
        expect(counter(security, label), label).to_have_text(re.compile(rf"^{data[key]}\s*{label}$"))


def test_builtin_bord_system_lists_org_members(security: Page):
    """Встроенная «BORD» = участники организации, «авто», со ссылкой в «Пользователи»."""
    users = api_get(security, "/api/admin/auth/users").json()
    bord = [g for g in api_get(security, f"{API}grants?state=active").json()["items"] if g["system"]["name"] == "BORD"]

    assert sorted(g["login"] for g in bord) == sorted(u["email"] for u in users)
    for user in users:
        line = rows(security, user["email"])
        expect(line).to_contain_text("BORD")
        expect(line).to_contain_text("авто")
        expect(line.get_by_role("link", name="в «Пользователях»")).to_have_attribute("href", "/admin/users")


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


@pytest.mark.writes_data
def test_revoke_can_be_cancelled(security: Page):
    system = create_system(security)
    grant = create_grant(security, system)
    reload(security)

    rows(security, system["name"]).get_by_role("button", name="Отозван").click()
    security.get_by_role("dialog", name=re.compile("^Отметить, что доступ")).get_by_role("button", name="Отмена").click()

    expect(rows(security, system["name"])).to_have_count(1)
    assert grants_of(security, system["id"], "active")[0]["id"] == grant["id"]


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


@pytest.mark.writes_data
def test_system_with_grants_cannot_be_deleted(security: Page):
    system = create_system(security)
    grant = create_grant(security, system)
    api_post(security, f"{API}grants/{grant['id']}/revoke", {"note": "автотест"})
    reload(security)
    open_systems(security)

    rows(security, system["name"]).get_by_role("button", name=re.compile("^Удалить")).click()
    with security.expect_response(lambda r: r.url.endswith(f"systems/{system['id']}") and r.request.method == "DELETE") as deleted:
        security.get_by_role("dialog", name=f"Удалить «{system['name']}»?").get_by_role("button", name="Да").click()

    assert deleted.value.status == 409
    expect(security.get_by_text("К системе уже выдавали доступы — её можно только отправить в архив")).to_be_visible()
    expect(rows(security, system["name"])).to_have_count(1)


@pytest.mark.parametrize("label", TAB_API)
def test_tab_opens(security: Page, label):
    with security.expect_response(lambda r: f"{API}{TAB_API[label]}" in r.url) as loaded:
        tab(security, label).click()

    assert loaded.value.ok, f"{label}: {loaded.value.status}"
    expect(security.locator("main").get_by_text("Ошибка 500")).to_have_count(0)
