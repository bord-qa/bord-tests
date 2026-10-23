"""Окна-карточки справочников (Контрагенты, Продукты).

Это не role=dialog, а оверлей div.fixed.inset-0 с заголовком h2; Escape его
не закрывает — только крестик рядом с заголовком. Окна подтверждений
(«В архив?») — настоящие role=dialog, их берём по имени.
"""
import re

from playwright.sync_api import Page, expect


def modal(page: Page, heading: str | re.Pattern):
    # уровень заголовка разный: h2 в справочниках и задачах, h3 в доменах
    return page.locator("div.fixed.inset-0").filter(has=page.get_by_role("heading", name=heading))


def close_modal(page: Page, heading: str | re.Pattern):
    box = modal(page, heading)
    # крестик без подписи — кнопка с иконкой lucide-x (у задачи рядом ещё «Изменить»)
    box.locator("button:has(svg.lucide-x)").first.click()
    expect(box).to_have_count(0)
