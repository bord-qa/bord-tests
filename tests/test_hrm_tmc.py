"""Блок 57. HRM — ТМЦ (/admin/tmc): каталог, остатки, выдача сотруднику и возврат.

Снято 02.11.2026 (в видимом MCP-браузере и по справке help.bord.one/hrm/tmc):
- вкладки «Каталог ТМЦ» / «Выдача ТМЦ»; каталог = GET /tmc/catalog/ («Каталог пуст»), колонки
  «Название, Код, Категория, Учётный, Кол-во, Ед., Стоимость, К возврату, Статус, Действия»;
  в строке «Пополнить», «Редактировать», «Деактивировать», «Удалить»;
- «Добавить» → «Новый ТМЦ»: «Название *», «Код», «Категория», «Кол-во по умолч.», «Ед.
  измерения», «Учётный», «Стоимость», «Сумма к возврату», «Остаток на складе», «Описание» →
  POST /tmc/catalog/ (остаток в API — quantity_on_hand, «Учётный» — is_tracked);
- «Выдача ТМЦ»: статусы «Все / Выдано / Возвращён частично / Закрыт (Возвращено) / Утеряно /
  Повреждено / Списано», «Выдать ТМЦ» → окно: «Сотрудник *», «Дата выдачи», «Позиции» —
  «ТМЦ» (вариант «Название (код)»), «Количество», «К возврату (переопр.)», «Примечание» →
  «Выдать» → POST /tmc/issues/bulk; строка выдачи «1 поз. / 2 ед.», «Выдано»; клик раскрывает
  позиции → «Возврат» → «Оформить возврат» («Выдано: N · возвращено: 0 · осталось: N»,
  «Количество *(до N)», «Результат *» — Возвращено) → POST /tmc/issues/<id>/return-event → «Закрыт»;
- «Сформировать акт» необратим (номер из журнала) — не жмём; удаление выдачи откатывает склад.
Свои позиции, сотрудник и выдачи удаляются после теста.
"""
import re

import pytest
from playwright.sync_api import Page, expect

from helpers.hrm import (QA_PREFIX, clean_up, create_employee, create_legal_entity, delete_employee, dropdown, field,
                         header_button, hrm_delete, hrm_get, hrm_json, hrm_post, items, open_hrm, stamp)

pytestmark = pytest.mark.requires_auth


def main(page: Page):
    return page.locator("main")


def item_row(page: Page, name: str):
    return main(page).locator("tr").filter(has_text=name).first


def get_item(page: Page, item_id: int) -> dict:
    return next(i for i in items(hrm_json(hrm_get(page, "/tmc/catalog/"))) if i["id"] == item_id)


def remove_own(page: Page):
    for issue in items(hrm_json(hrm_get(page, "/tmc/issues/"))):
        if str(issue.get("tmc_item_name") or issue.get("item_name") or "").startswith(QA_PREFIX) or \
                str(issue.get("employee_name") or "").startswith(QA_PREFIX):
            hrm_delete(page, f"/tmc/issues/{issue['id']}")
    for it in items(hrm_json(hrm_get(page, "/tmc/catalog/"))):
        if it["name"].startswith(QA_PREFIX):
            hrm_delete(page, f"/tmc/catalog/{it['id']}")


@pytest.fixture
def stock(auth_page: Page):
    yield
    remove_own(auth_page)
    clean_up(auth_page)


