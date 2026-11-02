"""Facturi (app.facturi.md): вход через BORD и запросы к API от имени тест-учётки.

Как устроено (снято 02.11.2026):
- Facturi — отдельное Next.js-приложение на своём домене, кука bord_sso до него
  не доходит. Даже при живой сессии BORD /admin/* уводит на
  /login?next=<путь>, а войти без пароля можно кнопкой «BORD»
  (data-testid=bord-login): /auth/login/bord → app.bord.one/oauth/authorize
  (client_id=facturi) → /auth/callback → обратно на next;
- сессия — httpOnly-куки facturi_session (~8 ч), facturi_refresh, fin_session
  на app.facturi.md; localStorage пустой. У тест-учётки в Facturi нет ни
  пароля, ни email (has_password=false, bord_linked=true) — вход только через BORD;
- API — app.facturi.md/api/* по тем же кукам (гость → 401), схема —
  /api/openapi.json («BORD Finance API», ≈730 путей; пути в схеме без /api);
- заголовок вкладки «<Экран> — Facturi» ставится после загрузки данных
  пользователя, до этого просто «Facturi» — по нему и ждём готовность экрана.

Не трогаем: «Выйти со всех устройств», «Скрыть» у «С чего начать» (прячет
карточку всей организации), отправку в e-Factura (СФС), реальные платёжные шлюзы.
Записи «QA Buyer …», «QA MDL», INV-2026-0001 и акты 30.10 завёл не автотест —
их не меняем и не удаляем.
"""
import datetime as dt
import re

from playwright.sync_api import APIResponse, Browser, Page, expect

FACTURI = "https://app.facturi.md"
QA_PREFIX = "QA Autotest"
QA_ORG = 42  # организация BORD QA
READY_TITLE = re.compile(r".+ — Facturi$")


def bord_login(page: Page, path: str = "/admin"):
    """Войти в Facturi кнопкой «BORD» (нужна живая сессия app.bord.one в контексте)."""
    page.goto(f"{FACTURI}/login?next={path.replace('/', '%2F')}")
    button = page.get_by_test_id("bord-login")
    expect(button).to_be_visible(timeout=20_000)
    button.click()
    page.wait_for_url(FACTURI + path, timeout=30_000)
    expect(page).to_have_title(READY_TITLE, timeout=20_000)


def make_state(browser: Browser, context_args: dict, bord_state: str, path: str) -> str:
    """Сохранённая сессия Facturi поверх сессии BORD — один вход на прогон."""
    ctx = browser.new_context(**context_args, storage_state=bord_state)
    page = ctx.new_page()
    bord_login(page)
    ctx.storage_state(path=path)
    ctx.close()
    return path


def open_fx(page: Page, path: str = "/admin") -> Page:
    """Открыть экран Facturi и дождаться, пока он отрисуется под пользователем."""
    page.goto(FACTURI + path)
    assert "/login" not in page.url, f"Facturi попросил войти: {page.url}"
    expect(page).to_have_title(READY_TITLE, timeout=20_000)
    return page


def fx_get(page: Page, path: str, params: dict | None = None) -> APIResponse:
    return page.request.get(f"{FACTURI}/api{path}", params=params)


def fx_post(page: Page, path: str, data=None) -> APIResponse:
    return page.request.post(f"{FACTURI}/api{path}", data=data)


def fx_put(page: Page, path: str, data=None) -> APIResponse:
    return page.request.put(f"{FACTURI}/api{path}", data=data)


def fx_patch(page: Page, path: str, data=None) -> APIResponse:
    return page.request.patch(f"{FACTURI}/api{path}", data=data)


def fx_delete(page: Page, path: str) -> APIResponse:
    return page.request.delete(f"{FACTURI}/api{path}")


def fx_json(resp: APIResponse):
    assert resp.ok, f"{resp.url} → {resp.status}: {resp.text()[:300]}"
    return resp.json()


def fx_me(page: Page) -> dict:
    """Кто вошёл: {user, current_org, role, …} из /api/auth/me (без token)."""
    me = fx_json(fx_get(page, "/auth/me"))
    me.pop("token", None)
    return me


def user_menu(page: Page, name: str):
    """Кнопка пользователя в шапке (открывает меню: сессии, профиль, «Выйти»)."""
    return page.locator("header.sticky").get_by_role("button", name=name)


def modal(page: Page, title: str):
    """Окно формы: div.fixed.inset-0 с заголовком (не role=dialog, Esc не закрывает)."""
    return page.locator("div.fixed.inset-0").filter(
        has=page.get_by_role("heading", name=title, exact=True)).last


def _label(label: str, exact: bool) -> str:
    """xpath подписи: точная («Название» или «Название *») либо по началу строки."""
    if exact:
        return f"label[normalize-space(.)='{label}' or normalize-space(.)='{label} *']"
    return f"label[starts-with(normalize-space(.), '{label}')]"


def field(box, label: str, exact: bool = True):
    """Поле по подписи: label без for, поле — следующий сосед («Название *» → «Название»)."""
    return box.locator(f"xpath=.//{_label(label, exact)}/following-sibling::*[self::input or self::textarea][1]")


def listbox(box, label: str, exact: bool = True):
    """Кнопка выпадашки после подписи — сама соседка или внутри соседней обёртки."""
    return box.locator(f"xpath=.//{_label(label, exact)}"
                       "/following-sibling::*[1]/descendant-or-self::button[@aria-haspopup='listbox']").first


def pick(page: Page, box, label: str, option, exact: bool = True):
    """Выпадашка формы: кнопка aria-haspopup=listbox → role=option (текст целиком или regex)."""
    listbox(box, label, exact).click()
    if isinstance(option, str):
        page.get_by_role("option", name=option, exact=True).click()
    else:
        page.get_by_role("option").filter(has_text=option).first.click()


