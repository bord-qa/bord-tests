"""Блок 31. CRM — команда и роли: crm.bord.one/admin/crm/team.

Снято 29.10.2026:
- «Сотрудники»: таблица = GET /api/crm/roles/members ({members: [{user_id,
  name, email, role, role_name, crm_permissions, is_admin, joined_at}]}) и
  GET /settings/members (scope: all / own_tasks / own). У админа — плашка
  «админ», роль «Суперпользователь» текстом, «Доступ к записям» выключен с
  подписью «Администратора ограничить нельзя», кнопки «Убрать из
  организации» нет. У остальных — выпадашки «Роль — <имя>» (PUT
  /roles/members/<id> {role}) и «Доступ к записям — <имя>» (PUT
  /settings/members/<id>/scope {scope}); при crm_permissions = 0 подсказка
  «У роли нет прав — сотрудник не откроет ни одного экрана»;
- «Пригласить сотрудника» (POST /roles/invites/ — уходит письмо) и «Создать
  сотрудника по логину» (POST /roles/login-accounts/ — заводит учётку): тесты
  только заполняют формы и проверяют, когда кнопка становится доступной, —
  не отправляют;
- «Роли и права»: GET /roles/ → {roles: [{slug, name, kind: system|custom,
  permissions (CRM), other (права других модулей), members}]}; карточками
  показаны системные роли и роли с правами CRM, остальные — под «Показать
  роли других продуктов (N)». BORD QA переведена на роли модулей — плашка
  «Права в CRM этой организации решают роли модуля в «Доступах»». Своя роль:
  «Создать роль» → POST /roles/ {name, permissions: [crm.view]} → карточка
  раскрыта, галочки прав → PUT /roles/<slug> {permissions}, «Удалить роль» →
  «Удалить роль «…»?» → «Продолжить» → DELETE /roles/<slug>.

Менять роль и видимость можно только второй тест-учётке (qaColleague) —
тест возвращает их как было. Живой аккаунт и прочих участников не трогаем,
«Разложить роли» (типовые роли в организацию) не нажимаем. В выпадашке ролей
два «Сотрудника» (системный и роль модуля) — системный идёт первым.
"""
import datetime as dt
import random
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.auth import load_user
from helpers.crm import QA_PREFIX, crm_delete, crm_get, crm_put, open_crm

pytestmark = pytest.mark.requires_auth

PAGE = "/admin/crm/team"
SCOPES = {"all": "Все записи организации", "own_tasks": "Все записи, задачи — только свои",
          "own": "Только свои записи"}


def tag() -> str:
    return "".join(random.choices("abcdefghijkmnpqrstuvwxyz", k=8))


def members(page: Page) -> list[dict]:
    return crm_get(page, "/roles/members").json()["members"]


def scopes(page: Page) -> dict[int, str]:
    return {m["user_id"]: m["scope"] for m in crm_get(page, "/settings/members").json()["items"]}


def roles(page: Page) -> list[dict]:
    return crm_get(page, "/roles/").json()["roles"]


def colleague(page: Page) -> dict:
    user = load_user("qaColleague")
    if not user:
        pytest.skip("нет второй учётки qaColleague")
    found = next((m for m in members(page) if m["email"] == user["email"]), None)
    if not found:
        pytest.skip("qaColleague не состоит в команде CRM BORD QA")
    return found


def delete_qa_roles(page: Page):
    for role in roles(page):
        if role["kind"] == "custom" and role["name"].startswith(QA_PREFIX):
            crm_delete(page, f"/roles/{role['slug']}")


@pytest.fixture
def team(auth_page: Page):
    yield auth_page
    delete_qa_roles(auth_page)


def open_team(page: Page, tab: str = "Сотрудники"):
    open_crm(page, PAGE)
    page.locator("main").get_by_role("button", name=tab, exact=True).click()


def pick(page: Page, option: str, first: bool = False):
    """Вариант в плавающем списке; first — первый из одноимённых (системная роль)."""
    found = page.locator("div.shadow-xl[style*='position: fixed']").last.get_by_role("button", name=option, exact=True)
    (found.first if first else found).click()


def member_row(page: Page, user: dict):
    return page.locator("main table tbody tr").filter(has_text=user["email"])


def role_card(page: Page, name: str):
    return page.locator("main").get_by_role("button", name=re.compile(f"^{re.escape(name)}"))


