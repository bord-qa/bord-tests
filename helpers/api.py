"""Запросы к API платформы от имени вошедшего пользователя.

Фронт ходит в /api/* с заголовком `Authorization: Bearer <admin_token>`,
токен берём из localStorage той же страницы — так API видит ту же организацию,
что и интерфейс. Ответы API — эталон для сверки экранов (как __NUXT__ в ECOM)
и способ прибрать за тестом.
"""
from playwright.sync_api import APIResponse, Page

APP = "https://app.bord.one"


def _headers(page: Page) -> dict:
    token = page.evaluate("() => localStorage.getItem('admin_token')")
    assert token, "нет admin_token: страница не вошла в BORD"
    return {"Authorization": f"Bearer {token}"}


def api_get(page: Page, path: str) -> APIResponse:
    return page.request.get(APP + path, headers=_headers(page))


def api_post(page: Page, path: str, data: dict) -> APIResponse:
    return page.request.post(APP + path, headers=_headers(page), data=data)


def api_delete(page: Page, path: str) -> APIResponse:
    return page.request.delete(APP + path, headers=_headers(page))


def api_put(page: Page, path: str, data: dict) -> APIResponse:
    return page.request.put(APP + path, headers=_headers(page), data=data)


def api_patch(page: Page, path: str, data: dict) -> APIResponse:
    return page.request.patch(APP + path, headers=_headers(page), data=data)
