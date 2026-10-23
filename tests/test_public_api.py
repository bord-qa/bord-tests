"""Блок 14. Public API: app.bord.one/admin/developers.

Снято 22.10.2026 (экран целиком на английском):
- токены: GET/POST /api/admin/api-tokens/, …/scopes (модуль × read/write),
  DELETE …/<id> = отзыв (строка остаётся с пометкой «Revoked»),
  …/<id>/audit — «Recent API Calls» под токеном;
- «Create Token» → окно: Token Name, галочки скоупов (подпись модуля в разметке
  строчная), Rate Limit (req/min), Expires → POST → 201 и окно «API Token
  Created» — токен «bord_…» показан один раз, в списке потом только префикс;
- в строке: «View audit log», «Regenerate token» (окно «Regenerate token "…"?
  The old token will stop working immediately.» — Отмена / Да), «Revoke token»;
- сам API: https://app.bord.one/api/v1/<модуль>/…, Authorization: Bearer
  bord_…; без токена / чужой / отозванный — 401, нет скоупа — 403
  insufficient_scope, сверх лимита в минуту — 429; лимит в X-RateLimit-Limit;
  ответ — {items, total} (skip/limit из Quick Start не приходят);
- по документации: hrm/departments, hrm/positions, mms/materials,
  mms/qc-checks → 404 (ecom-100161), весь CRM → 502 Cloudflare (ecom-100160).

Лимит — 20 токенов на организацию, и отозванные в него входят, а удалить их
нельзя (ecom-100162). Поэтому токенов на прогон минимум: один общий на модуль
(все скоупы чтения — им идут проверки чтения и сверка документации) и по
одному у тестов создания, лимита запросов, отзыва и перевыпуска. Упёрлись в
лимит — тест помечается xfail с этим багом.

Токены — секрет: тесты не печатают их и не сравнивают в assert'ах напрямую
(pytest вывел бы значение в лог).
"""
import re
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_delete, api_get, api_post
from helpers.modal import modal
from helpers.wait import settle

# Вне обычного прогона: каждый запуск навсегда съедает 5 токенов из 20 (ecom-100162).
pytestmark = [pytest.mark.requires_auth, pytest.mark.manual]

APP = "https://app.bord.one"
TOKENS = "/api/admin/api-tokens/"
NAME_PREFIX = "QA Autotest"
SHARED = f"{NAME_PREFIX} shared"
DOCS_404 = "ecom-100161: эндпоинт из Documentation отвечает 404"
CRM_502 = "ecom-100160: Public API CRM отвечает 502"
LIMIT_BUG = "ecom-100162: отозванные токены съели лимит «Maximum 20 API tokens per organization»"
MISSING = {("hrm", "/departments/"), ("hrm", "/positions/"), ("mms", "/materials/"), ("mms", "/qc-checks/")}
DOCUMENTED = [
    ("hrm", "/employees/"), ("hrm", "/departments/"), ("hrm", "/positions/"), ("hrm", "/leave/requests/"),
    ("wms", "/products/"), ("wms", "/warehouses/"), ("wms", "/stock/"), ("wms", "/orders/"),
    ("crm", "/contacts/"), ("crm", "/companies/"), ("crm", "/leads/"), ("crm", "/deals/"), ("crm", "/pipelines/"),
    ("finance", "/accounts/"), ("finance", "/transactions/"), ("finance", "/invoices/"),
    ("finance", "/counterparties/"), ("finance", "/reports/pnl/"),
    ("mms", "/orders/"), ("mms", "/workshops/"), ("mms", "/materials/"), ("mms", "/qc-checks/"),
]


def name(prefix: str = NAME_PREFIX) -> str:
    return f"{prefix} token {int(time.time() * 1000)}"


def check_limit(resp):
    if resp.status == 400 and "Maximum" in resp.text():
        pytest.xfail(LIMIT_BUG)


def create_token(page: Page, scopes=("hrm.read",), rate_limit: int = 60, prefix: str = NAME_PREFIX) -> dict:
    resp = api_post(page, TOKENS, {"name": name(prefix), "scopes": list(scopes), "rate_limit": rate_limit})
    check_limit(resp)
    assert resp.status == 201, resp.status
    return resp.json()