# Кейс: Позиция каталога — форма, «Пополнить», «Деактивировать», «Удалить»
@pytest.mark.writes_data
def test_catalog_item(auth_page: Page, stock):
    name = f"{QA_PREFIX} Ноутбук {stamp()}"
    open_hrm(auth_page, "/admin/tmc")
    expect(main(auth_page).get_by_role("button", name="Каталог ТМЦ", exact=True)).to_be_visible()
    header_button(auth_page, "Добавить").click() if header_button(auth_page, "Добавить").count() \
        else main(auth_page).get_by_role("button", name="Добавить", exact=True).click()
    box = auth_page.locator("div.fixed:visible").filter(has_text="Новый ТМЦ").last
    field(box, "Название *").fill(name)
    field(box, "Код").fill("QA-NB")
    expect(field(box, "Ед. измерения")).to_have_value("шт")
    field(box, "Стоимость").fill("1000")
    field(box, "Сумма к возврату").fill("500")
    field(box, "Остаток на складе").fill("5")
    with auth_page.expect_response(lambda r: "/api/hrm/tmc/catalog/" in r.url and r.request.method == "POST") as resp:
        box.get_by_role("button", name="Создать", exact=True).click()
    assert resp.value.ok, resp.value.text()
    item = get_item(auth_page, resp.value.json()["id"])
    assert (item["name"], item["code"], item["quantity_on_hand"], float(item["cost"]), float(item["refund_amount"])) == \
        (name, "QA-NB", 5, 1000.0, 500.0), item
    row = item_row(auth_page, name)
    expect(row).to_contain_text("QA-NB")
    expect(row).to_contain_text("Активно")

    row.get_by_role("button", name="Пополнить").click()
    dlg = auth_page.locator("div.fixed:visible").last
    dlg.locator("input[type=number], input").first.fill("3")
    with auth_page.expect_response(lambda r: f"/tmc/catalog/{item['id']}/restock" in r.url) as rs:
        dlg.get_by_role("button", name="Пополнить", exact=True).click()
    assert rs.value.ok, rs.value.text()
    assert get_item(auth_page, item["id"])["quantity_on_hand"] == 8

    with auth_page.expect_response(lambda r: f"/tmc/catalog/{item['id']}" in r.url and r.request.method == "PUT") as off:
        item_row(auth_page, name).get_by_role("button", name="Деактивировать").click()
        confirm = auth_page.locator("div.fixed:visible").get_by_role("button", name=re.compile("^(Деактивировать|Да)$"))
        if confirm.count():
            confirm.last.click()
    assert off.value.ok, off.value.text()
    expect(item_row(auth_page, name)).not_to_contain_text("Активно")

    item_row(auth_page, name).get_by_role("button", name="Удалить").click()
    dlg = auth_page.locator("div.fixed:visible").last
    with auth_page.expect_response(lambda r: f"/tmc/catalog/{item['id']}" in r.url and r.request.method == "DELETE") as gone:
        dlg.get_by_role("button", name=re.compile("^(Удалить|Да|Удалить безвозвратно)$")).last.click()
    assert gone.value.ok, gone.value.text()
    expect(item_row(auth_page, name)).to_have_count(0)


