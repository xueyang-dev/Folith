#!/usr/bin/env python3
"""验收包生成器：把候选 DMG + 三份文档 + 一页 README 收进一个可以直接发给测试者的目录。

用法：

    venv/bin/python scripts/qt_acceptance_bundle.py --write          # 生成 dist/qt-beta-handoff/
    venv/bin/python scripts/qt_acceptance_bundle.py --check          # 复核现有验收包与构建记录一致

诚实性约束：

* 身份（sha256 / 字节数 / built_at / build_id）**只从 `build/qt-desktop/` 的构建记录读**，
  写包时当场核对 DMG 的真实 sha256：不一致就失败退出，绝不写出一个"看起来对"的包。
* `--check` 在以下任一情况以非 0 退出：包不存在、文件缺失、sha 不符、manifest 与构建记录不一致、
  README 里的身份与记录不一致（也就是"包过期了"）。
* README 明确写未签名/未公证的一次性放行步骤，**绝不**建议全局关闭 Gatekeeper。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / "build" / "qt-desktop"
DEFAULT_DOCS = ROOT / "docs" / "qt-beta"
DEFAULT_OUT = ROOT / "dist" / "qt-beta-handoff"

#: 包内文件名 → 源文件（保持原名，这样文档之间的相互链接仍然有效）
DOC_FILES = {
    "acceptance-quickstart.md": "acceptance-quickstart.md",
    "manual-acceptance.md": "manual-acceptance.md",
    "status.md": "status.md",
}
README_NAME = "README-先读我.md"
MANIFEST_NAME = "manifest.json"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_identity(build_dir: Path) -> dict:
    """从构建记录读候选身份（权威来源），缺任何一个都算失败。"""
    record_path = build_dir / "dmg_record.json"
    info_path = build_dir / "desktop_build_info.json"
    problems = []
    if not record_path.is_file():
        problems.append(f"缺少构建记录：{record_path}")
    if not info_path.is_file():
        problems.append(f"缺少构建记录：{info_path}")
    if problems:
        raise SystemExit("；".join(problems))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    info = json.loads(info_path.read_text(encoding="utf-8"))
    return {
        "app_version": record.get("app_version"),
        "build_id": record.get("build_id"),
        "built_at": info.get("built_at") or record.get("built_at"),
        "sha256": record.get("sha256"),
        "bytes": record.get("bytes"),
        "minimum_system_version": record.get("minimum_system_version"),
        "target_arch": record.get("target_arch"),
        "signed": bool(record.get("signed")),
        "notarized": bool(record.get("notarized")),
        "dmg": Path(str(record.get("artifact") or "")).name,
        "artifact": str(record.get("artifact") or ""),   # 仅供本脚本定位文件，不写进 manifest
    }


def readme_text(identity: dict, dmg_name: str) -> str:
    size_mib = (int(identity["bytes"] or 0)) / (1024 * 1024)
    return f"""# Folith Qt 桌面端 — 受邀 Private Beta 验收包（先读我）

这个目录就是**全部要发给测试者的东西**：一个安装包 + 三份文档 + 本说明。

## 一、候选身份（请先核对）

| 项 | 值 |
| --- | --- |
| 安装包 | `{dmg_name}` |
| sha256 | `{identity['sha256']}` |
| 大小 | {int(identity['bytes'] or 0):,} 字节（{size_mib:.1f} MiB） |
| 版本 / 构建 | `{identity['app_version']}` / `{identity['build_id']}` |
| 构建时间 | `{identity['built_at']}` |
| 声明最低系统 | macOS {identity['minimum_system_version']}（实测；未在更低版本验收） |
| 架构 | {identity['target_arch']}（Apple Silicon） |
| 签名 | **未签名、未公证**（受邀测试分发） |

核对命令：

```sh
shasum -a 256 {dmg_name}
```

与上表的 sha256 逐字符比对。不一致就**不要**安装，先告诉我。

## 二、三步装起来

