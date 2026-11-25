"""Блок 73. BORD One — единое окно: app.bord.one/admin/one (вид по умолчанию с 17.11.2026).

Снято 18.11.2026 (в видимом MCP-браузере):
- вид выбирает кука bord_view на .bord.one: «one» — единое окно, «classic» — прежний
  интерфейс (боковое меню, каждая система на своём домене). Остальная суита гоняет
  classic (helpers/auth.classic_view), этот блок — one;
- эталон оболочки — GET /api/admin/shell: sections[{key, title, items[{key, title, app,
  path, group, url, embed}]}]; порядок разделов зависит от направления организации
  (vertical; в BORD QA его ставит мастер онбординга);
- верхняя панель (nav «Меню»): «Главная» + разделы; клик по разделу открывает его
  первый пункт. Под ней подменю: пункты без группы — ссылки, группа — кнопка с
  role=menu (menuitem); справа «Настроить меню» → /admin/one/hub/settings/menu;
- адрес пункта: /admin/one/<app><path без /admin>; система открывается во встроенном
  окне iframe[title=<app>] по item.url (hub — app.bord.one/admin/…, crm — crm.bord.one,
  finance — fms.bord.one, targeting — ads.bord.one …);
- заход на crm.bord.one/admin/… при bord_view=one переадресует в оболочку, с
  ?standalone=1 — нет; «Обычный вид BORD» в шапке → /admin и bord_view=classic,
  «Единое окно» в боковом меню классики → /admin/one и bord_view=one;
- тур «Обучение BORD» (data-testid=vertical-tour) = GET /api/onboarding/tour: открывается
  сам, если tour.auto и в localStorage нет bord.tour.<org>.<user>, или по ?tour=1;
  шаги: «BORD настроен для вас», «Меню под ваш бизнес», tour[] (каждый открывает свой
  экран), «Поиск по всему BORD», «Помощник и инструкции», «Внедрение через Claude» →
  «Готово»; «Позже» / «Готово» ставят флаг в localStorage (на сервер ничего не уходит).

Блок только читает: меню, направление и роли не меняет.
"""
import re

import pytest
from playwright.sync_api import Browser, Page, expect

from helpers.api import api_get

pytestmark = pytest.mark.requires_auth

ONE = "https://app.bord.one/admin/one"
SHELL = "/api/admin/shell"
TOUR = "/api/onboarding/tour"
# флаг «тур пройден» ставим заранее — иначе тур открывается сам и перекрывает клики
TOUR_DONE = """(() => { try {
  const u = JSON.parse(localStorage.getItem('admin_user')), o = JSON.parse(localStorage.getItem('admin_current_org'));
  if (u && o) localStorage.setItem(`bord.tour.${o.id}.${u.id}`, '1');
} catch (e) {} })()"""


def one_context(browser: Browser, auth_state: str, context_args: dict, tour_done: bool = True):
    ctx = browser.new_context(**context_args, storage_state=auth_state)
    ctx.add_cookies([{"name": "bord_view", "value": "one", "domain": ".bord.one", "path": "/",
                      "secure": True, "sameSite": "Lax"}])
    if tour_done:
        ctx.add_init_script(TOUR_DONE)
    return ctx


@pytest.fixture
def one(browser, auth_state, browser_context_args):
    """Страница тест-учётки в виде BORD One, тур отмечен пройденным."""
    ctx = one_context(browser, auth_state, browser_context_args)
    page = ctx.new_page()
    yield page
    ctx.close()


@pytest.fixture
def fresh(browser, auth_state, browser_context_args):
    """BORD One без отметки о туре — как у человека, впервые открывшего единое окно."""
    ctx = one_context(browser, auth_state, browser_context_args, tour_done=False)
    page = ctx.new_page()
    yield page
    ctx.close()


def open_one(page: Page, path: str = "") -> Page:
    page.goto(ONE + path)
    expect(top_nav(page).get_by_role("link", name="Главная", exact=True)).to_be_visible(timeout=20_000)
    return page


def shell(page: Page) -> dict:
    resp = api_get(page, SHELL)
    assert resp.ok, f"{SHELL} → {resp.status}"
    return resp.json()


def menu_bar(page: Page):
    return page.get_by_role("navigation", name="Меню")


def top_nav(page: Page):
    """Разделы верхней панели: «Главная» и кнопки разделов (без иконок справа)."""
    return menu_bar(page).locator(":scope > div.flex-1")


def top_items(page: Page):
    return top_nav(page).locator(":scope > a, :scope > button")


def subnav(page: Page):
    """Подменю раздела — строка под верхней панелью."""
    return page.locator("nav[aria-label='Меню'] + div")


def sub_items(page: Page):
    return subnav(page).locator("div.flex-1 > a, div.flex-1 > button")


def one_url(item: dict) -> str:
    """Адрес пункта в оболочке: /admin/one/<app><path без /admin>."""
    return f"{ONE}/{item['app']}{item['path'][len('/admin'):]}"


