"""HRM (hrm.bord.one): вход по SSO и запросы к API HRM от имени тест-учётки.

Как устроено (снято 02.11.2026):
- HRM — отдельное Next.js-приложение, вход как у CRM: страница зовёт
  GET app.bord.one/api/admin/sso/exchange?module=hrm с кукой bord_sso и получает
  {token, user, current_org}; тест-учётка в BORD QA — superuser, тариф enterprise;
- API — hrm.bord.one/api/hrm/* с `Authorization: Bearer <token>`, схема —
  hrm.bord.one/api/openapi.json («BORD HRM API», 413 путей; пути в схеме с /hrm);
- заголовок вкладки «<Экран> — BORD HRM» ставится после загрузки (до этого «BORD HRM»)
  и иногда так и застревает на «BORD HRM» — готовность ждём по заголовку экрана в
  шапке (screen_title); язык интерфейса — кука NEXT_LOCALE.

Сотрудник HRM — это участник организации BORD (id сотрудника = id пользователя):
созданный тестом человек сразу виден в «Пользователях» app.bord.one, в CRM и
Facturi. Поэтому свои сотрудники — только с префиксом QA_PREFIX, без email, и
удаляются полностью (cascade, вместе с учёткой SSO) в любом исходе.
Не трогаем: живого «Maxim Țurcan» (1244), служебные учётки bodeho2831 (1748) и
lemido4693 (1767), записи ручных проверок «[QA-AUTO]» / «[QA-RO]».
"""
import datetime as dt
import re

from playwright.sync_api import APIResponse, Page, expect

HRM = "https://hrm.bord.one"
EXCHANGE = "https://app.bord.one/api/admin/sso/exchange?module=hrm"
QA_PREFIX = "QA Autotest"
QA_ORG = 42  # организация BORD QA
ME = 1717  # тест-учётка
FOREIGN_PEOPLE = {1244, 1748, 1767}  # живой человек и служебные учётки — не трогаем
# С 08.11 заявке на повышение нужен руководитель (manager_ids, обязательно): ему уходит письмо,
# уведомление и напоминание раз в 3 дня. Адресуем только тест-учётки: от qaUser — коллеге
# lemido4693 (qaColleague, 1767), от коллеги — qaUser. Живого Maxim Țurcan (1244) не выбирать.
RAISE_MANAGER = 1767
RAISE_MANAGER_NAME = "lemido4693"
READY_TITLE = re.compile(r".+ — BORD HRM$")


def _sso(page: Page) -> dict:
    """Ответ SSO-обмена {token, user, current_org} — один на страницу."""
    cached = getattr(page, "_hrm_sso", None)
    if cached:
        return cached
    resp = page.request.get(EXCHANGE)
    assert resp.ok, f"SSO-обмен для HRM вернул {resp.status}: {resp.text()[:200]}"
    data = resp.json()
    assert data.get("token") and data.get("user"), f"SSO-обмен без token/user: {str(data)[:200]}"
    assert data["current_org"]["id"] == QA_ORG, f"вошли не в BORD QA: {data['current_org']}"
    page._hrm_sso = data
    return data


def hrm_me(page: Page) -> dict:
    return _sso(page)["user"]


def hrm_org(page: Page) -> dict:
    return _sso(page)["current_org"]


def _headers(page: Page) -> dict:
    return {"Authorization": f"Bearer {_sso(page)['token']}"}


def hrm_get(page: Page, path: str, params: dict | None = None) -> APIResponse:
    return page.request.get(HRM + "/api/hrm" + path, headers=_headers(page), params=params)


def hrm_post(page: Page, path: str, data=None) -> APIResponse:
    return page.request.post(HRM + "/api/hrm" + path, headers=_headers(page), data=data)


def hrm_put(page: Page, path: str, data=None) -> APIResponse:
    return page.request.put(HRM + "/api/hrm" + path, headers=_headers(page), data=data)


def hrm_patch(page: Page, path: str, data=None) -> APIResponse:
    return page.request.patch(HRM + "/api/hrm" + path, headers=_headers(page), data=data)


def hrm_delete(page: Page, path: str, params: dict | None = None) -> APIResponse:
    return page.request.delete(HRM + "/api/hrm" + path, headers=_headers(page), params=params)


def hrm_json(resp: APIResponse):
    assert resp.ok, f"{resp.url} → {resp.status}: {resp.text()[:300]}"
    return resp.json()


def items(data):
    """Списки HRM приходят то массивом, то {items: [...]}."""
    return data["items"] if isinstance(data, dict) and "items" in data else data


def sidebar(page: Page):
    return page.locator("aside")


def screen_title(page: Page):
    """Заголовок экрана в шапке — span[title="<Экран>"]; шапка продублирована
    (десктоп / мобильная), видимая — одна."""
    return page.locator("header span[title]:visible").first


