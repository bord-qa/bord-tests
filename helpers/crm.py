"""CRM (crm.bord.one): вход по SSO и запросы к API CRM от имени тест-учётки.

Как устроено (снято 27.10.2026):
- CRM — отдельное Next.js-приложение. Своего пароля нет: страница зовёт
  GET app.bord.one/api/admin/sso/exchange?module=crm с кукой bord_sso
  (её ставит вход в app.bord.one) и получает {token, user, current_org, …};
- токен CRM живёт только в памяти страницы (в localStorage его нет), API
  ходит в crm.bord.one/api/crm/* с `Authorization: Bearer <token>`;
- схема всего API — crm.bord.one/api/crm/openapi.json (≈690 путей), там и
  то, чего нет в каталоге MCP BORD.

Тестам токен достаём тем же обменом через page.request — он делит куки с
контекстом страницы, так что API видит ту же организацию, что и экран.
Ответы API — эталон для сверки экранов и способ прибрать за тестом.
"""
import re

from playwright.sync_api import APIResponse, Page, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

CRM = "https://crm.bord.one"
EXCHANGE = "https://app.bord.one/api/admin/sso/exchange?module=crm"
# Ответственным в тестах ставим только тест-учётку: в команде CRM BORD QA
# состоит живой человек (turcan.maxim@alsodev.com, ID 1244 — на нём стартовые
# примеры), назначение на него уйдёт ему уведомлением.
QA_PREFIX = "QA Autotest"
QA_ORG = 42  # организация BORD QA


def _sso(page: Page) -> dict:
    """Ответ SSO-обмена {token, user, …} — один на страницу: обмен дёргается
    в каждом хелпере, и частые повторы иногда отдают ответ без user."""
    cached = getattr(page, "_crm_sso", None)
    if cached:
        return cached
    resp = page.request.get(EXCHANGE)
    assert resp.ok, f"SSO-обмен для CRM вернул {resp.status}: {resp.text()[:200]}"
    data = resp.json()
    assert data.get("token") and data.get("user"), f"SSO-обмен без token/user: {str(data)[:200]}"
    page._crm_sso = data
    return data


def crm_token(page: Page) -> str:
    return _sso(page)["token"]


def crm_me(page: Page) -> dict:
    """Кто вошёл: user из SSO-обмена (id совпадает с id сотрудника в CRM)."""
    return _sso(page)["user"]


def _headers(page: Page) -> dict:
    return {"Authorization": f"Bearer {crm_token(page)}"}


def crm_get(page: Page, path: str, params: dict | None = None) -> APIResponse:
    return page.request.get(CRM + "/api/crm" + path, headers=_headers(page), params=params)


def crm_post(page: Page, path: str, data: dict | list) -> APIResponse:
    return page.request.post(CRM + "/api/crm" + path, headers=_headers(page), data=data)


def crm_put(page: Page, path: str, data: dict) -> APIResponse:
    return page.request.put(CRM + "/api/crm" + path, headers=_headers(page), data=data)


def crm_patch(page: Page, path: str, data: dict) -> APIResponse:
    return page.request.patch(CRM + "/api/crm" + path, headers=_headers(page), data=data)


def crm_delete(page: Page, path: str) -> APIResponse:
    return page.request.delete(CRM + "/api/crm" + path, headers=_headers(page))


def plugins_on(browser, state, context_args, *keys):
    """Включить плагины на время блока и вернуть как было — для module-фикстуры
    (`yield from plugins_on(...)`).

    В BORD QA плагины выключены, а test_crm_menu сверяет меню дословно: включённый
    навсегда плагин добавит пункты и уронит его. Организация проверяется перед
    записью: переключатель действует на всю организацию (разрешено только в BORD QA).
    """
    ctx = browser.new_context(**context_args, storage_state=state)
    page = ctx.new_page()
    org = _sso(page)["current_org"]
    assert org["id"] == QA_ORG, f"плагины включаем только в BORD QA, а вошли в {org}"
    state_now = {p["plugin_key"]: p["is_enabled"] for p in crm_get(page, "/plugins/").json()}
    turned = [k for k in keys if not state_now[k]]
    for key in turned:
        resp = crm_put(page, f"/plugins/{key}", {"is_enabled": True})
        assert resp.ok, f"плагин {key} не включился: {resp.status} {resp.text()[:200]}"
    yield
    page._crm_sso = None  # блок идёт дольше, чем живёт токен
    left_on = []
    for key in turned:
        resp = crm_put(page, f"/plugins/{key}", {"is_enabled": False})
        if not resp.ok:  # молча оставленный плагин потом роняет test_crm_menu лишними пунктами меню
            left_on.append(f"{key}: {resp.status} {resp.text()[:200]}")
    ctx.close()
    assert not left_on, "плагины не выключились после блока:\n" + "\n".join(left_on)


def sidebar(page: Page):
    return page.locator("aside")


def crm_ready(page: Page, timeout: float = 20_000):
    """Экран CRM готов: заголовок вкладки «<Раздел> · BORD CRM».

    Его ставит клиент после гидрации (до неё — просто «BORD CRM»). Ждать
    `networkidle` здесь бесполезно: у CRM всегда открыт поток
    /api/crm/events/stream, и тишины в сети не бывает — settle() просто
    выжидал бы свой таймаут.
    """
    expect(page).to_have_title(re.compile(r". · BORD CRM$"), timeout=timeout)


def until_loaded(page: Page, action, loaded, attempts: int = 3):
    """Сделать action (переход, клик) и дождаться УСПЕШНОГО ответа loaded(r).

    API CRM режет всплески запросов (429): тест сам дёргает API, следом экран
    шлёт свои запросы — и список полей или правил получает 429 и остаётся
    старым или пустым, сам запрос экран не повторяет. Поэтому при неудаче
    пауза и повтор action с начала (action должен открывать экран заново)."""
    for attempt in range(attempts):
        try:
            with page.expect_response(lambda r: loaded(r) and r.ok, timeout=10_000) as resp:
                action()
            return resp.value
        except PlaywrightTimeoutError:
            page.wait_for_timeout(3_000 * (attempt + 1))
    raise AssertionError(f"экран {attempts} раза не получил данные (429?)")


def open_crm(page: Page, path: str = "/admin/crm/reports"):
    """Открыть раздел CRM и дождаться интерфейса организации (SSO прошёл)."""
    page.goto(CRM + path)
    expect(sidebar(page).get_by_role("link", name="Сделки", exact=True)).to_be_visible(timeout=20_000)
    crm_ready(page)
    return page