def frame_url(item: dict) -> str:
    return item["url"] if item["url"].startswith("http") else "https://app.bord.one" + item["url"]


def label(title: str) -> re.Pattern:
    """Надпись пункта; у пунктов со счётчиком цифры идут сразу за названием («Входящие45»)."""
    return re.compile(rf"^{re.escape(title)}\s*\d*$")


def expected_subnav(section: dict) -> list[tuple[str, str]]:
    """Пункты без группы — ссылки, группа — одна кнопка на месте своего первого пункта."""
    out, seen = [], set()
    for item in section["items"]:
        if item["group"] is None:
            out.append(("a", item["title"]))
        elif item["group"] not in seen:
            seen.add(item["group"])
            out.append(("button", item["group"]))
    return out


def system_frame(page: Page, app: str):
    return page.locator(f"iframe[title='{app}']")


def expect_system_open(page: Page, item: dict):
    """Пункт открыт: адрес оболочки, видимое окно системы, внутри — адрес пункта и экран, а не вход."""
    expect(page).to_have_url(re.compile(rf"^{re.escape(one_url(item))}(\?|$)"), timeout=20_000)
    frame_el = system_frame(page, item["app"])
    expect(frame_el).to_be_visible(timeout=20_000)
    frame = frame_el.element_handle().content_frame()
    want = frame_url(item)
    for _ in range(40):  # адрес окна меняется после загрузки системы — ждём до 10 с
        if frame.url.split("?")[0].rstrip("/") == want.rstrip("/"):
            break
        page.wait_for_timeout(250)
    assert frame.url.split("?")[0].rstrip("/") == want.rstrip("/"), f"{item['key']}: в окне {frame.url}, ждали {want}"
    assert "/login" not in frame.url, f"{item['key']}: система попросила войти — {frame.url}"
    expect(page.frame_locator(f"iframe[title='{item['app']}']").locator("body")).to_contain_text(
        re.compile(r"\w{3}"), timeout=20_000)


# Кейс: Разделы верхней панели совпадают с /api/admin/shell
@pytest.mark.smoke
def test_top_sections_match_api(one: Page):
    open_one(one)
    data = shell(one)
    assert data["enabled"], data
    expect(top_items(one)).to_have_text([label("Главная")] + [label(s["title"]) for s in data["sections"]])
    expect(top_nav(one).get_by_role("link", name="Главная", exact=True)).to_have_attribute("href", "/admin/one")


# Кейс: Раздел открывает первый пункт, подменю = пункты и группы раздела из API
def test_section_opens_first_item_and_subnav(one: Page):
    open_one(one)
    for section in shell(one)["sections"]:
        top_nav(one).get_by_role("button", name=label(section["title"])).click()
        first = section["items"][0]
        expect(one, section["key"]).to_have_url(re.compile(rf"^{re.escape(one_url(first))}(\?|$)"), timeout=20_000)
        want = expected_subnav(section)
        expect(sub_items(one), section["key"]).to_have_text([label(t) for _, t in want])
        for (tag, title), el in zip(want, sub_items(one).all()):
            assert el.evaluate("e => e.tagName.toLowerCase()") == tag, f"{section['key']}/{title}: ждали <{tag}>"
            if tag == "a":
                item = next(i for i in section["items"] if i["title"] == title and i["group"] is None)
                expect(el).to_have_attribute("href", one_url(item).removeprefix("https://app.bord.one"))
        expect(subnav(one).get_by_role("link", name="Настроить меню")).to_have_attribute(
            "href", "/admin/one/hub/settings/menu")


# Кейс: Группа подменю раскрывается списком своих пунктов, пункт открывает систему
def test_group_menus_match_api(one: Page):
    open_one(one)
    clicked = None
    for section in shell(one)["sections"]:
        groups = [g for _, g in expected_subnav(section) if any(i["group"] == g for i in section["items"])]
        if not groups:
            continue
        top_nav(one).get_by_role("button", name=label(section["title"])).click()
        for group in groups:
            button = subnav(one).get_by_role("button", name=label(group))
            button.click()
            expect(button).to_have_attribute("aria-expanded", "true")
            menu = one.get_by_role("menu")
            expect(menu.get_by_role("menuitem"), f"{section['key']}/{group}").to_have_text(
                [label(i["title"]) for i in section["items"] if i["group"] == group])
            one.keyboard.press("Escape")
            expect(menu).to_have_count(0)
            clicked = clicked or (section, group)
    assert clicked, "в разделах нет ни одной группы — проверять нечего"
    section, group = clicked
    item = next(i for i in section["items"] if i["group"] == group)
    top_nav(one).get_by_role("button", name=label(section["title"])).click()
    subnav(one).get_by_role("button", name=label(group)).click()
    one.get_by_role("menu").get_by_role("menuitem", name=label(item["title"])).click()
    expect_system_open(one, item)


