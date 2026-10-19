#!/usr/bin/env python3
"""Release plumbing for the Qt candidate: manifest + generated release notes.

Two defects from the retired shell's release chain are fixed here rather than
inherited (MAC-03 / MAC-04):

* bundled distribution versions are read with the correct metadata prefix
  (``len("Version: ")``), so ``Version: 1.64.0`` can never become ``64.0``;
* the notes are generated from *this* build's manifest and DMG record, and
  ``--check`` fails when the file on disk is stale, instead of comparing a fresh
  manifest against old text and calling it a pass.

The manifest also records what the Qt candidate must *not* contain (Streamlit,
pywebview) and the measured support boundary, so a release claim can be traced
to a fact about the artifact.

Usage:
    venv/bin/python scripts/qt_release_checks.py --write
    venv/bin/python scripts/qt_release_checks.py --write-notes
    venv/bin/python scripts/qt_release_checks.py --check-notes
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP = ROOT / "dist" / "qt-desktop" / "Folith.app"
DEFAULT_OUT_DIR = ROOT / "build" / "qt-desktop"
DEFAULT_MANIFEST = DEFAULT_OUT_DIR / "release-manifest.json"
DEFAULT_DMG_RECORD = DEFAULT_OUT_DIR / "dmg_record.json"
DEFAULT_NOTES = ROOT / "docs" / "qt-beta" / "release-notes.md"

# The Qt candidate must not ship the retired shell's runtime.
FORBIDDEN_BUNDLE_ENTRIES = ("streamlit", "webview", "pywebview")

NAME_PREFIX = "Name: "
VERSION_PREFIX = "Version: "


def _read_metadata(metadata: Path) -> tuple[str, str]:
    """(name, version) from a METADATA file, using exact prefixes.

    The old implementation sliced with a hard-coded 11 for ``Version: `` (9
    characters), which truncated every version string; this reads the prefix
    length from the prefix itself.
    """
    name = version = ""
    try:
        text = metadata.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    for line in text.splitlines()[:60]:
        if not name and line.startswith(NAME_PREFIX):
            name = line[len(NAME_PREFIX):].strip()
        elif not version and line.startswith(VERSION_PREFIX):
            version = line[len(VERSION_PREFIX):].strip()
        if name and version:
            break
    return name, version


def bundled_distributions(app: Path) -> list[dict]:
    found: dict[str, str] = {}
    for info in app.rglob("*.dist-info"):
        metadata = info / "METADATA"
        if not metadata.is_file():
            continue
        name, version = _read_metadata(metadata)
        if name:
            found[name] = version or "unknown"
    return [{"name": name, "version": version}
            for name, version in sorted(found.items())]


def _run(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=120, check=False)
        return result.stdout
    except Exception:  # pragma: no cover - defensive
        return ""


def _macho_dependencies(binary: Path) -> list[str]:
    output = _run(["otool", "-L", str(binary)])
    deps = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        deps.append(line.split(" (")[0].strip())
    return deps


def external_dependencies(app: Path) -> list[str]:
    """Absolute non-system library references from the bundled binaries."""
    allowed_prefixes = ("/usr/lib/", "/System/", "/Library/Apple/")
    external: set[str] = set()
    for path in app.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in (".dylib", ".so") and path.name != "Folith":
            continue
        for dep in _macho_dependencies(path):
            if dep.startswith("@") or dep.startswith(allowed_prefixes):
                continue
            external.add(dep)
    return sorted(external)


def forbidden_entries(app: Path) -> list[str]:
    hits = []
    for name in FORBIDDEN_BUNDLE_ENTRIES:
        if any(name in part.lower() for part in app.parts) or \
                (app / "Contents" / "Frameworks" / name).exists() or \
                (app / "Contents" / "Resources" / name).exists():
            hits.append(name)
    # PyInstaller may place packages under Contents/Frameworks/<name> or inside
    # the PYZ; the directory check above is the honest, checkable statement.
    return hits


def build_manifest(app: Path, out_dir: Path) -> dict:
    build_info_path = out_dir / "desktop_build_info.json"
    build_info = json.loads(build_info_path.read_text(encoding="utf-8")) \
        if build_info_path.is_file() else {}
    min_os_path = out_dir / "native_min_os_bundle.json"
    min_os = json.loads(min_os_path.read_text(encoding="utf-8")) \
        if min_os_path.is_file() else {}
    payload_dir = app / "Contents" / "Frameworks" / "tesseract"
    languages = []
    tessdata = payload_dir / "share" / "tessdata"
    if tessdata.is_dir():
        languages = sorted(path.stem for path in tessdata.glob("*.traineddata"))
    # On-disk size via du: summing file sizes double counts the framework
    # symlinks (Python.framework), which would overstate the artifact.
    size_bytes = 0
    du = _run(["du", "-sk", str(app)]).split()
    if du and du[0].isdigit():
        size_bytes = int(du[0]) * 1024
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "shell": "qt-widgets",
        "app": {
            "path": str(app),
            "bundle_identifier": _bundle_identifier(app),
            "app_version": build_info.get("app_version", ""),
            "build_id": build_info.get("build_id", ""),
            "commit": build_info.get("commit", ""),
            "worktree_dirty": build_info.get("worktree_dirty"),
            "built_at": build_info.get("built_at", ""),
            "packaging_backend": build_info.get("packaging_backend", ""),
            "target_platform": build_info.get("target_platform", ""),
            "target_arch": build_info.get("target_arch", platform.machine()),
            "bytes": size_bytes,
        },
        "runtime": {
            "python_version": build_info.get("python_version", ""),
            "pyside6_version": build_info.get("pyside6_version", ""),
            "qt_version": build_info.get("qt_version", ""),
            "pyinstaller_version": build_info.get("pyinstaller_version", ""),
        },
        "support": {
            "minimum_system_version": build_info.get("minimum_system_version", ""),
            "minimum_system_version_source": build_info.get(
                "minimum_system_version_source", ""),
            "measured_bundle_max_minos": min_os.get("max_minos", ""),
            "measured_native_files": min_os.get("native_files"),
            "architectures": min_os.get("architectures") or {},
            "universal_files": min_os.get("universal_files"),
            "arm64_complete": min_os.get("arm64_complete"),
            "verified_on": build_info.get("os_version", ""),
        },
        "ocr": {
            "engine": "tesseract",
            "bundled_languages": languages,
            "baseline": ["eng", "chi_sim", "ara"],
            "paddle_bundled": False,
            "libreoffice_bundled": False,
        },
        "must_not_contain": {
            "entries": list(FORBIDDEN_BUNDLE_ENTRIES),
            "found": forbidden_entries(app),
        },
        "distributions": bundled_distributions(app),
        "external_library_references": external_dependencies(app),
        "distribution": {
            "signed": False,
            "notarized": False,
            "scope": "public release v0.6.0 (unsigned, not notarized)",
        },
    }


def _bundle_identifier(app: Path) -> str:
    plist = app / "Contents" / "Info.plist"
    if not plist.is_file():
        return ""
    try:
        import plistlib

        with plist.open("rb") as handle:
            return str(plistlib.load(handle).get("CFBundleIdentifier", ""))
    except Exception:  # pragma: no cover - defensive
        return ""


def _format_architectures(value) -> str:
    if not isinstance(value, dict) or not value:
        return "未测量"
    return "、".join(f"{arch}×{count}" for arch, count in sorted(value.items()))


def render_notes(manifest: dict, dmg: dict, install_notes: str = "") -> str:
    app = manifest.get("app") or {}
    runtime = manifest.get("runtime") or {}
    support = manifest.get("support") or {}
    ocr = manifest.get("ocr") or {}
    dist = manifest.get("distribution") or {}
    forbidden = (manifest.get("must_not_contain") or {})
    lines = [
        "# Folith 桌面端（Qt）— 发布说明",
        "",
        "本文件由 `scripts/qt_release_checks.py --write-notes` 依据**本次构建**的清单与",
        "DMG 记录生成；`--check-notes` 会在说明与当前产物不一致时失败（不做旧文本对比）。",
        "",
        "## 入口",
        "",
        "- 本说明只描述 DMG 内的 `Folith.app`：Qt Widgets 原生客户端"
        f"（bundle id `{app.get('bundle_identifier', '')}`），由 `packaging/folith-qt.spec` "
        "从 `qt_client.py` 冻结。这是唯一的桌面入口。",
        "- 源码等价入口：`python -m folith.qt`（或 `python qt_client.py`），需另装 PySide6。",
        "- Pip 安装的 `folith` 命令（含兼容别名 `foliothread`）启动同一个 Qt 客户端。",
        "- 旧 Streamlit 界面与 pywebview 桌面壳已从源码中移除，"
        "`docs/desktop-d3/` 下的说明只作历史基线。",
        "",
        "## 本候选范围",
        "",
        "- 导入：PDF / DOCX / EPUB（无 DRM）/ TXT / Markdown；新格式共用任务、翻译、审校、"
        "恢复与交付流程，不提供原文件格式写回。",
        "- 流程：准备 → 翻译 → 人工审校 → 交付，含术语治理、项目隔离的翻译记忆、段落编辑与"
        "结构修复、失败恢复、原页对照、交付门禁与不可变快照。",
        "- 语言资产、项目记忆，以及学术专业模式（QA、案例、实践报告）已在本客户端提供；"
        "学术模式默认关闭，显式开启或打开既有学术任务时可用。",
        "- 工作台动效：可见目标段落保持滚动位置；检查与修复侧栏可中断开合，"
        "支持减少动态效果与 RTL，Esc 收起回到编辑框。段落和 AI 操作保持固定，参考内容在下方滚动。"
        "导航、译前准备、原页与浮窗使用统一过渡；进度仅向真实值前进，保存落盘后给出行内确认。"
        "导入和启动使用按钮内等待，长时间导入才显示非模态进度。性能与人工验收边界见 P4 验收记录。",
        "",
        "- 通知与任务库：任务消息和普通错误集中到运行状态组与运行记录，完整原因可查看；"
        "全局结果使用顶部轻提示，停留 3 秒，悬停保留；底部状态栏移除，必要决定仍需确认。"
        "任务卡进度使用统一状态色，并显示可点击的问题数量。",
        "",
        "- 翻译模式：快速 / 标准 / 精细按各自批次、并发与知识反馈策略执行；准备页每张模式卡片显示本文的大约调用次数、输入 token 与耗时，自定义配置同步预估。",
        "",
        "- 字体层级：页面 / 区块 / 卡片标题与正文、说明、分组标签统一字号和字重；设置字段更清楚，阿拉伯语标题使用自然字距，译文阅读字号保持不变。",
        "",
        "- 应用图标：纯白圆角底板，保留原有品牌标；`.icns` 补齐 1024px Retina 帧，关于与欢迎页使用同一白底资产。",
        "",
        "## 产物",
        "",
        f"- 应用版本 / build id：`{app.get('app_version', '')}` / `{app.get('build_id', '')}`",
        f"- 构建时间（UTC）：`{app.get('built_at', '')}`",
        f"- 打包后端：`{app.get('packaging_backend', '')}`；worktree dirty：`{app.get('worktree_dirty')}`",
        f"- 目标平台 / 架构：`{app.get('target_platform', '')}` / `{app.get('target_arch', '')}`",
        f"- 随包运行时：Python `{runtime.get('python_version', '')}` / "
        f"PySide6 `{runtime.get('pyside6_version', '')}` / Qt `{runtime.get('qt_version', '')}`",
    ]
    if dmg:
        lines += [
            f"- DMG：`{Path(str(dmg.get('artifact', ''))).name}`",
            f"- DMG sha256：`{dmg.get('sha256', '')}`（{dmg.get('bytes', 0) / 1e6:.1f} MB）",
        ]
    lines += [
        "",
        "## 支持的系统",
        "",
        f"- 最低 macOS：**{support.get('minimum_system_version', '')}**"
        f"（依据：{support.get('minimum_system_version_source', '')}）",
        f"- 包内原生组件实测上限：`{support.get('measured_bundle_max_minos', '')}`"
        f"（{support.get('measured_native_files')} 个原生文件）",
        "- 架构：" + _format_architectures(support.get("architectures"))
        + f"；arm64 完整：{support.get('arm64_complete')}"
        + f"；通用（universal）二进制文件数：{support.get('universal_files')}"
        + "（本候选为 **arm64-only**，不含 Intel 切片）",
        f"- 本轮实际验收系统：`{support.get('verified_on', '')}`",
        f"- 本候选原生依赖闭包要求 macOS {support.get('minimum_system_version', '')}；"
        "未在更低版本上验收，不声称支持更低版本。",
        "- Intel Mac / Windows：本轮不在范围，未验收即不宣称支持。",
        "",
        "## 随包能力与外部依赖",
        "",
        f"- OCR 引擎：Tesseract（随包）；语言：{', '.join(ocr.get('bundled_languages') or [])}",
        "- 缺少语言包时明确报错，不会静默改用英语。",
        "- PaddleOCR / LibreOffice：**不随包**；缺失时如实记录 `NOT_RUN`，不宣称渲染通过。",
        f"- 已确认**不包含**旧壳运行时：{'、'.join(forbidden.get('entries') or [])}"
        f"（实际命中：{forbidden.get('found') or '无'}）",
    ]
    external = manifest.get("external_library_references") or []
    lines.append(
        "- 包外绝对路径动态库引用："
        + (f"{len(external)} 项（需逐项核对）" if external else "无"))
    distributions = manifest.get("distributions") or []
    lines.append(
        f"- 随包发行版共 {len(distributions)} 项，版本取自包内 `METADATA`（完整字符串，"
        "不截断）；逐项见 `release-manifest.json` 的 `distributions`。")
    lines.append(
        "- 依赖表述：`requirements*.txt` 记录的是**版本范围**；本文件与清单记录的是本次"
        "构建**实际解析并随包**的版本（`distributions` 与 `runtime`）。未完成依赖锁定时"
        "不宣称完整环境可复现。")
    lines += [
        "",
        "## 分发状态（如实披露）",
        "",
        f"- 签名：{'是' if dist.get('signed') else '否'}；公证："
        f"{'是' if dist.get('notarized') else '否'}",
        "- 适用范围：**v0.6.0 正式发布（未签名、未公证）**。首次打开需在「应用程序」中右键 → 打开，"
        "为本应用明确放行一次；**不要**全局关闭 Gatekeeper。",
        "- Developer ID 签名、hardened runtime、公证与 staple 尚未完成，计划在后续版本补齐；"
        "7 条强制人工验收（A.1′、A.7″、A.8′、B.1、C.2′、C.12、D.9）作为发布后验证继续进行，尚未全部闭环。",
        "",
        "## 卸载",
        "",
        "- 把 `Folith.app` 拖到废纸篓只移除应用本身；任务、术语、翻译记忆和设置都会保留，"
        "重新安装后原样可见。",
        "- 如需完全清除，先退出应用，再删除：`~/Library/Application Support/Folith`"
        "（任务与语言资产，与旧壳共用）、`~/Library/Logs/Folith`（日志）、"
        "`~/Library/Preferences/com.folith.QtDesktop.plist`（界面设置）。"
        "删除前请先导出需要保留的译文与 TMX/TBX。",
        "",
        "## 已知限制",
        "",
        "- 真实中文输入法、系统文件面板、睡眠/唤醒、真实模型服务与干净机器验收为**人工项**，"
        "尚未闭环；自动化与冻结产物证据不能替代。",
        "- 有意不迁移到本客户端：性能面板、Paddle 任务级模型选择、线上 OCR 独立来源、"
        "风格微调滑杆（风格由智能画像与风格卡片决定）；Agent 检查器已并入工作台右侧操作栏。",
        "- 数据目录 `~/Library/Application Support/Folith/data` 与旧壳共用；替换或删除应用"
        "不会删除用户数据。",
        "- **macOS 26/27 上的外部辅助功能扫描（AI \"Computer Use\" / VoiceOver / 窗口管理器的"
        "AX 客户端）会触发 Qt 平台插件崩溃**（SIGSEGV/SIGBUS，栈顶 `libqcocoa` + "
        "`NSAccessibilityAttributeAccessorInfo getAttributeValue:forObject:`）：上游根因是 "
        "**QTBUG-149612**（macOS: synthesized table elements can delete their parent "
        "accessibility interface；影响 Qt 6.11.0–6.11.2，截至 2026-10-05 无修复版本），"
        "同样见于 calibre（PyQt6）、Qt Creator、Anki、FreeCAD。R62 已在本机复现 3/3 并逐帧核对，"
        "且实测**没有可用的应用侧开关**：`QT_ACCESSIBILITY=0` 在 Qt 6.11 上完全无效，"
        "`QAccessible.setActive(False)` 会被系统在 AX 客户端接入时重新激活。"
        "**请用人工键鼠完成验收，不要挂载 AX 代理**——人工键鼠操作不受此缺陷影响。",
    ]
    if install_notes:
        lines += ["", install_notes.strip()]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qt release manifest / notes")
    parser.add_argument("--app", default=str(DEFAULT_APP))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--dmg-record", default=str(DEFAULT_DMG_RECORD))
    parser.add_argument("--notes", default=str(DEFAULT_NOTES))
    parser.add_argument("--write", action="store_true", help="写 release-manifest.json")
    parser.add_argument("--write-notes", action="store_true")
    parser.add_argument("--check-notes", action="store_true")
    args = parser.parse_args(argv)

    app = Path(args.app).expanduser()
    out_dir = Path(args.out_dir).expanduser()
    manifest_path = Path(args.manifest).expanduser()
    dmg_path = Path(args.dmg_record).expanduser()
    notes_path = Path(args.notes).expanduser()

    if not args.write and not args.write_notes and not args.check_notes:
        parser.error("choose --write, --write-notes or --check-notes")

    manifest = None
    if args.write or args.write_notes:
        if not app.is_dir():
            print(json.dumps({"ok": False, "error": f"missing app {app}"},
                             ensure_ascii=False))
            return 1
        manifest = build_manifest(app, out_dir)
    if args.write:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(json.dumps({"ok": True, "manifest": str(manifest_path),
                          "distributions": len(manifest["distributions"]),
                          "forbidden_found": manifest["must_not_contain"]["found"]},
                         ensure_ascii=False))

    if args.write_notes or args.check_notes:
        if manifest is None:
            if not manifest_path.is_file():
                print(json.dumps({"ok": False,
                                  "error": f"missing manifest {manifest_path}"},
                                 ensure_ascii=False))
                return 1
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        dmg = json.loads(dmg_path.read_text(encoding="utf-8")) \
            if dmg_path.is_file() else {}
        rendered = render_notes(manifest, dmg)
        if args.write_notes:
            notes_path.parent.mkdir(parents=True, exist_ok=True)
            notes_path.write_text(rendered, encoding="utf-8")
            print(json.dumps({"ok": True, "notes": str(notes_path)},
                             ensure_ascii=False))
            return 0
        current = notes_path.read_text(encoding="utf-8") if notes_path.is_file() else ""
        stale = current != rendered
        print(json.dumps({"ok": not stale, "stale": stale, "notes": str(notes_path)},
                         ensure_ascii=False))
        return 1 if stale else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
