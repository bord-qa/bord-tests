from playwright.sync_api import Error, Page


def settle(page: Page, timeout: float = 10_000):
    """Дождаться, пока сеть затихнет, но не дольше timeout.

    Нужно против гонки с гидрацией Next.js: кнопки видны раньше, чем на них
    навешаны обработчики. Жёсткий `networkidle` здесь флакает — на страницах
    бывают фоновые опросы (виджет «Помощник BORD» и т.п.), и тишина может так
    и не наступить. Готовность экрана тесты всё равно проверяют по элементам.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Error:
        pass