1. 双击 `{dmg_name}`，把 `Folith.app` 拖进「应用程序」；
2. 打开「应用程序」→ **右键** `Folith` → **打开** → 再点一次「打开」
   （未签名候选需要为本应用放行**一次**；**不要**全局关闭 Gatekeeper）；
3. 启动后在应用内「配置模型连接…」填好自己的服务地址与 Key（Key 只在应用里填，
   不要发到聊天里），点「测试连接」。

## 三、要做的验收（最短路径）

打开 `acceptance-quickstart.md`（一页速览）：里面是**优先四项**、**7 条强制人工项**和**回传模板**。
详细步骤在 `manual-acceptance.md`。当前状态与全部证据入口在 `status.md`。

**请用人工键鼠操作，不要挂载 AX 代理 / Computer Use**：macOS 上外部辅助功能扫描会触发上游 Qt 缺陷
（QTBUG-149612）导致进程原生崩溃，这与本候选无关，且实测没有任何应用侧开关能挡住它。人工键鼠不受影响。

## 四、已知限制（不必当缺陷回报）

- 未签名、未公证：首次需右键「打开」放行一次；不要求关闭系统安全设置。
- 声明最低 **macOS {identity['minimum_system_version']}**；13/14 未验收，不声称支持。
- 仅 **{identity['target_arch']}**（Apple Silicon）；Windows / Intel 不在本轮范围。
- 扫描件 OCR 随包 `eng`/`chi_sim`/`ara`；缺包会明确报出缺哪个，不会静默改用英语。
- LibreOffice 未随包：渲染 QA 记为 `NOT_RUN`，不阻塞可用 DOCX 的验收。
- 挂 AX 代理会崩进程（上游缺陷），但**不会弄坏任务**：崩溃前后任务数据逐项一致、可重开继续用；
  代价只有最后一次自动保存（1.5 秒）之后的输入。

## 五、回传

按 `acceptance-quickstart.md` 末尾的模板回传即可（候选 sha256 + 7 条强制项结果 + 遇到的原生崩溃 `.ips`）。

