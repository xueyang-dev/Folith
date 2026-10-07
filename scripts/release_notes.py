#!/usr/bin/env python3
"""Generate the desktop release notes from the release manifest (roadmap line 57/117/170).

The roadmap requires the release page to disclose, per platform, the languages and
external tools that are actually bundled, plus the supported systems.  Writing that
by hand invites drift, so the notes are generated from measured facts:

* `build/desktop/release-manifest.json` — produced by `scripts/release_checks.py`;
* `build/desktop/dmg_record.json` — produced by `scripts/build_dmg.sh`.

`--check` re-renders and compares against the file on disk (used by tests and CI to
catch stale notes).

Usage:
    venv/bin/python scripts/release_notes.py [--manifest ...] [--out ...] [--check]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "build" / "desktop" / "release-manifest.json"
DEFAULT_DMG_RECORD = ROOT / "build" / "desktop" / "dmg_record.json"
DEFAULT_OUT_DIR = ROOT / "docs" / "desktop-d3"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _mb(value: int | None) -> str:
    return f"{value / 1e6:.1f} MB" if value else "unknown"


def render(manifest: dict, dmg_record: dict) -> str:
    app = manifest.get("app") or {}
    runtime = manifest.get("runtime") or {}
    ocr = manifest.get("ocr") or {}
    artifact = manifest.get("artifact") or {}
    signature = manifest.get("signature") or {}
    systems = manifest.get("supported_systems") or {}
    verified = manifest.get("verified_environment") or {}
    distributions = manifest.get("bundled_distributions") or []
    requirements = manifest.get("pinned_requirements") or {}
    checks = {item["check"]: item for item in manifest.get("checks") or []}

    version = app.get("version") or dmg_record.get("app_version") or "unknown"
    dmg_name = Path(str(artifact.get("path") or dmg_record.get("artifact") or "")).name or "unknown"
    languages = ", ".join(f"`{name}`" for name in ocr.get("languages") or [])
    dist_lines = "\n".join(
        f"| {item['name']} | {item['version']} |" for item in distributions
    ) or "| (none recorded) | — |"
    req_lines = "\n".join(
        f"| `{name}` | {len(items)} pinned entries |" for name, items in sorted(requirements.items())
    ) or "| (none recorded) | — |"
    blocked = [item["check"] for item in (manifest.get("checks") or []) if item["ok"] is None]

    return f"""# Folith 桌面版发布说明（{version} · macOS Beta）

> 本文件由 `scripts/release_notes.py` 从发布清单自动生成，请勿手工编辑。
> 事实来源：`build/desktop/release-manifest.json`、`build/desktop/dmg_record.json`。

## 1. 版本与产物

| 项 | 值 |
| --- | --- |
| 应用版本 | `{version}` |
| build id | `{app.get('build_id')}` |
| 源码 commit | `{app.get('commit')}` |
| 工作区状态 | {"有未提交改动（开发构建）" if app.get('worktree_dirty') else "干净"} |
| 打包后端 | `{app.get('packaging_backend')}` |
| 产物 | `{dmg_name}`（{_mb(artifact.get('bytes') or dmg_record.get('bytes'))}） |
| sha256 | `{artifact.get('sha256') or dmg_record.get('sha256')}` |
| 格式 | `{artifact.get('format') or dmg_record.get('format')}`，`hdiutil verify` 退出码 {artifact.get('verify_exit_code')} |

## 2. 支持系统

| 平台 | 状态 |
| --- | --- |
| macOS | **声明最低 {systems.get('macos_minimum') or '未声明'}**，{systems.get('architecture')}（Apple Silicon） |
| Windows | {systems.get('windows')} |

- **实际验收环境**：`{verified.get('platform') or '未记录'}`（{verified.get('machine') or '—'}，Python {verified.get('python') or '—'}）。
- `{systems.get('macos_minimum') or '13.0'}` 是**声明的**最低版本，本版**未在 13.x 实机验证**；只把上述实际验收环境列为已验证系统。
- 首批只承诺 Apple Silicon；Intel Mac / Windows ARM64 不在本版承诺内。

## 3. 安装

1. 打开 `{dmg_name}`，把 `Folith.app` 拖入 `Applications`；
2. 首次启动若出现 Gatekeeper 提示：本产物**未签名、未公证**（见第 6 节），需在"系统设置 → 隐私与安全性"中允许，或右键"打开"；
3. 用户数据位于 `~/Library/Application Support/Folith/data`，日志位于 `~/Library/Logs/Folith`；删除应用不会删除数据。

## 4. 随包能力（不需要额外安装）

