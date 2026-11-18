"""Блок 18. Мастер первой настройки: app.bord.one/admin/onboarding.

Снято 27.10.2026:
- над мастером — блок «Настроить с ИИ» с адресом https://app.bord.one/mcp;
- шаги-кнопки «1. Компания» / «2. Команда»;
- «1. Компания» — те же поля и тот же API, что «Настройки → Компания»
  (GET /api/onboarding, «Сохранить» → PUT /api/onboarding/company,
  уходят все поля формы) → переход на «2. Команда», а если она уже «сделано»
  (с 08.11) — сразу в «Биллинг» (/admin/subscription); реквизиты свёрнуты под «Исправить реквизиты вручную»; «Пропустить и перейти к
  модулям» → /admin/catalog («Модули», с 12.11; раньше — «Биллинг»);
- «2. Команда» — строки «email@компания.md» + роль + «Доступ к модулям: N из
  M» + «Убрать»; «Ещё коллега» добавляет строку; «Отправить приглашения» →
  POST /api/admin/auth/invites на каждую строку (note «Приглашение из первой
  настройки»), отправленная строка блокируется с зелёной галочкой;
  «Дальше — выбрать модули» → /admin/catalog (каталог подключения модулей).

Настоящие адреса в «Команду» не вводим — придёт письмо. ecom-100214: адрес
не проверяется ни формой, ни сервером — «not-an-email» становится рабочим
приглашением с галочкой «отправлено», а так как приглашение с письмом сразу
заводит аккаунт, «not-an-email» становится и участником BORD QA. Тест на это —
xfail; уборка отзывает приглашение и удаляет такого участника. Реквизиты общие на организацию — фикстура возвращает их.
"""
import re
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_delete, api_get, api_put
from helpers.wait import settle

# с 12.11 «Пропустить и перейти к модулям» и «Дальше — выбрать модули» вели в каталог «Модули»;
# с 17.11 обе («Дальше — к работе») ведут в тур нового вида BORD One
MODULES_URL = "https://app.bord.one/admin/one?tour=1"  # до 17.11 — /admin/catalog

pytestmark = pytest.mark.requires_auth

ONBOARDING = "/api/onboarding"
SAVE = "/api/onboarding/company"
INVITES = "/api/admin/auth/invites"
USERS = "/api/admin/auth/users"
FIELDS = ("name", "industry", "legal_name", "idno", "vat_code", "address", "phone")
BAD_EMAIL_PREFIX = "qa-autotest-no-at-"
EMAIL_BUG = "ecom-100214: приглашение создаётся на строку без «@», мастер ставит галочку «отправлено»"


def company(page: Page) -> dict:
    resp = api_get(page, ONBOARDING)
    assert resp.ok, f"{ONBOARDING} → {resp.status}"
    return resp.json()["company"]


def revoke_bad_invites(page: Page):
    resp = api_get(page, INVITES)
    for inv in resp.json() if resp.ok else []:
        if (inv.get("email") or "").startswith(BAD_EMAIL_PREFIX):
            api_delete(page, f"{INVITES}/{inv['code']}")
    # приглашение с письмом сразу заводит участника (и аккаунт) — пока жив ecom-100214,
    # «адрес» без @ становится членом BORD QA; удаление из единственной орги стирает и аккаунт
    resp = api_get(page, USERS)
    for user in resp.json() if resp.ok else []:
        if (user.get("email") or "").startswith(BAD_EMAIL_PREFIX):
            api_delete(page, f"{USERS}/{user['id']}")


@pytest.fixture
def wizard(auth_page: Page):
    auth_page.goto("/admin/onboarding")
    expect(step(auth_page, "1. Компания")).to_be_visible()
    settle(auth_page)
    before = company(auth_page)
    yield auth_page
    revoke_bad_invites(auth_page)
    if company(auth_page) != before:
        restored = api_put(auth_page, SAVE, {k: before.get(k) or "" for k in FIELDS})
        assert restored.ok, f"не удалось вернуть реквизиты: {restored.status} {restored.text()}"


def step(page: Page, name: str):
    # с 08.11 у пройденного шага в кнопке иконка «сделано» (входит в имя) — берём по data-testid
    return page.locator("main").get_by_test_id(f"step-{name.split('.')[0]}")


def field(page: Page, label: str):
    return page.locator("main").get_by_text(label, exact=True).locator("xpath=following-sibling::input[1]")


def email_rows(page: Page):
    return page.locator("main").get_by_placeholder("email@компания.md")


def open_team(page: Page):
    step(page, "2. Команда").click()
    expect(page.locator("main").get_by_role("button", name="Отправить приглашения")).to_be_visible()


