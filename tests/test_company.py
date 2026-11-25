"""Блок 4. Компания: app.bord.one/admin/settings/company.

Снято 22.10.2026 (после того как BORD QA вернули из 500, ecom-100148):
- форма читает GET /api/onboarding → company {name, industry, legal_name,
  idno, vat_code, address, phone}, пустые поля приходят null;
- «Сохранить» → PUT /api/onboarding/company: name, industry и только
  изменённые поля; успех → «Сохранено» под формой;
- проверки сервера (422): IDNO — ровно 13 цифр («IDNO — 13 цифр»),
  название — минимум 2 символа; на фронте «Сохранить» неактивна, пока
  в названии меньше 2 непустых символов. Телефон сервер не проверяет;
- отрасль — кастомный listbox поверх скрытого <select> (clinica, retail, …).
  При сохранённом шаблоне карты смена отрасли раскладку не трогает.

Реквизиты общие на организацию: фикстура запоминает их до теста и
возвращает через API в любом исходе.
"""
import time

import pytest
from playwright.sync_api import Page, expect

from helpers.api import api_get, api_put
from helpers.wait import settle
from helpers.ui import screen_field, screen_button

pytestmark = pytest.mark.requires_auth

ONBOARDING = "/api/onboarding"
SAVE = "/api/onboarding/company"
FIELDS = ("name", "industry", "legal_name", "idno", "vat_code", "address", "phone")
INDUSTRIES = {
    "": "Не выбрана",
    "clinica": "Клиники и медицина",
    "retail": "Торговля и розница",
    "logistica": "Логистика и транспорт",
    "constructii": "Строительство",
    "it": "IT и услуги",
    "productie": "Производство",
    "horeca": "Бани, рестораны, гостиницы",  # +3 с 17.11
    "realty": "Недвижимость",
    "lombard": "Ломбарды",
    "other": "Другая отрасль",
}


class CompanyForm:
    def __init__(self, page: Page):
        self.page = page
        main = page.locator("main")
        field = lambda label: main.get_by_text(label, exact=True).locator("xpath=following-sibling::input")
        self.name = field("Название в BORD *")
        # с 02.11 в форме вторая выпадашка — «Страна»; отрасль берём по подписи
        # (с 17.11 подпись «Направление бизнеса», label[for=ob-industry])
        self.industry = main.locator("label[for='ob-industry'] + button[aria-haspopup='listbox']")
        self.legal_name = screen_field(page, "Exemplu SRL")
        self.idno = screen_field(page, "13 цифр")
        self.vat_code = field("Код НДС")
        self.address = field("Адрес")
        self.phone = screen_field(page, "+373 …")
        self.save = screen_button(page, "Сохранить")
        self.saved = main.get_by_text("Сохранено", exact=True)

    def submit(self):
        with self.page.expect_response(lambda r: r.url.endswith(SAVE) and r.request.method == "PUT") as resp:
            self.save.click()
        return resp.value


def company(page: Page) -> dict:
    resp = api_get(page, ONBOARDING)
    assert resp.ok, f"{ONBOARDING} → {resp.status}"
    return resp.json()["company"]


@pytest.fixture
def form(auth_page: Page):
    auth_page.goto("/admin/settings/company")
    # заголовок вкладки с 02.11 всегда «BORD» (баг заведён) — готовность ждём по форме
    expect(auth_page.locator("main").get_by_text("Название в BORD")).to_be_visible(timeout=20_000)
    settle(auth_page)
    # с 08.11 реквизиты (юр. название, НДС, адрес, телефон) свёрнуты под ссылкой —
    # по умолчанию их подтягивает IDNO из госреестра kyc.bord.one
    auth_page.locator("main").get_by_text("Исправить реквизиты вручную", exact=True).click()
    expect(screen_field(auth_page, "Exemplu SRL")).to_be_visible()
    before = company(auth_page)
    yield CompanyForm(auth_page)
    if company(auth_page) != before:
        restored = api_put(auth_page, SAVE, {k: before.get(k) or "" for k in FIELDS})
        assert restored.ok, f"не удалось вернуть реквизиты: {restored.status} {restored.text()}"


