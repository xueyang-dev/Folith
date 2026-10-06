#!/usr/bin/env python3
"""人工验收清单的候选身份：从构建记录生成，并检查是否过期。

为什么需要它：验收清单是**交付物的一部分**——它必须指向测试者手上那一份产物。
之前 sha256/构建时间是手写的，每次重建都会悄悄过期（测试者可能拿着旧哈希去核对
新包，或者根本不知道要核对什么）。这里把身份表变成生成的：

    scripts/qt_acceptance_kit.py --write    # 重建后刷新
    scripts/qt_acceptance_kit.py --check    # 过期则返回非零（进链）

清单里用一对标记包住身份表，生成时只替换标记之间的内容。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "docs" / "qt-beta" / "manual-acceptance.md"
STATUS = ROOT / "docs" / "qt-beta" / "status.md"
QUICKSTART = ROOT / "docs" / "qt-beta" / "acceptance-quickstart.md"
BUILD_DIR = ROOT / "build" / "qt-desktop"
BEGIN = "<!-- candidate-identity:begin -->"
END = "<!-- candidate-identity:end -->"
STATUS_BEGIN = "<!-- status-identity:begin -->"
STATUS_END = "<!-- status-identity:end -->"
DMG_BEGIN = "<!-- status-dmg:begin -->"
DMG_END = "<!-- status-dmg:end -->"


def _manifest() -> dict:
    path = BUILD_DIR / "release-manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _facts() -> dict:
    record = json.loads((BUILD_DIR / "dmg_record.json").read_text(encoding="utf-8"))
    info_path = BUILD_DIR / "desktop_build_info.json"
    info = json.loads(info_path.read_text(encoding="utf-8")) \
        if info_path.is_file() else {}
    manifest = _manifest()
    return {
        "pyinstaller_version": record.get("pyinstaller_version") or "",
        "app_version": (manifest.get("app") or {}).get("app_version")
        or info.get("app_version") or "",
        "dirty": info.get("worktree_dirty"),
        "artifact": Path(record.get("artifact") or "").name,
        "sha256": record.get("sha256") or "",
        "bytes": int(record.get("bytes") or 0),
        "built_at": info.get("built_at") or record.get("built_at") or "",
        "build_id": record.get("build_id") or info.get("build_id") or "",
        "minimum_system_version": record.get("minimum_system_version") or "",
        "target_arch": record.get("target_arch") or "",
        "signed": bool(record.get("signed")),
        "notarized": bool(record.get("notarized")),
    }


def render(facts: dict) -> str:
    mib = facts["bytes"] / (1024 * 1024)
    signature = ("已签名" if facts["signed"] else "未签名") + "、" + \
        ("已公证" if facts["notarized"] else "未公证")
    return "\n".join([
        BEGIN,
        "| 项 | 值 |",
        "| --- | --- |",
        f"| 安装包 | `dist/qt-desktop/{facts['artifact']}` |",
        f"| sha256 | `{facts['sha256']}` |",
        f"| 大小 | {facts['bytes']:,} 字节（{mib:.0f} MiB） |",
        f"| 构建时间 | `{facts['built_at']}`（build id `{facts['build_id']}`） |",
        f"| 声明最低系统 | **macOS {facts['minimum_system_version']}**"
        "（实测；未在更低版本验收，不声称支持） |",
        f"| 架构 | {facts['target_arch']}（Apple Silicon） |",
        f"| 签名 | **{signature}**（受邀 Private Beta） |",
        END,
    ])


def _split(text: str) -> tuple[str, str, str]:
    begin = text.index(BEGIN)
    end = text.index(END) + len(END)
    return text[:begin], text[begin:end], text[end:]


def _split_marked(text: str, begin_mark: str, end_mark: str) -> tuple[str, str, str]:
    begin = text.index(begin_mark)
    end = text.index(end_mark) + len(end_mark)
    return text[:begin], text[begin:end], text[end:]


def render_status_identity(facts: dict, manifest: dict) -> str:
    app = manifest.get("app") or {}
    deps = manifest.get("dependencies") or {}
    return "\n".join([
        STATUS_BEGIN,
        f"- 候选身份：`app_version={facts['app_version']}`、"
        f"`build_id={facts['build_id']}`、"
        f"`worktree_dirty={str(app.get('worktree_dirty', facts.get('dirty'))).lower()}`、"
        f"PyInstaller {facts.get('pyinstaller_version') or deps.get('pyinstaller_version') or '—'}、"
        f"built_at `{facts['built_at']}`",
        STATUS_END,
    ])


def render_status_dmg(facts: dict) -> str:
    mib = facts["bytes"] / (1024 * 1024)
    return "\n".join([
        DMG_BEGIN,
        f"| DMG | `dist/qt-desktop/{facts['artifact']}`"
        f"（{mib:.0f} MiB / {facts['bytes'] // 1_000_000} MB，"
        f"sha256 `{facts['sha256'][:8]}…{facts['sha256'][-4:]}`） |",
        DMG_END,
    ])


def write() -> int:
    facts = _facts()
    text = KIT.read_text(encoding="utf-8")
    head, _old, tail = _split(text)
    block = render(facts)
    KIT.write_text(head + block + tail, encoding="utf-8")
    print(block)

    quickstart = QUICKSTART.read_text(encoding="utf-8")
    head, _old, tail = _split(quickstart)
    quickstart = head + block + tail
    QUICKSTART.write_text(quickstart, encoding="utf-8")

    status = STATUS.read_text(encoding="utf-8")
    manifest = _manifest()
    for begin_mark, end_mark, rendered in (
        (STATUS_BEGIN, STATUS_END, render_status_identity(facts, manifest)),
        (DMG_BEGIN, DMG_END, render_status_dmg(facts)),
    ):
        head, _old, tail = _split_marked(status, begin_mark, end_mark)
        status = head + rendered + tail
    STATUS.write_text(status, encoding="utf-8")
    print("状态清单与验收速览身份已刷新")
    return 0


def check() -> int:
    text = KIT.read_text(encoding="utf-8")
    _head, current, _tail = _split(text)
    expected = render(_facts())
    # 验收清单与速览用同一张身份表；状态清单用的是另外两行（身份行 + DMG 行），
    # 因此要按各自的渲染结果比较，不能一把尺子量三个文件。
    facts = _facts()
    manifest = _manifest()
    targets = (
        (KIT, BEGIN, END, expected),
        (QUICKSTART, BEGIN, END, expected),
        (STATUS, STATUS_BEGIN, STATUS_END,
         render_status_identity(facts, manifest)),
        (STATUS, DMG_BEGIN, DMG_END, render_status_dmg(facts)),
    )
    for path, begin_mark, end_mark, want in targets:
        _head, block, _tail = _split_marked(
            path.read_text(encoding="utf-8"), begin_mark, end_mark)
        if block.strip() != want.strip():
            print(f"{path.name} 的候选身份已过期（{begin_mark}）：请运行 "
                  "`venv/bin/python scripts/qt_acceptance_kit.py --write`",
                  file=sys.stderr)
            print("--- 当前 ---\n" + block, file=sys.stderr)
            print("--- 期望 ---\n" + want, file=sys.stderr)
            return 1
    if current.strip() != expected.strip():
        print("验收清单的候选身份已过期：请运行 "
              "`venv/bin/python scripts/qt_acceptance_kit.py --write`",
              file=sys.stderr)
        print("--- 当前 ---\n" + current, file=sys.stderr)
        print("--- 期望 ---\n" + expected, file=sys.stderr)
        return 1
    print("验收清单的候选身份与构建记录一致")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="验收清单候选身份")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    return write() if args.write else check()


if __name__ == "__main__":
    sys.exit(main())