| 能力 | 实际随包内容 |
| --- | --- |
| 运行时 | Python {runtime.get('python_version')}、Streamlit {runtime.get('streamlit_version')}、pywebview {runtime.get('pywebview_version')}（PyInstaller {runtime.get('pyinstaller_version')}） |
| OCR 引擎 | Tesseract（随包，无需 Homebrew 或系统安装） |
| OCR 语言包 | {languages or '未记录'} —— 数据来源与版本记录随包（许可 `{ocr.get('licence')}`），并逐文件校验 sha256 |
| OCR 输出配置 | `configs/`（含 `tsv`）随包 |

**语言披露（roadmap 要求逐项写明）**：本版**只**验证并随包上述三种语言；其他语言即使 Tesseract 理论上支持，也**不在本版承诺内**。缺少所选语言包时应用会明确报错，不会静默改用 `eng`。

## 5. 外部依赖（可选 / 必需）

| 依赖 | 状态 |
| --- | --- |
| 模型服务（翻译/画像） | **必需**，由用户自行配置 API Key（本地 JSON 保存，不进入交付物、项目转移与诊断包） |
| LibreOffice | **可选**。仅用于交付渲染 QA；未安装时该步骤保持 `NOT_RUN`（不会被伪报为通过）。本版已支持在 `/Applications` 等标准安装位置发现它 |

## 6. 已知限制与未验证项（如实披露）

| 项 | 状态 |
| --- | --- |
| 代码签名 | **未签名**（`adhoc={str(signature.get('adhoc')).lower()}`，无 Developer ID） |
| 公证 / staple | **未公证**（`spctl` 判定：{(manifest.get('gatekeeper') or {}).get('verdict') or ('rejected' if (manifest.get('gatekeeper') or {}).get('exit_code') else 'unknown')}，退出码 {(manifest.get('gatekeeper') or {}).get('exit_code')}） |
| 真实下载后的 Gatekeeper 体验 | 未验证（无干净 Mac 与签名产物） |
| **干净机器验收（D2 完成条件）** | **未完成**：本版只在本机验证（见第 2 节实际验收环境）。验收手册与免 Python 的自动冒烟脚本已就绪：`docs/desktop-d2/clean-machine-acceptance.md` |
| 真实操作系统级中文输入法 | 未验证（无辅助功能授权） |
| 原生保存对话框 / 强制退出对话框点击 | 未验证（人工项） |
| 机器睡眠唤醒 | 未验证（人工项；断连与中断状态识别已实现并验证） |
| 真实 LibreOffice 渲染 | 未验证（本机未安装 LibreOffice） |
| Windows 版本 | **未提供**（无 Windows 主机，D2/D3 后续解决） |

自动检查中记为 BLOCKED 的项：{", ".join(f"`{name}`" for name in blocked) or "无"}。

## 7. 发行依赖与资源清单

包内实际收集到的发行版（{len(distributions)} 个）：

| 发行版 | 版本 |
| --- | --- |
{dist_lines}

仓库固定依赖清单：

| 文件 | 内容 |
| --- | --- |
{req_lines}

## 8. 校验方式

```bash
shasum -a 256 {dmg_name}   # 期望 {artifact.get('sha256') or dmg_record.get('sha256')}
hdiutil verify {dmg_name}
```

发布前自动校验（{sum(1 for item in (manifest.get('checks') or []) if item['ok'] is True)} 项通过、{len(blocked)} 项 BLOCKED）：

```bash
venv/bin/python scripts/release_checks.py --mount
```
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="generate desktop release notes")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--dmg-record", default=str(DEFAULT_DMG_RECORD))
    parser.add_argument("--out", default=None)
    parser.add_argument("--check", action="store_true",
                        help="verify the notes on disk match the manifest")
    args = parser.parse_args(argv)

    manifest = _load(Path(args.manifest))
    if not manifest:
        print(json.dumps({"error": f"no release manifest at {args.manifest}; run release_checks.py"},
                         indent=2))
        return 2
    dmg_record = _load(Path(args.dmg_record))
    version = (manifest.get("app") or {}).get("version") or "unknown"
    out = Path(args.out) if args.out else DEFAULT_OUT_DIR / f"release-notes-{version}.md"
    rendered = render(manifest, dmg_record)

    if args.check:
        current = out.read_text(encoding="utf-8") if out.is_file() else ""
        stale = current != rendered
        print(json.dumps({"ok": not stale, "path": str(out), "stale": stale,
                          "reason": "" if not stale else "notes do not match the release manifest"},
                         ensure_ascii=False, indent=2))
        return 0 if not stale else 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(out), "bytes": len(rendered.encode("utf-8"))},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
