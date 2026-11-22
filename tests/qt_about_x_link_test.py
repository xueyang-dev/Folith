"""#519: the About page exposes the project's X account."""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
from PySide6.QtWidgets import QApplication  # noqa: E402
from folith.qt import settings_pages, support  # noqa: E402


def test_about_has_x_link():
    QApplication.instance() or QApplication([])
    page = settings_pages.AboutPage(None)
    assert support.X_URL == "https://x.com/xueyang2216"
    assert "xueyang2216" in page.x_link.toolTip()


def test_clicking_x_link_opens_the_reviewed_url(monkeypatch):
    """The About link is a real user click, not a model-generated citation."""
    from PySide6.QtWidgets import QApplication
    from folith.qt import settings_pages

    QApplication.instance() or QApplication([])
    opened = []
    monkeypatch.setattr(settings_pages, "_open_url", lambda url: opened.append(url))
    page = settings_pages.AboutPage(None)
    page.x_link.click()
    assert opened == [support.X_URL]
