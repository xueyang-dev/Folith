#!/usr/bin/env python3
"""取证脚本：1440×900 下顶栏与侧栏角标的实际几何与文字裁切。

    # 第一次运行前：把验收任务复制成「原样副本」（每门语言开始前从这里恢复，
    # 保证 7 门语言量到的是同一个任务状态）
    cp -R /var/folders/.../T/folith-acceptance/data/ed69c82a98d81d7b \
        .acceptance-tmp/layout/pristine-data/
    cp -R /var/folders/.../T/folith-acceptance/data/. .acceptance-tmp/layout-data/

    ./venv/bin/python scripts/qt_header_layout_probe.py --label before
    ./venv/bin/python scripts/qt_header_layout_probe.py --label after --languages de,ar
    ./venv/bin/python scripts/qt_header_layout_probe.py --label after-narrow \
        --languages de,ru,ar --width 1180      # 窄窗口：验证省略号

每一门语言在**独立子进程**里（界面语言在启动时确定，同进程换语言量到的数字不可比）：
用产品自己的 ``MainWindow`` 打开指定任务、切到审校页，然后

* 截图整窗、顶栏与任务库侧栏（``.acceptance-tmp/layout/<label>/``）；
* 记录顶栏关键控件的 geometry / sizeHint / 完整文字 / 实际画出来的文字；
* 记录侧栏「项目」按钮与 999+ 角标的位置，判断角标是否压住标签。

默认会**固定任务状态**（见 :func:`_pin_task_state`）：并行的交付门禁改造会在打开
任务时一次性修复过期的 blocking findings，状态行与主按钮的文案会随之变短——那样
量的就不是验收截图里的场景了。``--no-pin-state`` 可以关掉这个固定。

这不是单元测试：它把**真实窗口**里量到的数字写成 JSON，供修复前后对照。断言在
``tests/qt_header_layout_test.py`` 里，跑得比这个脚本快得多。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANGUAGES = ("zh-CN", "en", "es", "ru", "fr", "de", "ar")
DEFAULT_JOB = "ed69c82a98d81d7b"
DEFAULT_DATA = ROOT / ".acceptance-tmp" / "layout-data"
DEFAULT_OUT = ROOT / ".acceptance-tmp" / "layout"
DEFAULT_PRISTINE = DEFAULT_OUT / "pristine-data"
WINDOW_SIZE = (1440, 900)


def _process(app, seconds: float = 0.0) -> None:
    app.processEvents()
    deadline = time.monotonic() + seconds
    while seconds and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _wait(app, predicate, *, timeout: float = 90.0, label: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {label}")


def _advance(widget, text: str) -> int:
    return widget.fontMetrics().horizontalAdvance(text)


def _header_card(bench):
    card = bench.stage_bar.parentWidget()
    while card is not None and card.objectName() != "unifiedToolbar":
        card = card.parentWidget()
    return card


def _header_metrics(window) -> dict:
    from PySide6.QtWidgets import QPushButton

    bench = window.workbench
    card = _header_card(bench)
    line = bench.run_status.line
    view = bench.run_status.line_view
    button = bench.stage_action_button
    drawn = QPushButton.text(button)  # ElideButton.text() 返回完整文字，这里要画出来的那份
    return {
        "window": [window.width(), window.height()],
        "header_card": list(card.geometry().getRect()) if card else None,
        "run_status": {
            "geometry": list(bench.run_status.geometry().getRect()),
            "minimum_width": bench.run_status.minimumWidth(),
            "maximum_width": bench.run_status.maximumWidth(),
            "line_full_text": line.text(),
            "line_tooltip": line.toolTip(),
            "line_label_size_hint": [line.sizeHint().width(), line.sizeHint().height()],
            "line_label_width": line.width(),
            "line_view_width": view.width(),
            "line_viewport_width": line.contentsRect().width(),
            "line_full_text_width": _advance(line, line.text()),
            "line_elided_text": line.elided_text() if hasattr(line, "elided_text") else line.text(),
            "line_overflows": (_advance(line, line.text())
                               > line.contentsRect().width()),
            "line_ends_with_ellipsis": (line.elided_text().rstrip().endswith("…")
                                        if hasattr(line, "elided_text") else False),
        },
        "stage_action_button": {
            "visible": button.isVisible(),
            "geometry": list(button.geometry().getRect()),
            "full_text": button.text(),
            "drawn_text": drawn,
            "maximum_width": button.maximumWidth(),
            "size_hint_width": button.sizeHint().width(),
            "full_text_width": _advance(button, button.text()),
            "drawn_text_width": _advance(button, drawn),
            "ends_with_ellipsis": drawn.rstrip().endswith("…"),
            "tooltip": button.toolTip(),
        },
        "stage_bar": {
            "geometry": list(bench.stage_bar.geometry().getRect()),
            "size_hint": [bench.stage_bar.sizeHint().width(), bench.stage_bar.sizeHint().height()],
            "minimum_size_hint": [bench.stage_bar.minimumSizeHint().width(),
                                  bench.stage_bar.minimumSizeHint().height()],
        },
        "identity_box": {
            "geometry": list(bench.title_label.parentWidget().geometry().getRect()),
        },
        "header_actions_bar": {
            "geometry": list(bench.header_actions_bar.geometry().getRect()),
        },
    }


def _home_metrics(window) -> dict:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton

    home = window.home
    button = home.manage_projects_button
    badge = home.pending_badge
    drawn = QPushButton.text(button)
    badge_rect = badge.geometry()
    rtl = button.layoutDirection() == Qt.LayoutDirection.RightToLeft
    margin = 12
    text_w = min(_advance(button, drawn), max(0, button.width() - 2 * margin))
    if rtl:
        text_left, text_right = button.width() - margin - text_w, button.width() - margin
    else:
        text_left, text_right = margin, margin + text_w
    overlap = badge.isVisible() and not (
        badge_rect.right() < text_left or badge_rect.left() > text_right)
    return {
        "sidebar_width": home.sidebar.width(),
        "projects_button": {
            "geometry": list(button.rect().getRect()),
            "layout_direction": "rtl" if rtl else "ltr",
            "full_text": button.text(),
            "drawn_text": drawn,
            "text_band": [text_left, text_right],
        },
        "pending_badge": {
            "visible": badge.isVisible(),
            "geometry": list(badge_rect.getRect()),
            "text": badge.text(),
        },
        "badge_overlaps_label": overlap,
        "badge_on_trailing_edge": badge_rect.right() >= button.width() - 16,
        "badge_on_leading_edge": badge_rect.left() <= 16,
    }


def _restore_job(pristine: Path, target: Path) -> None:
    """用原样副本覆盖任务目录（保留目录本身，避免路径失效）。"""
    if not pristine.is_dir():
        return
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(pristine, target)


def _grab(widget, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    assert widget.grab().save(str(path)), f"could not save {path}"


def _pin_task_state() -> None:
    """把任务状态固定在验收时的那一刻。

    并行的交付门禁改造会在打开任务时一次性「修复」过期的 blocking findings
    （ed69c82a98d81d7b 的 9 条会变成 0），状态行与主按钮的文案随之变短——量到的
    就不是验收截图里的那个场景了。取证脚本固定这份持久化状态，让修复前后量的是
    同一段文字；门禁本身的行为不在本脚本的验证范围内。
    """
    from folith.engine import delivery_state

    delivery_state.gate_reconciliation_needed = lambda *_args, **_kwargs: False


def run_language(language: str, data_dir: Path, out_dir: Path, job_id: str,
                 *, pin_state: bool = True, size: tuple[int, int] = WINDOW_SIZE) -> dict:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["FOLITH_UI_LANGUAGE"] = language
    os.environ["FOLITH_DESKTOP_DATA_DIR"] = str(data_dir)
    os.environ["FOLITH_OUTPUT_DIR"] = str(data_dir)
    os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
    with tempfile.TemporaryDirectory(prefix="folith-layout-probe-") as temporary:
        base = Path(temporary)
        os.environ["FOLITH_DESKTOP_LOG_DIR"] = str(base / "logs")
        os.environ["FOLITH_QT_SETTINGS_DIR"] = str(base / "settings")

        if pin_state:
            _pin_task_state()

        from folith.desktop import paths
        from folith.qt import adapter
        from folith.qt import window as window_module
        from folith.qt.selftest import open_job_and_wait

        app = window_module.build_application([])
        assert app.platformName() == "offscreen", app.platformName()
        window = window_module.MainWindow(adapter.EngineConfig(), paths.resolve_paths(), None)
        window.resize(*size)
        window.show()
        _process(app, 0.3)
        window.refresh_home()
        _process(app, 0.5)
        # 验收截图里的 999+ 是真实待审核总数；这里直接置数，保证每门语言量的是同一个角标。
        window.home.set_pending_badge(999)
        _process(app, 0.3)
        home = _home_metrics(window)
        _grab(window, out_dir / f"{language}-home.png")
        _grab(window.home.sidebar, out_dir / f"{language}-home-sidebar.png")

        open_job_and_wait(window, job_id)
        window.workbench.show_page("review")
        _wait(app, lambda: bool(window.workbench._all_rows), label="review rows")
        _process(app, 0.6)
        header = _header_metrics(window)
        _grab(window, out_dir / f"{language}-review.png")
        card = _header_card(window.workbench)
        if card is not None:
            _grab(card, out_dir / f"{language}-review-header.png")

        result = {"language": language, "window": [window.width(), window.height()],
                  "header": header, "home": home}
        window.timer.stop()
        window._closing_accepted = True
        window.close()
        _process(app, 0.2)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="before", help="输出子目录名（before / after）")
    parser.add_argument("--languages", default=",".join(LANGUAGES), help="逗号分隔的语言代码")
    parser.add_argument("--job", default=DEFAULT_JOB)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA),
                        help="验收数据副本（只读打开）")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--pristine-dir", default=str(DEFAULT_PRISTINE),
                        help="任务状态的原样副本；每门语言开始前从这里恢复")
    parser.add_argument("--no-pin-state", action="store_true",
                        help="不固定任务状态（默认固定，见 _pin_task_state）")
    parser.add_argument("--width", type=int, default=WINDOW_SIZE[0],
                        help="窗口逻辑宽度（默认 1440；窄宽度用来验证省略）")
    parser.add_argument("--height", type=int, default=WINDOW_SIZE[1])
    parser.add_argument("--single", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    if not (data_dir / args.job).is_dir():
        raise SystemExit(f"missing job {args.job} under {data_dir}; copy the acceptance "
                         "data there first")
    out_dir = Path(args.out) / args.label
    out_dir.mkdir(parents=True, exist_ok=True)
    pristine = Path(args.pristine_dir) / args.job

    if args.single:
        result = run_language(args.single, data_dir, out_dir, args.job,
                              pin_state=not args.no_pin_state,
                              size=(args.width, args.height))
        (out_dir / f"{args.single}-metrics.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    results = {}
    for language in [item.strip() for item in args.languages.split(",") if item.strip()]:
        print(f"[probe] {language}", flush=True)
        # 每门语言从同一份原样状态开始：否则第一门语言打开任务后的写盘会改变
        # 后面几门语言量到的状态行与主按钮文案。
        _restore_job(pristine, data_dir / args.job)
        command = [sys.executable, str(Path(__file__).resolve()), "--single", language,
                   "--label", args.label, "--job", args.job,
                   "--data-dir", str(data_dir), "--out", str(Path(args.out)),
                   "--width", str(args.width), "--height", str(args.height)]
        if args.no_pin_state:
            command.append("--no-pin-state")
        completed = subprocess.run(command, capture_output=True, text=True, cwd=str(ROOT),
                                   env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
        if completed.returncode != 0:
            raise SystemExit(f"{language} failed:\n{completed.stdout}\n{completed.stderr}")
        payload = out_dir / f"{language}-metrics.json"
        results[language] = json.loads(payload.read_text(encoding="utf-8"))
    summary = out_dir / "summary.json"
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[probe] wrote {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
