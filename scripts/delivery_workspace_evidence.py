#!/usr/bin/env python3
"""交付工作区证据：真实 demo 数据 + 真实冻结快照 + 真实文件动作 + 截图。

    venv/bin/python scripts/delivery_workspace_evidence.py

做三件事，全部落在 ``tmp/delivery-redesign/``（工作区临时目录，已 gitignore）：

1. 用 ``scripts/qt_showcase_screenshots.py`` 的 ``seed`` 造演示任务，并真的调用
   ``core.approve_delivery`` 冻结一份快照（不是手工摆出来的假 manifest）；
2. 打开真实 ``MainWindow``，进入交付页，截取：冻结（浅色 / 深色）、窄窗
   （820x760）、技术附件展开、长中文文件名任务、未冻结（门禁）页；
3. 走一遍真实动作：ZIP 保存、单文件另存、打开交付文件夹（注入 opener，**绝不
   启动 Finder/Explorer**）、复制路径（读剪贴板核对），最后断言快照文件在动作
   前后逐字节未变。

每项结果打印 ``PASS`` / ``FAIL`` 行，任一项失败以非零码退出。种子数据与译文全部
是为截图写的合成内容；截图只写到 tmp/，不写 docs/。
"""
from __future__ import annotations

import copy
import hashlib
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "tmp" / "delivery-redesign"
DATA = WORK / "data"
OUT = WORK / "evidence"

# 环境必须在导入 core / 界面模块之前定好：core.OUTPUT_DIR 在导入时读环境变量，
# 设置目录也要重定向，脚本不能改掉本机真实的偏好。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"
os.environ["FOLITH_UI_LANGUAGE"] = "zh-CN"
os.environ["FOLITH_QT_SETTINGS_DIR"] = str(WORK / "settings")
os.environ["FOLITH_DESKTOP_DATA_DIR"] = str(DATA)
os.environ["FOLITH_OUTPUT_DIR"] = str(DATA)
os.environ["FOLITH_DESKTOP_LOG_DIR"] = str(WORK / "logs")

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import qt_showcase_screenshots as showcase  # noqa: E402

import core  # noqa: E402
from folith.qt import adapter, bootstrap  # noqa: E402
from folith.qt import window as window_module  # noqa: E402

#: 长中文文件名任务：交付页顶部要能省略显示、悬停给出全名。
LONG_NAME = ("2026 年度第二季度跨区域合规审查与风险评估工作的最终交付说明"
             "（修订版，含附件与附录）.docx")

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    suffix = f"｜{detail}" if detail and not ok else ""
    print(("PASS " if ok else "FAIL ") + name + suffix, flush=True)
    if not ok:
        FAILURES.append(name)
    return ok