# Кейс: Каждая система открывается в оболочке своим окном по адресу из API
def test_each_system_opens_in_frame(one: Page):
    open_one(one)
    by_app: dict[str, dict] = {}
    for section in shell(one)["sections"]:
        for item in section["items"]:
            by_app.setdefault(item["app"], item)
    assert {"hub", "crm"} <= set(by_app), sorted(by_app)
    failed = []
    for app, item in by_app.items():
        one.goto(one_url(item))
        try:
            expect_system_open(one, item)
        except AssertionError as e:
            failed.append(f"{app} ({item['key']}): {str(e).splitlines()[0]}")
    assert not failed, "не открылись в оболочке:\n" + "\n".join(failed)


# Кейс: Заход на crm.bord.one при виде One уводит в оболочку, ?standalone=1 — нет
def test_module_domain_redirects_into_shell(one: Page):
    one.goto("https://crm.bord.one/admin/crm/deals")
    expect_system_open(one, {"key": "crm-deals", "app": "crm", "path": "/admin/crm/deals",
                             "url": "https://crm.bord.one/admin/crm/deals"})

    one.goto("https://crm.bord.one/admin/crm/deals?standalone=1")
    expect(one).to_have_title(re.compile(r"^Сделки · BORD CRM$"), timeout=20_000)
    assert one.url.startswith("https://crm.bord.one/admin/crm/deals"), one.url
    expect(menu_bar(one)).to_have_count(0)


# Кейс: «Обычный вид BORD» ↔ «Единое окно» переключают вид и куку bord_view
def test_switch_view_classic_and_back(one: Page):
    def view() -> str | None:
        return next((c["value"] for c in one.context.cookies("https://app.bord.one") if c["name"] == "bord_view"), None)

    open_one(one)
    menu_bar(one).get_by_role("link", name="Обычный вид BORD").click()
    expect(one).to_have_url("https://app.bord.one/admin")
    expect(one.locator("aside").get_by_role("link", name="Единое окно")).to_be_visible(timeout=20_000)
    assert view() == "classic"

    one.locator("aside").get_by_role("link", name="Единое окно").click()
    expect(one).to_have_url(ONE)
    expect(top_nav(one).get_by_role("link", name="Главная", exact=True)).to_be_visible(timeout=20_000)
    assert view() == "one"


def tour(page: Page):
    return page.get_by_test_id("vertical-tour")


# Кейс: Тур открывается сам при первом входе, «Позже» его больше не показывает
def test_tour_auto_and_later(fresh: Page):
    with fresh.expect_response(lambda r: r.url.endswith(TOUR)) as resp:
        open_one(fresh)
    data = resp.value.json()
    if not data["auto"]:
        pytest.skip("тур сейчас не включён на автозапуск (auto=false)")
    box = tour(fresh)
    expect(box).to_be_visible(timeout=10_000)
    expect(box).to_contain_text("BORD настроен для вас")
    expect(box).to_contain_text(f"Направление: {data['name']}")
    if data["modules"]:
        expect(box).to_contain_text("Включено: " + ", ".join(m["name"] for m in data["modules"]))

    box.get_by_role("button", name="Позже").click()
    expect(box).to_have_count(0)
    key = f"bord.tour.{data['org_id']}.{fresh.evaluate('JSON.parse(localStorage.admin_user).id')}"
    assert fresh.evaluate(f"localStorage.getItem('{key}')") == "1"

    with fresh.expect_response(lambda r: r.url.endswith(TOUR)):
        fresh.reload()
    fresh.wait_for_timeout(1_500)  # тур открывается через 0.7 с после ответа — проверяем, что не открылся
    expect(tour(fresh)).to_have_count(0)


# Кейс: Тур по ?tour=1 — шаги из API, каждый шаг открывает свой экран, «Готово» закрывает
def test_tour_steps_follow_api(one: Page):
    with one.expect_response(lambda r: r.url.endswith(TOUR)) as resp:
        open_one(one, "?tour=1")
    steps = resp.value.json()["tour"]
    box = tour(one)
    titles = (["BORD настроен для вас", "Меню под ваш бизнес"] + [s["title"] for s in steps]
              + ["Поиск по всему BORD", "Помощник и инструкции", "Внедрение через Claude"])
    for n, title in enumerate(titles):
        expect(box, f"шаг {n + 1}").to_contain_text(title, timeout=10_000)
        step = steps[n - 2] if 2 <= n < 2 + len(steps) else None
        if step and step.get("app") and step.get("path"):
            expect(one).to_have_url(re.compile(rf"^{re.escape(one_url(step))}(\?|$)"), timeout=20_000)
        if n == len(titles) - 1:
            box.get_by_role("button", name="Готово").click()
        else:
            if n:
                expect(box.get_by_role("button", name="Назад")).to_be_visible()
            box.get_by_role("button", name="Дальше").click()
    expect(box).to_have_count(0)
    assert "tour=" not in one.url, one.url