# Кейс: Форма показывает данные компании из API
@pytest.mark.smoke
def test_form_shows_company_from_api(form: CompanyForm):
    data = company(form.page)

    expect(form.name).to_have_value(data["name"])
    expect(form.industry).to_have_text(INDUSTRIES[data["industry"] or ""])
    for key in ("legal_name", "idno", "vat_code", "address", "phone"):
        expect(getattr(form, key), key).to_have_value(data[key] or "")


# Кейс: В «Отрасли» все варианты
def test_industry_offers_all_options(form: CompanyForm):
    form.industry.click()

    expect(form.page.get_by_role("option")).to_have_text(list(INDUSTRIES.values()))
    form.page.keyboard.press("Escape")


# Кейс: Реквизиты сохраняются и остаются после перезагрузки
@pytest.mark.writes_data
def test_requisites_saved_and_survive_reload(form: CompanyForm):
    stamp = int(time.time()) % 10**6
    values = {
        "legal_name": f"QA Autotest {stamp} SRL",
        "idno": f"1003600{stamp:06d}",
        "vat_code": f"MD{stamp:07d}",
        "address": f"mun. Chișinău, str. QA {stamp}",
        "phone": "+37369000000",
    }
    for key, value in values.items():
        getattr(form, key).fill(value)

    resp = form.submit()

    assert resp.status == 200, resp.text()
    assert {k: resp.request.post_data_json[k] for k in values} == values
    expect(form.saved).to_be_visible()
    assert {k: company(form.page)[k] for k in values} == values

    form.page.reload()
    settle(form.page)
    form.page.locator("main").get_by_text("Исправить реквизиты вручную", exact=True).click()
    for key, value in values.items():
        expect(getattr(form, key), key).to_have_value(value)


# Кейс: Отрасль сохраняется
@pytest.mark.writes_data
def test_industry_saved(form: CompanyForm):
    current = company(form.page)["industry"]
    key = "it" if current != "it" else "retail"

    form.industry.click()
    form.page.get_by_role("option", name=INDUSTRIES[key], exact=True).click()
    resp = form.submit()

    assert resp.status == 200 and resp.request.post_data_json["industry"] == key
    assert company(form.page)["industry"] == key
    form.page.reload()
    expect(form.industry).to_have_text(INDUSTRIES[key])


# Кейс: IDNO должен быть из 13 цифр
@pytest.mark.writes_data
@pytest.mark.parametrize("idno", ["123", "100360001234", "10036000123456"],
                         ids=["3 цифры", "12 цифр", "14 цифр"])
def test_idno_must_be_13_digits(form: CompanyForm, idno):
    before = company(form.page)["idno"]
    form.idno.fill(idno)

    resp = form.submit()

    assert resp.status == 422, f"IDNO {idno!r} принят: {resp.status}"
    expect(form.page.locator("main").get_by_text("IDNO — 13 цифр")).to_be_visible()
    expect(form.saved).to_have_count(0)
    assert company(form.page)["idno"] == before


# Кейс: Буквы в IDNO — поиск фирмы, сохранить нельзя до выбора из списка
def test_idno_letters_is_company_search(form: CompanyForm):
    """С 08.11 в поле IDNO можно вводить название фирмы: это поиск по госреестру,
    «Сохранить» неактивна, пока фирма не выбрана из списка."""
    before = company(form.page)["idno"]
    form.idno.fill("abcdefghijklm")

    expect(form.page.locator("main").get_by_text("Выберите компанию из списка")).to_be_visible()
    expect(form.save).to_be_disabled()
    assert company(form.page)["idno"] == before


# Кейс: Слишком короткое название не даёт сохранить
@pytest.mark.parametrize("name", ["", "Q", "   "], ids=["пусто", "1 символ", "пробелы"])
def test_short_name_blocks_save(form: CompanyForm, name):
    form.name.fill(name)

    expect(form.save).to_be_disabled()
    form.name.fill("BORD QA")
    expect(form.save).to_be_enabled()