def snapshot_fingerprint(folder: Path) -> dict:
    """快照目录里每个文件的 sha256 + mtime（目录本身也算一条）。"""
    rows = {path.relative_to(folder).as_posix():
            (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in sorted(folder.rglob("*")) if path.is_file()}
    rows["."] = ("", folder.stat().st_mtime_ns)
    return rows


def clone_job(source_job: str, job_id: str, filename: str) -> None:
    """从已冻结任务复制一份工作状态（换文件名），用于长中文文件名截图。"""
    state = copy.deepcopy(core.load_job_state(source_job) or {})
    state["filename"] = filename
    state["delivery_status"] = "draft"
    for key in ("latest_delivery_snapshot_version", "delivery_approval",
                "delivery_approved_by_human"):
        state.pop(key, None)
    core.save_source(job_id, core.load_source(source_job))
    core.save_job_state(job_id, state)
    core.update_runtime_state(job_id, status="completed", phase="completed",
                              phase_label="已完成", last_event="completed")


def main() -> int:
    shutil.rmtree(WORK, ignore_errors=True)
    DATA.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    paths = bootstrap.prepare_environment(data_dir=DATA, log_dir=WORK / "logs")
    ids = showcase.seed(core, DATA, showcase.DEMOS["zh-CN"])
    check("seed 演示任务（真实 core 数据）",
          len(core.list_jobs()) >= 3 and bool(ids.get("fin")))

    # ---- 真实冻结：showcase-fin 已完成翻译与审校 ----
    approved, ok, errors = core.approve_delivery(
        "showcase-fin", note="交付工作区证据（真实快照）", actor="evidence")
    check("approve_delivery 冻结真实快照", ok, "；".join(errors or []))
    if not ok:
        return 1
    version = int(approved["latest_delivery_snapshot_version"])
    manifest = next(item for item in core.list_delivery_snapshots("showcase-fin")
                    if item.get("snapshot_version") == version)
    asset_names = [str(item.get("name")) for item in manifest.get("assets") or []]
    folder = adapter.delivery_folder_path("showcase-fin", version)
    check("快照目录与清单一致",
          bool(asset_names) and all((folder / name).is_file() for name in asset_names),
          f"{asset_names}")

    # 长中文文件名任务：同一份源文/译文，只换文件名，再真实冻结一次。
    clone_job("showcase-fin", "evidence-long-name", LONG_NAME)
    long_ok, long_errors = core.approve_delivery(
        "evidence-long-name", note="长文件名证据", actor="evidence")[1:]
    check("长文件名任务冻结", long_ok, "；".join(long_errors or []))

    # ---- 真实窗口 ----
    app = window_module.build_application([])
    config = adapter.EngineConfig(
        provider="DeepSeek", model="deepseek-v4-flash", api_key="demo",
        target_lang="简体中文")
    window = window_module.MainWindow(config, paths, None)
    window.resize(1440, 900)
    window.show()

    def pump(seconds: float = 1.2) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)

    def shot(name: str) -> None:
        pump(1.2)
        target = OUT / f"{name}.png"
        image = window.grab()
        image.save(str(target))
        check(f"截图 {target.relative_to(ROOT)}",
              target.is_file() and target.stat().st_size > 0,
              f"size={target.stat().st_size if target.exists() else 0}")

    pump(2.0)
    try:
        showcase.open_job_and_wait(window, "showcase-fin")
        window.workbench.show_page("deliver")
        pump(1.5)
        panel = window.workbench.delivery
        check("交付页列出真实冻结资产",
              sorted(panel.file_names()) == sorted(asset_names),
              f"{panel.file_names()} != {asset_names}")
        check("交付页顶部统计来自同一 manifest",
              panel.meta_labels["count"].text() == f"{len(asset_names)} 个文件",
              panel.meta_labels["count"].text())

        # 1) 冻结（浅色）
        window.settings.setValue(window.THEME_KEY, "light")
        window.settings.sync()
        window.apply_theme_preference()
        pump(1.0)
        shot("delivery-frozen-light")

        # 2) 冻结（深色）
        window.settings.setValue(window.THEME_KEY, "dark")
        window.settings.sync()
        window.apply_theme_preference()
        pump(1.0)
        shot("delivery-frozen-dark")
        window.settings.setValue(window.THEME_KEY, "light")
        window.settings.sync()
        window.apply_theme_preference()
        pump(0.8)

        # 3) 窄窗 820x760：内容列不得横向溢出
        window.resize(820, 760)
        pump(1.0)
        check("窄窗内容列不溢出",
              panel._column.width() <= 820
              and panel.minimumSizeHint().width() <= 820,
              f"column={panel._column.width()} min={panel.minimumSizeHint().width()}")
        shot("delivery-narrow-820x760")
        window.resize(1440, 900)
        pump(0.8)

        # 4) 技术附件：键盘空格展开（整条折叠头可聚焦）
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        header = panel.technical.header
        header.setFocus()
        QTest.keyClick(header, Qt.Key.Key_Space)
        pump(0.6)
        check("技术附件键盘展开", panel.technical.is_open() is True)
        technical_group = panel.model().technical
        check("技术附件行与当前模型一致",
              len(panel.technical.rows) == (technical_group.count
                                            if technical_group else 0),
              f"{len(panel.technical.rows)} 行")
        shot("delivery-technical-expanded")
        QTest.keyClick(header, Qt.Key.Key_Space)
        pump(0.4)
        check("技术附件再按空格收起", panel.technical.is_open() is False)

        # ---- 真实动作（全部只读快照 + 写 tmp）----
        actions = WORK / "actions"
        actions.mkdir(parents=True, exist_ok=True)
        before = snapshot_fingerprint(folder)
        assets = core.delivery_snapshot_assets("showcase-fin", version)

        archive_name = adapter.delivery_archive_name("showcase-fin", version)
        archive = actions / archive_name
        adapter.save_delivery_archive("showcase-fin", version, archive)
        with zipfile.ZipFile(archive) as bundle:
            members = set(bundle.namelist())
            content_ok = members == set(asset_names) | {showcase_snapshots_manifest()}
            bytes_ok = all(bundle.read(name) == assets[name] for name in asset_names)
        check("ZIP 保存并逐项校验", archive.is_file() and content_ok and bytes_ok,
              f"{sorted(members)}")

        for name in asset_names:
            default_name = adapter.delivery_default_asset_name(
                "showcase-fin", version, name)
            target = actions / "copies" / default_name
            adapter.save_delivery_asset_copy("showcase-fin", version, name, target)
            check(f"单文件另存 {name}",
                  target.is_file() and target.read_bytes() == assets[name])
        try:
            adapter.save_delivery_asset_copy(
                "showcase-fin", version, "not-in-snapshot.docx",
                actions / "rejected.docx")
        except adapter.AdapterError:
            check("未知资产名被拒绝", not (actions / "rejected.docx").exists())
        else:
            check("未知资产名被拒绝", False, "没有抛 AdapterError")

        # 打开文件夹：适配器注入 opener（不启动 Finder/Explorer）。
        opened: list[Path] = []
        result = adapter.open_delivery_folder(
            "showcase-fin", version,
            opener=lambda path: opened.append(Path(path)) or SimpleNamespace(
                ok=True, detail=""))
        check("适配器打开交付文件夹（注入 opener）",
              result.get("ok") is True and opened == [folder], f"{result}")

        # 窗口层同样走注入：替换 file_actions.open_directory 后调用真实处理器。
        window_opened: list[Path] = []
        original_open = adapter.file_actions.open_directory
        adapter.file_actions.open_directory = (
            lambda path: window_opened.append(Path(path)) or SimpleNamespace(
                ok=True, detail="已打开"))
        try:
            window.open_delivery_folder(version, job_id="showcase-fin")
            pump(0.4)
        finally:
            adapter.file_actions.open_directory = original_open
        check("窗口层打开交付文件夹（注入 opener）",
              window_opened == [folder] and window.failures == [], f"{window_opened}")

        # 复制路径：读系统剪贴板核对（offscreen 平台同样可用）。
        path = str(folder / "translation.docx")
        window.copy_path(path)
        pump(0.2)
        check("复制路径进剪贴板",
              app.clipboard().text() == path,
              f"clipboard={app.clipboard().text()!r}")

        # 文件信息：自动化运行只记一条瞬时消息，不弹窗。
        window.show_delivery_asset_info("translation.docx", version,
                                        job_id="showcase-fin")
        pump(0.2)
        check("文件信息来自真实文件",
              "translation.docx" in str(window._last_notice)
              and "DOCX" in str(window._last_notice),
              str(window._last_notice))

        after = snapshot_fingerprint(folder)
        check("动作前后冻结快照逐字节未变", after == before,
              f"changed={sorted(set(after) ^ set(before)) or 'content'}")

        # ---- 长中文文件名任务 ----
        showcase.open_job_and_wait(window, "evidence-long-name")
        window.workbench.show_page("deliver")
        pump(1.5)
        long_panel = window.workbench.delivery
        check("长文件名任务交付页已冻结", long_panel.model().frozen is True)
        label = long_panel.filename_label
        check("长文件名完整保留在控件与悬停提示里",
              label.fullText() == LONG_NAME and label.toolTip() == LONG_NAME,
              f"{label.fullText()!r} / {label.toolTip()!r}")
        # 窄窗里长名字必须省略显示，而不是把布局撑开或裁成半句。
        window.resize(900, 760)
        pump(0.8)
        check("窄窗长文件名省略显示",
              label.text() != LONG_NAME and label.toolTip() == LONG_NAME,
              label.text())
        window.resize(1440, 900)
        pump(0.8)
        shot("delivery-long-chinese-filename")

        # ---- 未冻结（门禁页）----
        showcase.open_job_and_wait(window, "showcase-book")
        window.workbench.show_page("deliver")
        pump(1.5)
        gate_panel = window.workbench.delivery
        check("未冻结任务显示门禁而不是空清单",
              gate_panel.model().frozen is False
              and gate_panel.empty_state.isHidden() is False
              and gate_panel.header.gate.isHidden() is False)
        shot("delivery-pre-delivery-gate")

        check("全程没有窗口级失败", window.failures == [], str(window.failures))
    finally:
        window.timer.stop()
        window._closing_accepted = True
        window.close()
        pump(0.5)

    print(f"\n证据目录：{OUT.relative_to(ROOT)}")
    print(f"冻结快照：{folder.relative_to(ROOT)}（v{version}，{len(asset_names)} 个文件）")
    if FAILURES:
        print(f"FAILED {len(FAILURES)} 项：" + "、".join(FAILURES))
        return 1
    print("全部 PASS")
    return 0


def showcase_snapshots_manifest() -> str:
    """ZIP 里额外带一份快照清单（与 ``adapter.delivery_archive_bytes`` 同布局）。"""
    from folith import snapshots

    return snapshots.MANIFEST_FILE


if __name__ == "__main__":
    raise SystemExit(main())