def tick(box, label: str, value: bool = True):
    """Галочка внутри своей подписи («Активен», «Показывать в документах»)."""
    box.locator("label").filter(has_text=re.compile(f"^{re.escape(label)}")).locator(
        "input[type=checkbox]").set_checked(value)


def confirm(page: Page, answer: str = "Да"):
    """Окно подтверждения Facturi ([data-bord-dialogs], role=dialog)."""
    dlg = page.locator("[data-bord-dialogs]").get_by_role("dialog")
    expect(dlg).to_be_visible()
    text = dlg.inner_text()
    dlg.get_by_role("button", name=answer, exact=True).click()
    expect(dlg).to_have_count(0)
    return text


def maybe_confirm(page: Page, timeout: float = 2_000):
    """Если действие спросило подтверждение — ответить «Да» / «Удалить»; окно ждём,
    а не проверяем мгновенным count() (оно рисуется чуть позже клика)."""
    dlg = page.locator("[data-bord-dialogs]").get_by_role("dialog")
    try:
        dlg.wait_for(timeout=timeout)
    except Exception:  # noqa: BLE001 — подтверждения нет
        return None
    text = dlg.inner_text()
    yes = dlg.get_by_role("button", name=re.compile("^(Да|Удалить|Подтвердить)$"))
    (yes if yes.count() else dlg.get_by_role("button").last).first.click()
    expect(dlg).to_have_count(0)
    return text


def stamp() -> str:
    """Уникальный хвост для названий тестовых записей."""
    import time
    return time.strftime("%d.%m %H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"


def unique_idno() -> str:
    """13 цифр, уникальные по времени: ИНН в Facturi не уникален, но дубли попадают в «Дубли»."""
    import time
    return "19" + str(time.time_ns() // 1000)[-11:]


RAIL_GROUPS = ["Обзор", "Отчёты", "Деньги", "Продажи", "Зарплата", "Справочники",
               "Шаблоны", "Интеграции", "Настройки"]


def drawer(page: Page):
    """Мобильное меню (aside) — на десктопе скрыто, но держит в DOM все пункты."""
    return page.locator("aside")


def rail_open(page: Page, group: str):
    """Десктоп: кнопка группы на левом рельсе открывает панель со ссылками группы."""
    page.locator("button").filter(has_text=re.compile(f"^{group}$")).locator("visible=true").first.click()
    panel = page.locator("div.fixed[role=menu]")
    expect(panel).to_be_visible()
    return panel


class Books:
    """Свой маленький учёт за текущий месяц: статьи выручки (P&L 1100) и расхода (2200),
    бизнес-линия, клиент, банк. счёт, отправленная накладная 500 (+НДС 0), завершённые
    приход 500 и расход 120. cleanup() всё отменяет / удаляет / выключает (блоки 44, 48)."""

    def __init__(self, page: Page):
        self.page = page
        tag = stamp()
        today = dt.date.today()
        self.tag = tag
        self.income_cat = fx_json(fx_post(page, "/categories/", {"name": f"{QA_PREFIX} Выручка {tag}", "type": "income", "pls_type": 1100}))
        self.expense_cat = fx_json(fx_post(page, "/categories/", {"name": f"{QA_PREFIX} Расход {tag}", "type": "expense", "pls_type": 2200}))
        self.line = fx_json(fx_post(page, "/business-lines/", {"name": f"{QA_PREFIX} Отчёт {tag}"}))
        self.cp = fx_json(fx_post(page, "/counterparties/", {"name": f"{QA_PREFIX} Отчёты {tag}", "type": "customer"}))
        self.acc = fx_json(fx_post(page, "/bank-accounts/", {"name": f"{QA_PREFIX} Отчёты {tag}", "type": "bank", "currency": "MDL"}))
        self.invoice = fx_json(fx_post(page, "/invoices/", {
            "counterparty_id": self.cp["id"], "type": "receivable", "issue_date": str(today), "due_date": str(today + dt.timedelta(days=10)),
            "currency": "MDL", "category_id": self.income_cat["id"], "business_line_id": self.line["id"],
            "lines": [{"description": f"{QA_PREFIX} отчёты", "quantity": 1, "unit_price": 500, "category_id": self.income_cat["id"]}]}))
        fx_json(fx_post(page, f"/invoices/{self.invoice['id']}/send", {}))
        self.payments = []
        for kind, amount, cat in (("income", 500, self.income_cat), ("expense", 120, self.expense_cat)):
            pay = fx_json(fx_post(page, "/payments/", {
                "type": kind, "amount": amount, "currency": "MDL", "date": str(today), "bank_account_id": self.acc["id"],
                "counterparty_id": self.cp["id"] if kind == "income" else None, "category_id": cat["id"],
                "business_line_id": self.line["id"], "description": f"{QA_PREFIX} отчёты {tag}"}))
            fx_json(fx_post(page, f"/payments/{pay['id']}/complete", {}))
            self.payments.append(pay)

    def cleanup(self):
        p = self.page
        for pay in self.payments:
            fx_delete(p, f"/payments/{pay['id']}")
        fx_post(p, f"/invoices/{self.invoice['id']}/cancel", {})
        fx_delete(p, f"/bank-accounts/{self.acc['id']}")
        for cat in (self.income_cat, self.expense_cat):
            fx_put(p, f"/categories/{cat['id']}", {"is_active": False})
        fx_put(p, f"/business-lines/{self.line['id']}", {"is_active": False})
        fx_put(p, f"/counterparties/{self.cp['id']}", {"is_active": False})
