import pytest

from helpers.auth import classic_view, load_user, login


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {
        **browser_context_args,
        "viewport": {"width": 1920, "height": 1080},
        "locale": "ru-RU",
        # CRM показывает время в зашитом Europe/Chisinau, раннер CI живёт в UTC — без этого время и дни на экране расходятся с тестом
        "timezone_id": "Europe/Chisinau",
    }


@pytest.fixture
def context(context):
    """Стандартная страница pytest-playwright (экран входа и т.п.) — тоже в классическом виде."""
    classic_view(context)
    return context


@pytest.fixture(scope="session")
def qa_user() -> dict:
    user = load_user()
    if not user:
        pytest.skip("нет учётки: tests/fixtures/users.json (см. users.example.json) или BORD_EMAIL/BORD_PASSWORD")
    return user


@pytest.fixture(scope="session")
def auth_state(browser, base_url, qa_user, tmp_path_factory, browser_context_args):
    """Вход один раз на прогон: сохранённая сессия (localStorage + кука bord_sso)."""
    ctx = browser.new_context(**browser_context_args)
    classic_view(ctx)
    page = ctx.new_page()
    login(page, qa_user)
    path = tmp_path_factory.mktemp("auth") / "state.json"
    ctx.storage_state(path=str(path))
    ctx.close()
    return str(path)


@pytest.fixture
def auth_page(browser, auth_state, browser_context_args):
    """Страница уже вошедшего пользователя BORD QA."""
    ctx = browser.new_context(**browser_context_args, storage_state=auth_state)
    page = ctx.new_page()
    yield page
    ctx.close()


@pytest.fixture(scope="session")
def colleague_user() -> dict:
    """Вторая тест-учётка BORD QA (роль «Сотрудник») — ключ qaColleague."""
    user = load_user("qaColleague")
    if not user:
        pytest.skip("нет второй учётки: qaColleague в tests/fixtures/users.json или USERS_JSON")
    return user


@pytest.fixture(scope="session")
def colleague_state(browser, colleague_user, tmp_path_factory, browser_context_args):
    ctx = browser.new_context(**browser_context_args)
    classic_view(ctx)
    page = ctx.new_page()
    login(page, colleague_user)
    path = tmp_path_factory.mktemp("auth-colleague") / "state.json"
    ctx.storage_state(path=str(path))
    ctx.close()
    return str(path)


@pytest.fixture
def colleague_page(browser, colleague_state, browser_context_args):
    """Страница коллеги — отдельный контекст, рядом со страницей владельца."""
    ctx = browser.new_context(**browser_context_args, storage_state=colleague_state)
    page = ctx.new_page()
    yield page
    ctx.close()


@pytest.fixture(scope="session")
def fx_state(browser, auth_state, tmp_path_factory, browser_context_args):
    """Сессия Facturi (app.facturi.md): вход кнопкой «BORD» один раз на прогон."""
    from helpers.facturi import make_state
    path = tmp_path_factory.mktemp("auth-facturi") / "state.json"
    return make_state(browser, browser_context_args, auth_state, str(path))


@pytest.fixture
def fx_page(browser, fx_state, browser_context_args):
    """Страница тест-учётки, уже вошедшей в Facturi (организация BORD QA)."""
    ctx = browser.new_context(**browser_context_args, storage_state=fx_state)
    page = ctx.new_page()
    yield page
    ctx.close()


# Оформление отчёта (терминал, HTML, сводка в Actions) — отдельно, в reporting.py.
pytest_plugins = ("reporting",)