# Кейс: Шаг «Компания» показывает реквизиты из API и ведёт на «Команду»
@pytest.mark.writes_data
def test_company_step_matches_api_and_goes_next(wizard: Page):
    data = company(wizard)
    main = wizard.locator("main")
    expect(main.locator("code", has_text="https://app.bord.one/mcp")).to_be_visible()
    # с 08.11 реквизиты свёрнуты под ссылкой (по умолчанию их берёт IDNO из госреестра)
    main.get_by_text("Исправить реквизиты вручную", exact=True).click()
    expect(field(wizard, "Название в BORD *")).to_have_value(data["name"])
    expect(main.get_by_placeholder("Exemplu SRL")).to_have_value(data["legal_name"] or "")
    expect(main.get_by_placeholder("13 цифр")).to_have_value(data["idno"] or "")
    expect(field(wizard, "Код НДС")).to_have_value(data["vat_code"] or "")
    expect(field(wizard, "Адрес")).to_have_value(data["address"] or "")
    expect(main.get_by_placeholder("+373 …")).to_have_value(data["phone"] or "")

    team_done = step(wizard, "2. Команда").get_by_role("img", name="сделано").count() > 0
    with wizard.expect_response(lambda r: r.url.endswith(SAVE) and r.request.method == "PUT") as saved:
        main.get_by_role("button", name="Сохранить", exact=True).click()

    assert saved.value.ok, saved.value.text()
    assert saved.value.request.post_data_json["name"] == data["name"]
    # с 08.11 мастер перескакивает пройденные шаги: если «Команда» уже «сделано»
    # (в организации больше одного человека), «Сохранить» ведёт сразу к модулям
    if team_done:
        # «Сохранить» ведёт в «Биллинг» (с 17.11 — /admin/billing, раньше /admin/subscription)
        expect(wizard).to_have_url(re.compile(r"^https://app\.bord\.one/admin/(billing|subscription)$"))
    else:
        expect(main.get_by_role("button", name="Отправить приглашения")).to_be_visible()
    # без правок реквизиты не меняются
    assert company(wizard) == data


# Кейс: «Пропустить» и «Дальше» ведут в тур BORD One
@pytest.mark.parametrize("button", ["Пропустить и перейти к модулям", "Дальше — к работе"])
def test_skip_buttons_lead_to_modules(wizard: Page, button: str):
    """С 17.11 мастер заканчивается не каталогом «Модули», а туром по новому виду:
    /admin/one?tour=1 с окном «BORD настроен для вас» (вид переключается на bord_view=one).
    «Дальше — выбрать модули» стала «Дальше — к работе»; у «Пропустить и перейти к модулям» надпись прежняя."""
    if button.startswith("Дальше"):
        open_team(wizard)
    wizard.locator("main").get_by_role("button", name=button).click()
    expect(wizard).to_have_url(MODULES_URL)
    expect(wizard.get_by_text("BORD настроен для вас")).to_be_visible(timeout=15_000)


# Кейс: «Ещё коллега» добавляет строку, «Убрать» убирает
def test_team_rows_add_and_remove(wizard: Page):
    open_team(wizard)
    main = wizard.locator("main")
    expect(email_rows(wizard)).to_have_count(1)
    expect(main.get_by_text("Доступ к модулям:", exact=False)).to_have_count(1)

    main.get_by_role("button", name="Ещё коллега").click()
    expect(email_rows(wizard)).to_have_count(2)
    email_rows(wizard).nth(1).fill("второй")
    main.get_by_role("button", name="Убрать").first.click()

    expect(email_rows(wizard)).to_have_count(1)
    expect(email_rows(wizard).first).to_have_value("второй")


# Кейс: Строка без «@» не становится приглашением
@pytest.mark.writes_data
def test_invalid_email_not_invited(wizard: Page):
    open_team(wizard)
    bad = f"{BAD_EMAIL_PREFIX}{int(time.time() * 1000)}"
    sent = []
    wizard.on("request", lambda r: sent.append(r) if r.url.endswith(INVITES) and r.method == "POST" else None)
    email_rows(wizard).first.fill(bad)
    wizard.locator("main").get_by_role("button", name="Отправить приглашения").click()
    # отсутствие запроса не дождаться событием — даём форме время на ответ
    wizard.wait_for_timeout(2000)

    created = [i for i in api_get(wizard, INVITES).json() if i.get("email") == bad]
    if created:
        pytest.xfail(EMAIL_BUG)
    assert not sent or all(r.response() and not r.response().ok for r in sent)
    expect(email_rows(wizard).first).to_be_enabled()
