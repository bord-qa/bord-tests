"""Блок 52. HRM — оргструктура (/admin/orgchart).

Снято 02.11.2026 (в видимом MCP-браузере):
- схема-холст: вид «По департаментам» / «По проектам», «Добавить отдел», «Настройки», «Печать»;
  фильтры «Все статусы», «Все юрлица»; блоки по юрлицам («<Юрлицо> N отд. · M чел.») и
  «Без юрлица», в блоке — узлы отделов и «БЕЗ ОТДЕЛА (N)»; данные = GET /org/tree
  ({departments: [...дерево...], unassigned: [...]}), по проектам — /org/by-projects (пусто — «Ещё нет
  проектов» и «Создать проект»); ответы POST отделов и должностей краткие — сверяем по GET;
- в узле отдела — «Редактировать», «Добавить подотдел», «Добавить должность», чипы должностей;
- «Добавить отдел» → окно «Новый отдел»: «Название *», «Бизнес-блок (swimlane)», «Юридическое
  лицо», «Родительский отдел» («Корневой отдел»), «Руководитель», «Описание», галочка «Отдел на
  аутсорсе» (не ставим: подрядчик уходит в общий реестр «Контрагенты» платформы BORD) → POST
  /org/departments;
- «Добавить должность» → «Новая должность»: «Название *», «Описание», «Департаменты *»,
  «Порядок», Ф—П—О → POST /org/positions;
- в BORD QA есть пример «Отдел продаж (пример)» / «Менеджер по продажам (пример)» — не трогаем.
Свой отдел живёт на своём юрлице со своим сотрудником-руководителем; после теста отделы,
должности, сотрудник и юрлицо удаляются.
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (QA_PREFIX, clean_up, create_employee, create_legal_entity, delete_employee, dropdown, field, hrm_delete,
                         header_button, hrm_get, hrm_json, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth


def main(page: Page):
    return page.locator("main")


def dialog(page: Page, title: str):
    return page.locator("div.fixed").filter(has=page.get_by_text(title, exact=True)).last


def node(page: Page, name: str):
    """Узел отдела на схеме: блок с названием и кнопкой «Добавить должность»."""
    return main(page).locator("div").filter(has=page.get_by_text(name, exact=True)).filter(
        has=page.get_by_role("button", name="Добавить должность")).last


def remove_own_org(page: Page):
    for pos in items(hrm_json(hrm_get(page, "/org/positions"))):
        if pos["name"].startswith(QA_PREFIX):
            hrm_delete(page, f"/org/positions/{pos['id']}")
    deps = [d for d in items(hrm_json(hrm_get(page, "/org/departments", {"include_archived": "true"})))
            if d["name"].startswith(QA_PREFIX)]
    for d in sorted(deps, key=lambda d: d["parent_id"] is None):  # сначала подотделы
        hrm_delete(page, f"/org/departments/{d['id']}")


@pytest.fixture
def org(auth_page: Page):
    """Своё юрлицо и свой сотрудник — будущий руководитель отдела."""
    le = create_legal_entity(auth_page)
    head = create_employee(auth_page, le["id"], f"{QA_PREFIX} Руководитель {stamp()}")
    yield {"le": le, "head": head}
    remove_own_org(auth_page)
    delete_employee(auth_page, head["id"])
    clean_up(auth_page)


# Кейс: Схема = /org/tree — отделы, люди без отдела, блоки юрлиц
def test_tree_matches_api(auth_page: Page):
    with auth_page.expect_response(lambda r: r.url.endswith("/api/hrm/org/tree")) as resp:
        open_hrm(auth_page, "/admin/orgchart")
    tree = resp.value.json()
    for dep in tree["departments"]:
        expect(main(auth_page).get_by_text(dep["name"], exact=True).first).to_be_visible()
    expect(main(auth_page)).to_contain_text(f"{len(tree['unassigned'])} без отдела")
    for le in items(hrm_json(hrm_get(auth_page, "/legal-entities"))):
        expect(main(auth_page).get_by_role("button").filter(has_text=le["name"]).first).to_be_visible()


# Кейс: Вид «По проектам» строится по /org/by-projects, назад — «По департаментам»
def test_by_projects_view(auth_page: Page):
    open_hrm(auth_page, "/admin/orgchart")
    with auth_page.expect_response(lambda r: "/api/hrm/org/by-projects" in r.url) as resp:
        header_button(auth_page, "По проектам").click()
    assert resp.value.ok, resp.value.status
    data = resp.value.json()
    projects = data.get("projects", data) if isinstance(data, dict) else data
    if not projects:
        expect(main(auth_page)).to_contain_text("Ещё нет проектов")
        expect(main(auth_page).get_by_role("button", name="Создать проект")).to_be_visible()
    header_button(auth_page, "По департаментам").click()
    expect(main(auth_page).get_by_role("button", name="Добавить должность").first).to_be_visible()


# Кейс: Свой отдел — создать с юрлицом и руководителем, должность, подотдел, переименовать, архив
@pytest.mark.writes_data
def test_department_lifecycle(auth_page: Page, org):
    le, head = org["le"], org["head"]
    name = f"{QA_PREFIX} Отдел {stamp()}"
    open_hrm(auth_page, "/admin/orgchart")

    header_button(auth_page, "Добавить отдел").click()
    box = dialog(auth_page, "Новый отдел")
    field(box, "Название *").fill(name)
    dropdown(auth_page, box, "Юридическое лицо", le["name"])
    dropdown(auth_page, box, "Руководитель", head["telegram_first_name"])
    field(box, "Описание").fill(f"{QA_PREFIX} описание")
    with auth_page.expect_response(lambda r: r.url.endswith("/api/hrm/org/departments") and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    dep = hrm_json(hrm_get(auth_page, f"/org/departments/{resp.value.json()['id']}"))  # ответ POST краткий
    assert (dep["name"], dep["legal_entity_id"], dep["head_user_id"], dep["parent_id"]) == (name, le["id"], head["id"], None), dep
    card = node(auth_page, name)
    expect(card).to_be_visible()
    expect(card).to_contain_text(head["telegram_first_name"])

    # должность в отделе
    pos_name = f"{QA_PREFIX} Должность {stamp()}"
    card.get_by_role("button", name="Добавить должность").click()
    box = dialog(auth_page, "Новая должность")
    field(box, "Название *").fill(pos_name)
    expect(field(box, "Департаменты *")).to_contain_text(name)  # отдел узла подставлен
    field(box, "Функция (что делает)").fill(f"{QA_PREFIX} функция")
    with auth_page.expect_response(lambda r: r.url.endswith("/api/hrm/org/positions") and r.request.method == "POST") as presp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert presp.value.ok, presp.value.text()
    pos = hrm_json(hrm_get(auth_page, f"/org/positions/{presp.value.json()['id']}"))
    assert pos["name"] == pos_name and dep["id"] in ([pos.get("department_id")] + (pos.get("department_ids") or [])), pos
    expect(card).to_contain_text(pos_name)

    # подотдел
    sub_name = f"{QA_PREFIX} Подотдел {stamp()}"
    card.get_by_role("button", name="Добавить подотдел").click()
    box = auth_page.locator("div.fixed:visible").last
    field(box, "Название *").fill(sub_name)
    expect(box).to_contain_text("Новый отдел")
    expect(field(box, "Юридическое лицо")).to_contain_text(le["name"])  # юрлицо родителя подставлено
    # при выбранном юрлице подпись — «Родительский отдел — отделы выбранной компании»
    expect(field(box, "Родительский отдел", starts=True)).to_contain_text(name)
    with auth_page.expect_response(lambda r: r.url.endswith("/api/hrm/org/departments") and r.request.method == "POST") as sresp:
        box.get_by_role("button", name="Создать", exact=True).click()
    sub = hrm_json(hrm_get(auth_page, f"/org/departments/{sresp.value.json()['id']}"))
    assert sub["parent_id"] == dep["id"], sub
    expect(node(auth_page, sub_name)).to_be_visible()

    # переименование
    new_name = f"{name} ред"
    node(auth_page, name).get_by_role("button", name="Редактировать").first.click()
    box = auth_page.locator("div.fixed:visible").last
    field(box, "Название *").fill(new_name)
    with auth_page.expect_response(lambda r: f"/api/hrm/org/departments/{dep['id']}" in r.url and r.request.method == "PUT") as uresp:
        box.get_by_role("button", name="Сохранить", exact=True).click()
    assert uresp.value.ok, uresp.value.text()
    expect(node(auth_page, new_name)).to_be_visible()
    got = hrm_json(hrm_get(auth_page, f"/org/departments/{dep['id']}"))
    assert (got["name"], got["head_user_id"]) == (new_name, head["id"]), got

    # удаление подотдела из окна «Редактировать» («Архивировать» из справки на экране нет)
    node(auth_page, sub_name).get_by_role("button", name="Редактировать").first.click()
    box = auth_page.locator("div.fixed:visible").last
    box.get_by_role("button", name="Удалить", exact=True).click()
    confirm = auth_page.locator("div.fixed:visible").last
    with auth_page.expect_response(lambda r: f"/api/hrm/org/departments/{sub['id']}" in r.url and r.request.method == "DELETE") as dresp:
        confirm.get_by_role("button", name=re.compile("^(Удалить|Да)$")).last.click()
    assert dresp.value.ok, dresp.value.text()
    expect(node(auth_page, sub_name)).to_have_count(0)
    assert hrm_get(auth_page, f"/org/departments/{sub['id']}").status == 404
