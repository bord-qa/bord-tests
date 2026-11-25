"""Элементы шапки экрана BORD (app.bord.one, классический вид).

С ~20.11.2026 вкладки, фильтры, поиск и кнопки действий экранов («Новая задача», «Добавить продукт»,
«Добавить контрагента», «Редактор ролей»…) переехали из тела экрана (main) в его шапку (header — sticky
полоса с заголовком). На части экранов они ещё в main — поэтому ищем в видимой шапке, потом в main.
"""
import re

from playwright.sync_api import Locator, Page


def screen_button(page: Page, name, exact: bool = False) -> Locator:
    """Кнопка экрана по подписи (или регулярке): видимая шапка → main."""
    if isinstance(name, re.Pattern):
        label = name
    elif exact:
        label = re.compile(rf"^\s*{re.escape(name)}\s*$")
    else:
        label = re.compile(re.escape(name))
    return page.locator("header button:visible").filter(has_text=label).or_(
        page.locator("main").get_by_role("button", name=name, exact=exact)).first


def screen_field(page: Page, placeholder: str) -> Locator:
    """Поле поиска / фильтра экрана по placeholder: видимая шапка → main."""
    return page.locator("header").get_by_placeholder(placeholder).or_(
        page.locator("main").get_by_placeholder(placeholder)).first