# Кейс: Выдача сотруднику списывает со склада, видна у сотрудника; возврат закрывает выдачу и возвращает остаток
@pytest.mark.writes_data
def test_issue_and_return(auth_page: Page, stock):
    le = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Получатель {stamp()}")
    item = hrm_json(hrm_post(auth_page, "/tmc/catalog/", {"name": f"{QA_PREFIX} Каска {stamp()}", "code": "QA-H",
                                                          "is_tracked": True, "quantity_on_hand": 5, "unit": "шт",
                                                          "cost": 200, "refund_amount": 100, "is_returnable": True}))
    item = get_item(auth_page, item["id"])
    try:
        assert item["quantity_on_hand"] == 5, item
        open_hrm(auth_page, "/admin/tmc")
        main(auth_page).get_by_role("button", name="Выдача ТМЦ", exact=True).click()
        main(auth_page).get_by_role("button", name="Выдать ТМЦ").click()
        box = auth_page.locator("div.fixed:visible").filter(has_text="Выдать ТМЦ").last
        dropdown(auth_page, box, "Сотрудник *", emp["telegram_first_name"])
        dropdown(auth_page, box, "ТМЦ", f"{item['name']} (QA-H)")  # вариант — «Название (код)»
        field(box, "Количество").fill("2")
        with auth_page.expect_response(lambda r: "/api/hrm/tmc/issues/bulk" in r.url) as resp:
            box.get_by_role("button", name="Выдать", exact=True).click()
        assert resp.value.ok, resp.value.text()
        body = resp.value.request.post_data_json
        assert (body["employee_id"], body["items"][0]["catalog_id"], body["items"][0]["quantity"]) == (emp["id"], item["id"], 2), body
        assert get_item(auth_page, item["id"])["quantity_on_hand"] == 3  # списано со склада
        line = main(auth_page).locator("tr").filter(has_text=emp["telegram_first_name"]).first
        expect(line).to_contain_text("1 поз. / 2 ед.")
        expect(line).to_contain_text("400,00")  # 2 × 200
        expect(line).to_contain_text("Выдано")
        # у сотрудника — то, что показывает вкладка «ТМЦ» его карточки (/tmc/issues/?employee_id=)
        mine = items(hrm_json(hrm_get(auth_page, "/tmc/issues/", {"employee_id": emp["id"]})))
        assert [(m["tmc_item_id"], m["quantity"], m["status"]) for m in mine] == [(item["id"], 2, "issued")], mine

        # возврат: строка раскрывается в позиции → «Возврат» → «Оформить возврат»
        line.click()
        main(auth_page).get_by_role("button", name="Возврат", exact=True).first.click()
        dlg = auth_page.locator("div.fixed:visible").filter(has_text="Оформить возврат").last
        expect(dlg).to_contain_text("Выдано: 2 · возвращено: 0 · осталось: 2")
        expect(field(dlg, "Количество *(до 2)")).to_have_value("2")
        expect(field(dlg, "Результат *")).to_contain_text("Возвращено")
        with auth_page.expect_response(lambda r: "/return-event" in r.url) as ret:
            dlg.get_by_role("button", name="Оформить", exact=True).click()
        assert ret.value.ok, ret.value.text()
        assert ret.value.request.post_data_json == {"quantity": 2, "outcome": "returned"}, ret.value.request.post_data
        assert get_item(auth_page, item["id"])["quantity_on_hand"] == 5  # вернулось на склад
        expect(main(auth_page).locator("tr").filter(has_text=emp["telegram_first_name"]).first).to_contain_text("Закрыт")
        issue = next(i for i in items(hrm_json(hrm_get(auth_page, "/tmc/issues/"))) if i["employee_id"] == emp["id"])
        assert (issue["status"], issue["returned_quantity"], issue["outstanding_quantity"]) == ("returned", 2, 0), issue
    finally:
        remove_own(auth_page)
        delete_employee(auth_page, emp["id"])


# Кейс: ТМЦ сотрудника по API — /tmc/employee/<id>/ у сотрудника с выдачей (bord-45 починен 03.11.2026)
@pytest.mark.writes_data
def test_employee_tmc_endpoint(auth_page: Page, stock):
    le = create_legal_entity(auth_page)
    emp = create_employee(auth_page, le["id"], f"{QA_PREFIX} Получатель API {stamp()}")
    item = hrm_json(hrm_post(auth_page, "/tmc/catalog/", {"name": f"{QA_PREFIX} Перчатки {stamp()}", "code": "QA-G",
                                                          "is_tracked": True, "quantity_on_hand": 3, "unit": "шт"}))
    try:
        assert hrm_get(auth_page, f"/tmc/employee/{emp['id']}/").ok  # без выдач — 200
        hrm_json(hrm_post(auth_page, "/tmc/issues/bulk", {"employee_id": emp["id"], "issue_date": str(__import__("datetime").date.today()),
                                                          "items": [{"catalog_id": item["id"], "quantity": 1}]}))
        resp = hrm_get(auth_page, f"/tmc/employee/{emp['id']}/")
        assert resp.ok, f"{resp.status} {resp.text()[:200]}"
        assert resp.json()["total_issued"] == 1, resp.json()
    finally:
        remove_own(auth_page)
        delete_employee(auth_page, emp["id"])
