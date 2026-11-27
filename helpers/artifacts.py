"""Улики упавшего теста для страниц из своих контекстов (не стандартной фикстуры page)."""
import re
from pathlib import Path


def save_failure(request, page) -> None:
    """Скриншот и адрес упавшего теста для страниц из своих контекстов: --screenshot=only-on-failure
    у pytest-playwright снимает только стандартную фикстуру page, а в CI нужна картинка."""
    report = getattr(request.node, "rep_call", None)
    if not (report and report.failed) or page.is_closed():
        return
    out = Path(request.config.getoption("--output", "test-results")) / re.sub(r"[^\w.-]+", "-", request.node.nodeid)
    out.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(out / "failed.png"), full_page=True)
        (out / "url.txt").write_text(page.url, encoding="utf-8")
    except Exception as e:  # снимок — подсказка, а не причина второго падения
        print(f"скриншот не снят: {e}")