---
本文件由 `scripts/qt_acceptance_bundle.py` 生成，身份取自 `build/qt-desktop/dmg_record.json` 与
`desktop_build_info.json`；`manifest.json` 里有本目录每个文件的 sha256，可复核。
"""


def build_manifest(bundle: Path, identity: dict) -> dict:
    files = {}
    for path in sorted(bundle.iterdir()):
        if path.name == MANIFEST_NAME or not path.is_file():
            continue
        files[path.name] = {"sha256": sha256_of(path), "bytes": path.stat().st_size}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate": {key: value for key, value in identity.items() if key != "artifact"},
        "files": files,
    }


def write_bundle(args) -> int:
    build_dir = Path(args.build_dir).resolve()
    docs_dir = Path(args.docs_dir).resolve()
    out_dir = Path(args.out).resolve()
    identity = load_identity(build_dir)

    if args.dmg:
        dmg = Path(args.dmg).resolve()
    else:
        recorded = Path(str(identity.get("artifact") or ""))
        dmg = recorded if recorded.is_file() else (ROOT / "dist" / "qt-desktop" / str(identity["dmg"]))
    if not dmg.is_file():
        print(f"找不到 DMG：{dmg}", file=sys.stderr)
        return 1
    actual = sha256_of(dmg)
    if actual != identity["sha256"]:
        print(f"DMG 与构建记录不一致，拒绝写包：\n  实际 {actual}\n  记录 {identity['sha256']}", file=sys.stderr)
        return 1
    if int(identity["bytes"] or 0) and dmg.stat().st_size != int(identity["bytes"]):
        print(f"DMG 字节数与记录不一致：{dmg.stat().st_size} != {identity['bytes']}", file=sys.stderr)
        return 1

    missing = [name for name in DOC_FILES.values() if not (docs_dir / name).is_file()]
    if missing:
        print(f"缺少文档：{missing}", file=sys.stderr)
        return 1

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    shutil.copy2(dmg, out_dir / dmg.name)
    for target, source in DOC_FILES.items():
        shutil.copy2(docs_dir / source, out_dir / target)
    (out_dir / README_NAME).write_text(readme_text(identity, dmg.name), encoding="utf-8")
    manifest = build_manifest(out_dir, identity)
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"验收包已生成：{out_dir}")
    for name, info in manifest["files"].items():
        print(f"  {name}  {info['bytes']:>12,} 字节  {info['sha256'][:16]}…")
    return 0


def check_bundle(args) -> int:
    out_dir = Path(args.out).resolve()
    problems: list[str] = []
    if not out_dir.is_dir():
        print(f"验收包不存在：{out_dir}", file=sys.stderr)
        return 1
    manifest_path = out_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        print(f"缺少 {MANIFEST_NAME}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = load_identity(Path(args.build_dir).resolve())

    recorded = manifest.get("candidate") or {}
    for key in ("sha256", "build_id", "built_at", "app_version"):
        if str(recorded.get(key)) != str(identity.get(key)):
            problems.append(f"manifest.candidate.{key} 与构建记录不一致："
                            f"{recorded.get(key)!r} != {identity.get(key)!r}")

    files = manifest.get("files") or {}
    if str(identity["dmg"]) not in files:
        problems.append(f"manifest 里没有安装包 {identity['dmg']}")
    for name, info in files.items():
        path = out_dir / name
        if not path.is_file():
            problems.append(f"缺文件：{name}")
            continue
        if sha256_of(path) != info.get("sha256"):
            problems.append(f"{name} 的 sha256 与 manifest 不符（被改动或复制损坏）")
        if path.stat().st_size != info.get("bytes"):
            problems.append(f"{name} 的字节数与 manifest 不符")

    # 包里的文档必须与当前源文档一致：否则测试者读到的是过期说明（R67 实测踩到：
    # 生成包之后又改了 status/quickstart，包里的副本还是旧的，而 --check 当时看不出来）。
    docs_dir = Path(args.docs_dir).resolve()
    for target, source in DOC_FILES.items():
        bundled = out_dir / target
        original = docs_dir / source
        if bundled.is_file() and original.is_file() and sha256_of(bundled) != sha256_of(original):
            problems.append(f"{target} 与源文档不一致（文档已更新，验收包过期：请重跑 --write）")

    readme = out_dir / README_NAME
    if not readme.is_file():
        problems.append(f"缺少 {README_NAME}")
    else:
        text = readme.read_text(encoding="utf-8")
        if str(identity["sha256"]) not in text:
            problems.append(f"{README_NAME} 里的 sha256 与构建记录不一致（包过期了）")
        if "Gatekeeper" not in text or "全局关闭" not in text:
            problems.append(f"{README_NAME} 必须写明「不要全局关闭 Gatekeeper」")
        if "QTBUG-149612" not in text:
            problems.append(f"{README_NAME} 必须写明 AX 扫描的平台级限制")

    if problems:
        for problem in problems:
            print(f"  ✗ {problem}", file=sys.stderr)
        return 1
    print(f"验收包与构建记录一致：{out_dir}（{len(files)} 个文件）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="受邀 Private Beta 验收包生成/复核")
    parser.add_argument("--write", action="store_true", help="生成验收包")
    parser.add_argument("--check", action="store_true", help="复核验收包与构建记录一致")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="验收包目录")
    parser.add_argument("--dmg", default=None, help="指定 DMG（默认按构建记录里的 artifact）")
    parser.add_argument("--build-dir", default=str(BUILD_DIR))
    parser.add_argument("--docs-dir", default=str(DEFAULT_DOCS))
    args = parser.parse_args(argv)
    if args.write == args.check:
        parser.error("请给出 --write 或 --check 之一")
    return write_bundle(args) if args.write else check_bundle(args)


if __name__ == "__main__":
    raise SystemExit(main())
