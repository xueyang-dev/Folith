"""外观：日间 / 夜间 / 跟随系统 任意顺序切换，窗口、对话框、调色板始终同一套主题。

真实缺陷（试用反馈）：夜间 → 跟随系统（系统浅色）后主窗口留在深色、设置窗标题栏变浅
但内容仍深，之后整个客户端变卡。根因有二：

1. 钉住夜间后 ``QStyleHints.colorScheme()`` 报的是**我们钉的** Dark，窗口把它当成
   "系统是深色"，于是在跟随系统时解析出夜间；随后撤销钉住引发的同步
   ``colorSchemeChanged`` 又嵌套重入 apply_theme，内层设好的浅色被外层覆盖。
2. 判断"要不要写 setColorScheme"用的是 ``colorScheme() != Unknown``——跟随系统时它
   永远成立，于是每次应用主题都重写一次，平台每次重发主题变更，形成回环。

offscreen 的 QStyleHints 不接平台，所以这里用一个模拟 macOS 的替身
（钉住 / 系统两个值，生效值变化时同步发信号）。真实标题栏（NSAppearance）只能手测。
"""
from __future__ import annotations

import itertools
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, QSettings, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


class FakeHints(QObject):
    """模拟 cocoa：``colorScheme()`` = 钉住的值，没钉就是系统的值。"""

    colorSchemeChanged = Signal(Qt.ColorScheme)

    def __init__(self, system: str = "light") -> None:
        super().__init__()
        self.system = system
        self.pinned = Qt.ColorScheme.Unknown
        self.set_calls = 0

    def _effective(self):
        if self.pinned != Qt.ColorScheme.Unknown:
            return self.pinned
        return Qt.ColorScheme.Dark if self.system == "dark" else Qt.ColorScheme.Light

    def colorScheme(self):  # noqa: N802
        return self._effective()

    def setColorScheme(self, scheme):  # noqa: N802
        self.set_calls += 1
        before = self._effective()
        self.pinned = scheme
        if self._effective() != before:
            self.colorSchemeChanged.emit(self._effective())

    def set_system(self, system: str) -> None:
        before = self._effective()
        self.system = system
        if self._effective() != before:
            self.colorSchemeChanged.emit(self._effective())


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def client(tmp_path, monkeypatch, qt_app):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    import core

    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)

    from folith.desktop import paths as desktop_paths
    from folith.qt import adapter, i18n, style, window as window_module

    settings_file = str(tmp_path / "settings.ini")
    monkeypatch.setattr(window_module, "QSettings",
                        lambda *args: QSettings(settings_file, QSettings.Format.IniFormat))
    i18n.set_language("zh-CN")
    hints = FakeHints("light")
    monkeypatch.setattr(style, "style_hints", lambda: hints)
    monkeypatch.setattr(style, "_PINNED_SCHEME", None)
    config = adapter.EngineConfig(
        provider="自定义中转站", model="stub-model", api_key="stub-key", base_url="",
        target_lang="简体中文", enable_review=True, enable_report=False,
        enable_understanding=False, enable_source_cleanup=False)
    window = window_module.MainWindow(config, desktop_paths.resolve_paths(), None)
    window.show()
    qt_app.processEvents()
    yield {"window": window, "hints": hints, "app": qt_app, "style": style}
    from qt_workers import drain_job_workers

    drain_job_workers(core)
    window.timer.stop()
    window._closing_accepted = True
    window.close()
    qt_app.removeEventFilter(window)
    window.deleteLater()
    style.set_current_theme("light")


def _choose(window, preference: str) -> str:
    """走设置窗口的真实路径：选 外观 → 偏好落盘 → 应用。"""
    window.open_settings()
    dialog = window._settings_window
    index = dialog.theme_box.findData(preference)
    assert index >= 0
    dialog.theme_box.setCurrentIndex(index)
    dialog.flush_preferences()
    return str(window.settings.value(window.THEME_KEY))


def _assert_consistent(client, expected: str) -> None:
    from folith.qt import theme

    window, app, style = client["window"], client["app"], client["style"]
    app.processEvents()
    t = theme.tokens(expected)
    assert style.current_theme() == expected
    assert app.palette().window().color() == theme.qcolor(t["bg-page"])
    assert window.palette().window().color() == theme.qcolor(t["bg-page"])
    assert t["accent"] in app.styleSheet()
    dialog = getattr(window, "_settings_window", None)
    if dialog is not None:
        assert dialog.palette().window().color() == theme.qcolor(t["bg-page"])
    # 原生外观：显式选择钉住，跟随系统不钉。
    pinned = client["hints"].pinned
    if window.theme_preference == theme.FOLLOW_SYSTEM:
        assert pinned == Qt.ColorScheme.Unknown
    else:
        assert pinned == (Qt.ColorScheme.Dark if expected == "dark" else Qt.ColorScheme.Light)


@pytest.mark.parametrize("system", ["light", "dark"])
def test_day_night_follow_cycle(client, system):
    window, hints = client["window"], client["hints"]
    hints.set_system(system)
    client["app"].processEvents()
    for choice, expected in (("light", "light"), ("dark", "dark"),
                             ("system", system), ("dark", "dark"),
                             ("light", "light"), ("system", system)):
        assert _choose(window, choice) == choice
        _assert_consistent(client, expected)


@pytest.mark.parametrize("system", ["light", "dark"])
def test_every_switch_order(client, system):
    window, hints = client["window"], client["hints"]
    hints.set_system(system)
    for first, second in itertools.permutations(("system", "light", "dark"), 2):
        for choice in (first, second):
            _choose(window, choice)
            expected = system if choice == "system" else choice
            _assert_consistent(client, expected)


def test_follow_system_tracks_live_change(client):
    window, hints = client["window"], client["hints"]
    _choose(window, "dark")
    _choose(window, "system")
    _assert_consistent(client, "light")
    hints.set_system("dark")
    _assert_consistent(client, "dark")
    hints.set_system("light")
    _assert_consistent(client, "light")
    # 显式选择时系统外观变化不改界面。
    _choose(window, "dark")
    hints.set_system("dark")
    hints.set_system("light")
    _assert_consistent(client, "dark")


def test_follow_system_does_not_restyle_repeatedly(client, monkeypatch):
    """跟随系统：一次切换只重建一次样式表，稳态下重复应用不再写 setColorScheme。"""
    window, hints, style = client["window"], client["hints"], client["style"]
    builds = []
    real = style.build_qss
    monkeypatch.setattr(style, "build_qss", lambda *a, **k: builds.append(a) or real(*a, **k))
    _choose(window, "dark")
    _choose(window, "system")
    client["app"].processEvents()
    # 夜间一次、跟随系统一次；不允许嵌套重入或回环。
    assert len(builds) <= 2, len(builds)
    sets_before = hints.set_calls
    builds.clear()
    for _ in range(5):
        window.apply_theme_preference()
    client["app"].processEvents()
    assert hints.set_calls == sets_before
    assert len(builds) == 5
