"""Блок 70. HRM — права учётки «Сотрудник» (qaColleague, lemido4693, id 1767) и румынский язык экранов 58–64.

Снято 03.11.2026:
- qaColleague входит в HRM по той же SSO-куке; видит ВСЁ меню (20 пунктов) и открывает экраны
  администрирования, но данных нет: «Сотрудники» — «Нет сотрудников» (/employees пусто, даже себя —
  /employees/1767 → 404), «Компенсация» — «Нет грейдов» (API — 403 HRM_VIEW_COMPENSATION); запись в
  справочники — 403 «Permission denied: Permission.HRM_MANAGE» (спорно, что меню и кнопки не скрыты —
  в README);
- «Повышение квалификации» у сотрудника — «Здесь можно попросить о повышении зарплаты…», заявку он
  подаёт только за себя (rights.pick_employee = false); владелец её видит и может решать, тест её удаляет;
- NEXT_LOCALE=ro: заголовки и вкладки экранов 58–64 по-румынски; кириллица остаётся только в данных.
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (HRM, QA_PREFIX, pick_raise_manager, field, hrm_delete, hrm_get, hrm_json, hrm_me, hrm_post, items, open_hrm,
                         screen_title, sidebar, stamp)

pytestmark = pytest.mark.requires_auth

COLLEAGUE_ID = 1767
RAISE_NOTIFICATION = "Новая заявка на повышение зарплаты"


def main(page: Page):
    return page.locator("main")


# ───────────────────────── права «Сотрудник» ─────────────────────────

# Кейс: Сотрудник (qaColleague) — HRM открывается, но чужих данных нет: список сотрудников пуст, компенсация 403, справочники не пишутся
def test_colleague_read_only(colleague_page: Page):
    me = hrm_me(colleague_page)
    assert me["id"] == COLLEAGUE_ID, me
    open_hrm(colleague_page, "/admin")
    expect(sidebar(colleague_page).get_by_role("link", name="Сотрудники", exact=True)).to_be_visible()
    assert items(hrm_json(hrm_get(colleague_page, "/employees", {"status_filter": "all"}))) == []
    open_hrm(colleague_page, "/admin/employees")
    expect(main(colleague_page)).to_contain_text("Нет сотрудников")
    comp = hrm_get(colleague_page, "/compensation/records")
    assert comp.status == 403 and "HRM_VIEW_COMPENSATION" in comp.text(), comp.text()
    for path, body in (("/org/workplaces", {"name": f"{QA_PREFIX} Коллега место {stamp()}"}),
                       ("/legal-entities", {"name": f"{QA_PREFIX} Коллега юрлицо {stamp()}"}),
                       ("/discipline/violation-types", {"code": "qa_colleague", "name": f"{QA_PREFIX} Коллега тип"})):
        r = hrm_post(colleague_page, path, body)
        assert r.status == 403 and "HRM_MANAGE" in r.text(), f"{path}: {r.status} {r.text()[:150]}"


# Кейс: Сотрудник подаёт заявку на повышение только за себя; владелец видит её и может решать
@pytest.mark.writes_data
def test_colleague_own_raise_request(colleague_page: Page, auth_page: Page):
    rights = hrm_json(hrm_get(colleague_page, "/raise-requests"))["rights"]
    assert rights == {"view_all": False, "pick_employee": False, "delete": False, "decide": False}, rights
    open_hrm(colleague_page, "/admin/raise-requests")
    expect(main(colleague_page)).to_contain_text("Здесь можно попросить о повышении зарплаты")
    colleague_page.locator("header button:visible, main button:visible").filter(has_text="Новая заявка").first.click()
    box = colleague_page.locator("div.fixed.inset-0:visible").filter(has_text="Заявка на повышение").last
    expect(box.get_by_text("Сотрудник", exact=True)).to_have_count(0)  # выбора сотрудника нет — только за себя
    achievements = f"{QA_PREFIX} коллега закрыл квартал {stamp()}"
    pick_raise_manager(colleague_page, box, "maxim.turcan1")  # руководитель — тест-учётка qaUser
    field(box, "Текущая ставка / зарплата").fill("10000")
    field(box, "Желаемая ставка / зарплата").fill("11000")
    field(box, "Аргументация", starts=True).fill(achievements)
    with colleague_page.expect_response(lambda r: r.url.endswith("/raise-requests") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Отправить", exact=True).click()
    assert resp.value.ok, resp.value.text()
    rr = resp.value.json()
    try:
        assert (rr["employee_id"], rr["status"]) == (COLLEAGUE_ID, "new"), rr
        mine = items(hrm_json(hrm_get(colleague_page, "/raise-requests")))
        assert [r["id"] for r in mine] == [rr["id"]], mine  # видит только свою
        owner = next(r for r in items(hrm_json(hrm_get(auth_page, "/raise-requests"))) if r["id"] == rr["id"])
        assert owner["can_decide"] is True and owner["achievements"] == achievements, owner
        assert hrm_delete(colleague_page, f"/raise-requests/{rr['id']}").status == 403  # удалить сам не может
    finally:
        hrm_delete(auth_page, f"/raise-requests/{rr['id']}")


# ───────────────────────── румынский ─────────────────────────

RO_SCREENS = [
    ("/admin/compensation", "Compensații", ["Grile", "Înregistrări", "Pe locuri de muncă", "Beneficii", "Înscrieri"]),
    ("/admin/raise-requests", "Creșterea calificării", []),
    ("/admin/discipline", "Disciplină", ["Perioadă de probă", "Încălcări", "Tipuri de încălcări", "Audit"]),
    ("/admin/recruitment/vacancies", "Recrutare", ["Ciornă", "Deschisă", "În pauză", "Închisă", "Anulată"]),
    ("/admin/recruitment/sources", "Surse de candidați", []),
    ("/admin/onboarding", "Onboarding", ["Procese active", "Șabloane"]),
    ("/admin/skills", "Competențe", ["Catalog", "Matrice", "Certificate", "Progres", "Trasee de carieră"]),
    ("/admin/training", "Instruire", ["Prezentare", "Programe", "Înregistrări", "Rețineri", "Obligații"]),
    ("/admin/doc-templates", "Documente", ["Șabloane", "Generare", "Pachete de documente", "Registre de numerotare"]),
    ("/admin/reports", "Rapoarte", ["Privire generală", "Zile de naștere", "Raport pe registre", "Constructor de rapoarte"]),
    ("/admin/notifications", "Notificări", ["Notificări", "Reguli", "Setări"]),
    ("/admin/audit", "Jurnalul acțiunilor", []),
]


@pytest.fixture
def ro(auth_page: Page):
    auth_page.context.add_cookies([{"name": "NEXT_LOCALE", "value": "ro", "url": HRM}])
    yield auth_page
    auth_page.context.add_cookies([{"name": "NEXT_LOCALE", "value": "ru", "url": HRM}])


# Кейс: Română — заголовок и вкладки экрана переведены, в элементах управления нет кириллицы
@pytest.mark.parametrize("path,title,tabs", RO_SCREENS, ids=[p for p, _, _ in RO_SCREENS])
def test_screen_in_romanian(ro: Page, path, title, tabs):
    open_hrm(ro, path)
    expect(screen_title(ro)).to_have_attribute("title", title)
    for name in tabs:
        expect(main(ro).get_by_role("button", name=name, exact=True).first).to_be_visible()
    labels = main(ro).locator("button, th, label, h1, h2, h3").evaluate_all(
        "els => els.filter(e => e.offsetParent && e.children.length <= 2).map(e => e.innerText.trim())")
    cyrillic = [t for t in labels if re.search(r"[А-Яа-яЁё]", t) and not t.startswith(QA_PREFIX)
                and not re.search(r"QA Autotest|\[QA-AUTO\]|Акт (выдачи|приёма) ТМЦ|Системный шаблон", t)]
    # bord-179: уведомление о заявке на повышение не переводится (остальные типы переведены)
    if cyrillic and all(t.startswith(RAISE_NOTIFICATION) for t in cyrillic):
        pytest.xfail(f"bord-179: «{RAISE_NOTIFICATION} …» в Română не переведено")
    assert not cyrillic, f"кириллица в интерфейсе {path}: {cyrillic[:5]}"