# Кейс: Таблица сотрудников совпадает с API — имя, почта, ID, роль, видимость записей, «админ», дата
def test_members_match_api(team: Page):
    open_team(team)
    people, scope = members(team), scopes(team)

    expect(team.locator("main table tbody tr")).to_have_count(len(people))
    for m in people:
        row = member_row(team, m)
        expect(row).to_contain_text(f"ID {m['user_id']}")
        joined = dt.datetime.fromisoformat(m["joined_at"]).astimezone()
        expect(row).to_contain_text(f"{joined:%d.%m.%Y}")
        access = row.get_by_role("button", name=re.compile("^Доступ к записям"))
        expect(access).to_contain_text(SCOPES[scope[m["user_id"]]])
        if m["is_admin"]:
            expect(row).to_contain_text("админ")
            expect(row).to_contain_text(m["role_name"])
            expect(access).to_be_disabled()
            expect(row).to_contain_text("Администратора ограничить нельзя")
            expect(row.get_by_role("button", name="Убрать из организации")).to_have_count(0)
        else:
            expect(row.get_by_role("button", name=re.compile("^Роль — "))).to_contain_text(m["role_name"])
            expect(access).to_be_enabled()
            expect(row.get_by_role("button", name="Убрать из организации")).to_be_visible()
            hint = row.get_by_text("У роли нет прав — сотрудник не откроет ни одного экрана")
            expect(hint).to_have_count(1 if m["crm_permissions"] == 0 else 0)


# Кейс: Видимость записей у второй учётки — «Только свои записи» и обратно, API и подпись
def test_colleague_scope_change_and_back(team: Page):
    user = colleague(team)
    before = scopes(team)[user["user_id"]]
    open_team(team)
    access = member_row(team, user).get_by_role("button", name=f"Доступ к записям — {user['name']}")
    target = "own" if before != "own" else "all"
    try:
        access.click()
        with team.expect_response(lambda r: r.url.endswith(f"/settings/members/{user['user_id']}/scope")
                                  and r.request.method == "PUT") as resp:
            pick(team, SCOPES[target])
        assert resp.value.ok and resp.value.json()["scope"] == target, resp.value.text()
        expect(access).to_contain_text(SCOPES[target])
        assert scopes(team)[user["user_id"]] == target

        access.click()
        with team.expect_response(lambda r: r.url.endswith(f"/settings/members/{user['user_id']}/scope")):
            pick(team, SCOPES[before])
        expect(access).to_contain_text(SCOPES[before])
    finally:
        crm_put(team, f"/settings/members/{user['user_id']}/scope", {"scope": before})
    assert scopes(team)[user["user_id"]] == before


# Кейс: Роль второй учётки — «Руководитель» и обратно на системного «Сотрудника», API и подпись
def test_colleague_role_change_and_back(team: Page):
    user = colleague(team)
    before = user["role"]
    open_team(team)
    role = member_row(team, user).get_by_role("button", name=f"Роль — {user['name']}")
    try:
        role.click()
        with team.expect_response(lambda r: r.url.endswith(f"/roles/members/{user['user_id']}")
                                  and r.request.method == "PUT") as resp:
            pick(team, "Руководитель", first=True)
        assert resp.value.ok and resp.value.json()["role"] == "head_of_support", resp.value.text()
        expect(role).to_contain_text("Руководитель")
        assert next(m for m in members(team) if m["user_id"] == user["user_id"])["role"] == "head_of_support"

        role.click()
        with team.expect_response(lambda r: r.url.endswith(f"/roles/members/{user['user_id']}")) as resp:
            pick(team, user["role_name"], first=True)
        assert resp.value.json()["role"] == before, resp.value.text()
        expect(role).to_contain_text(user["role_name"])
    finally:
        crm_put(team, f"/roles/members/{user['user_id']}", {"role": before})
    assert next(m for m in members(team) if m["user_id"] == user["user_id"])["role"] == before


# Кейс: «Пригласить» доступна только с почтой — письмо тест не отправляет
def test_invite_needs_email(team: Page):
    open_team(team)
    main = team.locator("main")
    email, invite = main.get_by_placeholder("почта@компания.md"), main.get_by_role("button", name="Пригласить", exact=True)

    expect(main).to_contain_text("Ссылка действует неделю и срабатывает один раз.")
    expect(invite).to_be_disabled()
    email.fill(f"qa-autotest-{tag()}@example.com")
    expect(invite).to_be_enabled()
    email.fill("")
    expect(invite).to_be_disabled()