def call(page: Page, path: str, token: str | None = None, method: str = "GET"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return page.request.fetch(APP + path, method=method, headers=headers)


def revoke_qa_tokens(page: Page, keep_shared: bool = True):
    resp = api_get(page, TOKENS)
    for item in resp.json() if resp.ok else []:
        if not item["name"].startswith(NAME_PREFIX) or not item["is_active"]:
            continue
        if keep_shared and item["name"].startswith(SHARED):
            continue
        api_delete(page, f"{TOKENS}{item['id']}")


def is_token(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"bord_[A-Za-z0-9_\-]{16,}", value) is not None


@pytest.fixture(scope="module")
def shared(browser, auth_state, browser_context_args):
    """Общий токен модуля: все скоупы чтения, лимит 1000 в минуту."""
    ctx = browser.new_context(**browser_context_args, storage_state=auth_state)
    page = ctx.new_page()
    page.goto("/admin/developers")
    scopes = [s["scope"] for s in api_get(page, f"{TOKENS}scopes").json() if s["action"] == "read"]
    resp = api_post(page, TOKENS, {"name": name(SHARED), "scopes": scopes, "rate_limit": 1000})
    token = resp.json() if resp.status == 201 else None
    yield page, token
    if token:
        api_delete(page, f"{TOKENS}{token['id']}")
    ctx.close()


@pytest.fixture
def shared_token(shared) -> tuple[Page, dict]:
    page, token = shared
    if token is None:
        pytest.xfail(LIMIT_BUG)
    return page, token


@pytest.fixture
def dev(auth_page: Page):
    auth_page.goto("/admin/developers")
    expect(auth_page.get_by_role("heading", name="Your API Tokens")).to_be_visible()
    settle(auth_page)
    yield auth_page
    revoke_qa_tokens(auth_page)


def token_row(page: Page, token_name: str):
    """Карточка токена: от имени вверх до блока с кнопками действий."""
    return page.locator("main").get_by_text(token_name, exact=True).locator(
        "xpath=ancestor::*[.//button[@title='View audit log']][1]")


@pytest.mark.writes_data
def test_create_token_via_form_shown_once(dev: Page):
    token_name = name()
    dev.locator("main").get_by_role("button", name="Create Token").first.click()
    form = modal(dev, "Create API Token")
    form.get_by_placeholder("e.g. Golban Trade Import Bot").fill(token_name)
    # подпись модуля в разметке строчная («hrm»), с большой буквы её делает CSS
    hrm = form.get_by_role("checkbox", name="hrm", exact=True).locator("xpath=ancestor::label/following-sibling::div[1]")
    hrm.get_by_role("checkbox", name="read", exact=True).check()
    form.locator("input[type=number]").fill("30")
    form.locator("select").select_option("30")

    with dev.expect_response(lambda r: r.url.endswith(TOKENS) and r.request.method == "POST") as created:
        form.get_by_role("button", name="Create Token").click()

    check_limit(created.value)
    assert created.value.status == 201
    sent = created.value.request.post_data_json
    assert (sent["name"], sent["scopes"], sent["rate_limit"]) == (token_name, ["hrm.read"], 30)
    data = created.value.json()
    shown_once = is_token(data["token"])
    assert shown_once, "в ответе на создание нет токена bord_…"
    assert data["expires_at"], "срок 30 дней не сохранился"
    done = modal(dev, "API Token Created")
    expect(done).to_contain_text("Copy this token now. You won't be able to see it again.")
    expect(done.get_by_text(data["token"], exact=True)).to_be_visible()
    done.get_by_role("button", name="Done").click()

    card = token_row(dev, token_name)
    expect(card).to_contain_text("30 req/min")
    expect(card).to_contain_text("hrm.read")
    expect(dev.locator("main").get_by_text(data["token"], exact=True)).to_have_count(0)
    listed = next(t for t in api_get(dev, TOKENS).json() if t["id"] == data["id"])
    assert "token" not in listed and listed["is_active"]


def test_token_reads_its_module(shared_token):
    page, token = shared_token

    resp = call(page, "/api/v1/hrm/employees/?skip=0&limit=2", token["token"])

    assert resp.status == 200, resp.status
    body = resp.json()
    # Quick Start обещает {items, total, skip, limit}; hrm отдаёт только items/total (ecom-100161).
    # В assert — только ключи: в items персональные данные сотрудников.
    keys = sorted(body)
    assert {"items", "total"} <= set(keys), keys
    assert len(body["items"]) <= 2
    assert resp.headers["x-ratelimit-limit"] == "1000"
    users = {u["email"] for u in api_get(page, "/api/admin/auth/users").json()}
    assert {e["email"] for e in body["items"]} <= users


def test_read_token_cannot_write(shared_token):
    page, token = shared_token

    resp = call(page, "/api/v1/hrm/employees/", token["token"], method="POST")

    assert resp.status == 403
    assert resp.json()["detail"] == {"error": "insufficient_scope", "message": "Token lacks required scope: hrm.write"}


@pytest.mark.parametrize(
    ("token", "error"),
    [(None, "unauthorized"), ("bord_" + "0" * 40, "invalid_token")],
    ids=["без токена", "чужой токен"],
)
def test_bad_token_unauthorized(dev: Page, token, error):
    resp = call(dev, "/api/v1/hrm/employees/", token)

    assert resp.status == 401
    assert resp.json()["detail"]["error"] == error


def test_token_not_accepted_by_admin_api(shared_token):
    page, token = shared_token

    assert call(page, "/api/admin/auth/users", token["token"]).status == 401


@pytest.mark.writes_data
def test_rate_limit(dev: Page):
    token = create_token(dev, rate_limit=3)

    statuses = [call(dev, "/api/v1/hrm/employees/?limit=1", token["token"]).status for _ in range(4)]

    assert statuses == [200, 200, 200, 429]


@pytest.mark.writes_data
def test_revoke_via_ui(dev: Page):
    token = create_token(dev)
    dev.reload()
    card = token_row(dev, token["name"])

    card.get_by_role("button", name="Revoke token").click()
    dialog = dev.get_by_role("dialog", name=re.compile("Revoke"))
    with dev.expect_response(lambda r: r.url.endswith(f"{TOKENS}{token['id']}") and r.request.method == "DELETE") as revoked:
        dialog.get_by_role("button", name="Да").click()

    assert revoked.value.ok
    expect(card).to_contain_text("Revoked")
    assert call(dev, "/api/v1/hrm/employees/", token["token"]).status == 401


@pytest.mark.writes_data
def test_regenerate_via_ui(dev: Page):
    token = create_token(dev)
    dev.reload()

    token_row(dev, token["name"]).get_by_role("button", name="Regenerate token").click()
    dialog = dev.get_by_role("dialog", name=re.compile("^Regenerate token"))
    expect(dialog).to_contain_text(token["name"])
    expect(dialog).to_contain_text("The old token will stop working immediately.")
    with dev.expect_response(lambda r: f"{TOKENS}{token['id']}" in r.url and r.request.method == "POST") as regenerated:
        dialog.get_by_role("button", name="Да").click()

    assert regenerated.value.ok
    new = regenerated.value.json()["token"]
    changed = is_token(new) and new != token["token"]
    assert changed, "новый токен не выдан"
    expect(modal(dev, "API Token Created")).to_be_visible()
    assert call(dev, "/api/v1/hrm/employees/", token["token"]).status == 401
    assert call(dev, "/api/v1/hrm/employees/", new).status == 200


def test_audit_log_shows_calls(shared_token, dev: Page):
    page, token = shared_token
    assert call(page, "/api/v1/hrm/employees/?limit=1", token["token"]).ok
    dev.reload()

    with dev.expect_response(lambda r: f"{TOKENS}{token['id']}/audit" in r.url) as audit:
        token_row(dev, token["name"]).get_by_role("button", name="View audit log").click()

    calls = [(c["method"], c["path"], c["status_code"]) for c in audit.value.json()]
    assert ("GET", "/v1/hrm/employees/", 200) in calls
    # журнал рисуется под карточкой токена, не внутри неё
    log = dev.locator("main").get_by_text("Recent API Calls").locator("xpath=..")
    expect(log).to_contain_text("/v1/hrm/employees/")


@pytest.mark.parametrize(("module", "path"), DOCUMENTED, ids=[f"{m}{p}" for m, p in DOCUMENTED])
def test_documented_endpoint_answers(shared_token, module, path):
    page, token = shared_token

    resp = call(page, f"/api/v1/{module}{path}?limit=1", token["token"])

    if module == "crm" and resp.status == 502:
        pytest.xfail(CRM_502)
    if resp.status == 404 and (module, path) in MISSING:
        pytest.xfail(DOCS_404)
    assert resp.status == 200, f"GET /api/v1/{module}{path} → {resp.status}"
