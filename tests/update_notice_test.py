"""#518: launch-time update check is cached, bounded and silent on failure."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from folith.qt.update_notice import UpdateNotice, CACHE_SECONDS  # noqa: E402


def test_newer_release_is_cached_between_launches(tmp_path):
    calls = []
    def checker(current):
        calls.append(current)
        return {"state": "newer", "latest": "0.9.0", "url": "https://example.invalid/r",
                "notes": "what's new"}
    now = [1000.0]
    a = UpdateNotice(tmp_path, now=lambda: now[0], checker=checker)
    assert a.check("0.8.3") is True
    assert (a.latest, a.notes) == ("0.9.0", "what's new")
    now[0] += CACHE_SECONDS / 2
    b = UpdateNotice(tmp_path, now=lambda: now[0], checker=checker)
    assert b.check("0.8.3") is True and len(calls) == 1
    assert b.latest == "0.9.0"


def test_offline_and_current_are_silent(tmp_path):
    def boom(*_a, **_k):
        raise OSError("offline")
    assert UpdateNotice(tmp_path, checker=boom).check("0.8.3") is False
    assert UpdateNotice(tmp_path / "cur",
                        checker=lambda c: {"state": "current", "latest": c}).check("0.8.3") is False


def test_sidebar_has_update_pill_above_settings():
    from PySide6.QtWidgets import QApplication
    from folith.qt.views import HomeView

    QApplication.instance() or QApplication([])
    view = HomeView()
    assert view.update_pill is not None
    assert not view.update_pill.isVisible()
    from folith.qt import update_pill
    update_pill.show(view, "0.9.0", "https://example.invalid/r", "new stuff")
    assert "0.9.0" in view.update_pill.text()
    assert not view.update_pill.isHidden()
    view.update_pill.click()
    assert not view._update_notes.isHidden()
    view.close()