def open_hrm(page: Page, path: str = "/admin") -> Page:
    """Открыть экран HRM и дождаться, пока он отрисуется под организацией.

    Готовность — заголовок экрана в шапке, а не вкладки: заголовок вкладки
    «<Экран> — BORD HRM» примерно в 1 переходе из 10 застревает на «BORD HRM»."""
    page.goto(HRM + path)
    assert "/login" not in page.url, f"HRM попросил войти: {page.url}"
    expect(screen_title(page)).to_be_visible(timeout=20_000)
    return page


def stamp() -> str:
    """Уникальный хвост для названий тестовых записей (без «:» — годится и в имя файла)."""
    return dt.datetime.now().strftime("%d.%m %H%M%S%f")[:-3]


# ───────────────────────── свои данные ─────────────────────────

def create_legal_entity(page: Page, name: str | None = None) -> dict:
    return hrm_json(hrm_post(page, "/legal-entities", {"name": name or f"{QA_PREFIX} Юрлицо {stamp()}"}))


def create_employee(page: Page, legal_entity_id: int, first_name: str | None = None, **extra) -> dict:
    """Свой сотрудник — без email (никаких приглашений), на своём юрлице."""
    body = {"telegram_first_name": first_name or f"{QA_PREFIX} {stamp()}", "legal_entity_id": legal_entity_id,
            "hire_date": str(dt.date.today()), **extra}
    return hrm_json(hrm_post(page, "/employees", body))


def delete_employee(page: Page, employee_id: int) -> APIResponse:
    """Полное удаление со связанными записями (и учёткой SSO) — только своих."""
    assert employee_id not in FOREIGN_PEOPLE and employee_id != ME, f"чужой сотрудник {employee_id}"
    return hrm_delete(page, f"/employees/{employee_id}", {"cascade": "true"})


def clean_up(page: Page):
    """Убрать всё, что оставили упавшие тесты: своих сотрудников и юрлица с префиксом."""
    for emp in items(hrm_json(hrm_get(page, "/employees", {"status_filter": "all", "limit": 1000}))):
        name = " ".join(filter(None, [emp.get("telegram_first_name") or emp.get("name"), emp.get("last_name")]))
        if name.startswith(QA_PREFIX) and emp["id"] not in FOREIGN_PEOPLE | {ME}:
            delete_employee(page, emp["id"])
    for le in items(hrm_json(hrm_get(page, "/legal-entities"))):
        if le["name"].startswith(QA_PREFIX):
            hrm_delete(page, f"/legal-entities/{le['id']}")


def field(box, label: str, starts: bool = False):
    """Поле формы по подписи: label без for, поле — следующий соседний input/textarea/кнопка-выпадашка.
    starts=True — подпись начинается с label («Родительский отдел — отделы выбранной компании»)."""
    cond = f"starts-with(normalize-space(.), '{label}')" if starts else f"normalize-space(.)='{label}'"
    return box.locator(f"xpath=.//label[{cond}]/following-sibling::*[self::input or self::textarea "
                       f"or self::button or self::div][1]").first


def header_button(page: Page, name: str):
    """Кнопка действия в шапке экрана («Добавить», «По проектам», «Скачать Excel») —
    шапка продублирована (десктоп / мобильная), берём видимую."""
    return page.locator("header button:visible").filter(has_text=re.compile(rf"^\s*{re.escape(name)}\s*$")).first


def date_field(box, label: str):
    """Поле даты: после подписи — span с input[type=date] внутри (или сам input)."""
    return box.locator(f"xpath=.//label[normalize-space(.)='{label}']/following-sibling::*[1]"
                       f"/descendant-or-self::input[1]").first


def dropdown(page: Page, box, label: str, option: str, starts: bool = False):
    """Выпадашка формы HRM: кнопка после подписи → role=option. Внизу длинной формы
    первый клик уходит на прокрутку и список не раскрывается — тогда кликаем ещё раз."""
    button = field(box, label, starts)
    opt = page.get_by_role("option", name=option, exact=True)
    for _ in range(3):
        button.scroll_into_view_if_needed()
        button.click()
        try:
            expect(opt).to_be_visible(timeout=2_000)
            break
        except AssertionError:
            if page.locator("[role=listbox]:visible").count():  # список открыт, но такого варианта нет —
                options = page.locator("[role=option]:visible").all_inner_texts()  # дальше клики упрутся в подложку
                page.keyboard.press("Escape")
                raise AssertionError(f"в «{label}» нет варианта «{option}», есть: {options}")
    opt.click()


def pick_raise_manager(page: Page, box, name: str = RAISE_MANAGER_NAME):
    """«Руководитель (можно несколько) *» в заявке на повышение — мультивыбор с поиском."""
    combo = field(box, "Руководитель", starts=True)
    combo.click()
    page.get_by_role("option", name=name, exact=True).click()
    page.keyboard.press("Escape")
    expect(combo).to_contain_text(name)