# Кейс: «Создать сотрудника по логину» — кнопка доступна после логина, имени и пароля; «Сгенерировать» даёт пароль ≥ 8
def test_login_account_form(team: Page):
    open_team(team)
    main = team.locator("main")
    form = main.locator("div").filter(has=team.get_by_text("Создать сотрудника по логину", exact=True)).filter(
        has=team.get_by_placeholder("kassa1")).last
    create = form.get_by_role("button", name="Создать", exact=True)
    password = form.get_by_placeholder("Не короче 8 символов")

    expect(create).to_be_disabled()
    form.get_by_placeholder("kassa1").fill(f"qa_{tag()}")
    form.get_by_placeholder("Иван Петров или «Продавец Касса 1»").fill(f"{QA_PREFIX} {tag()}")
    expect(create).to_be_disabled()
    form.get_by_role("button", name="Сгенерировать").click()
    expect(password).to_have_value(re.compile(r"^\S{8,}$"))
    expect(create).to_be_enabled()


# Кейс: «Роли и права» совпадают с API — карточки ролей CRM, «системная», участники, права; остальные под «Показать роли других продуктов (N)»
def test_roles_tab_matches_api(team: Page):
    open_team(team, "Роли и права")
    all_roles = roles(team)
    shown = [r for r in all_roles if r["kind"] == "system" or r["permissions"]]
    hidden = len(all_roles) - len(shown)
    main = team.locator("main")

    expect(main).to_contain_text("Права в CRM этой организации решают роли модуля в «Доступах»")
    supers = sum(m["role"] == "superuser" for m in members(team))
    # число согласуется: «1 суперпользователь», «2 суперпользователя»
    expect(main).to_contain_text(re.compile(rf"в организации есть {supers} суперпользовател(ь|я|ей)[^а-я]"))
    expect(main.get_by_role("button", name="Показать роли других продуктов")).to_contain_text(f"({hidden})")
    for role in shown:
        card = role_card(team, role["name"]).filter(has_text=str(role["members"]))
        expect(card.first).to_be_visible()
        text = card.first
        if role["kind"] == "system":
            expect(text).to_contain_text("системная")
        if role["permissions"]:
            expect(text).to_contain_text(f"{len(role['permissions'])} пра")
        else:
            expect(text).to_contain_text("ничего не может")


# Кейс: «Создать роль» неактивна без названия
def test_create_role_needs_name(team: Page):
    open_team(team, "Роли и права")
    main = team.locator("main")
    name, create = main.get_by_placeholder("Название новой роли — «Агент», «Оценщик»"), main.get_by_role(
        "button", name="Создать роль", exact=True)

    expect(create).to_be_disabled()
    name.fill(f"{QA_PREFIX} {tag()}")
    expect(create).to_be_enabled()
    name.fill("   ")
    expect(create).to_be_disabled()


# Кейс: Своя роль — создать (право «Видеть CRM»), добавить «Вести сделки», удалить через подтверждение
def test_custom_role_lifecycle(team: Page):
    name = f"{QA_PREFIX} {tag()}"
    open_team(team, "Роли и права")
    main = team.locator("main")

    main.get_by_placeholder("Название новой роли — «Агент», «Оценщик»").fill(name)
    with team.expect_response(lambda r: r.url.endswith("/api/crm/roles/") and r.request.method == "POST") as resp:
        main.get_by_role("button", name="Создать роль", exact=True).click()
    assert resp.value.status == 201, resp.value.text()
    role = resp.value.json()
    assert (role["name"], role["kind"], role["permissions"], role["members"]) == (name, "custom", ["crm.view"], 0), role

    # has= ищет внутри карточки — локатор кнопки без привязки к main
    box = main.locator("div.rounded-xl.border").filter(
        has=team.get_by_role("button", name=re.compile(f"^{re.escape(name)}"))).last
    expect(role_card(team, name)).to_contain_text("1 пра")
    expect(box.locator("label").filter(has_text="Видеть CRM").locator("input")).to_be_checked()
    with team.expect_response(lambda r: r.url.endswith(f"/api/crm/roles/{role['slug']}")
                              and r.request.method == "PUT") as resp:
        box.locator("label").filter(has_text="Вести сделки").click()
    assert resp.value.json()["permissions"] == ["crm.view", "crm.manage_deals"], resp.value.text()
    expect(role_card(team, name)).to_contain_text("2 пра")
    assert next(r for r in roles(team) if r["slug"] == role["slug"])["permissions"] == ["crm.view", "crm.manage_deals"]

    box.get_by_role("button", name="Удалить роль").click()
    confirm = team.locator("div.fixed.inset-0").filter(has_text=f"Удалить роль «{name}»?").last
    with team.expect_response(lambda r: r.url.endswith(f"/api/crm/roles/{role['slug']}")
                              and r.request.method == "DELETE") as resp:
        confirm.get_by_role("button", name="Продолжить", exact=True).click()
    assert resp.value.ok, resp.value.status
    expect(role_card(team, name)).to_have_count(0)
    assert role["slug"] not in [r["slug"] for r in roles(team)]
