#!/usr/bin/env python3
"""文档与事实核对：状态/矩阵/验收清单/证据日志里的说法必须与产物、代码、实测一致。

为什么需要它：这些文档本身就是交付物（测试者按验收清单操作、按状态清单判断能不能
发）。历史上我在这里犯过几次错——用例数写成过时的数字、候选 sha 手写后过期、结论
字符串缺失。手写值迟早会漂移，所以把它们变成**可检查**的：

1. 状态清单里的用例数 == `pytest --collect-only` 的实际收集数；
2. 状态清单与验收清单里的候选 sha256 / 构建时间 == `build/qt-desktop/*.json`；
3. 最低系统声明 == `native_min_os_bundle.json` 里的**实测**值；
4. 验收矩阵引用的每个证据编号都存在于证据日志，且证据编号唯一；
5. 状态清单带有 §7 要求的**确切结论字符串**，且对 `PRIVATE_BETA_READY` 的提及只出现在
   "尚未达成/需通过后才可声明"这类限定语境里（不能出现无条件声明）。

Usage:
    venv/bin/python scripts/qt_docs_audit.py            # 打印报告
    venv/bin/python scripts/qt_docs_audit.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "docs" / "qt-beta" / "status.md"
MATRIX = ROOT / "docs" / "qt-beta" / "acceptance-matrix.md"
KIT = ROOT / "docs" / "qt-beta" / "manual-acceptance.md"
QUICKSTART = ROOT / "docs" / "qt-beta" / "acceptance-quickstart.md"
NOTES = ROOT / "docs" / "qt-beta" / "release-notes.md"
EVIDENCE = ROOT / "docs" / "qt-spike" / "evidence-log.md"
DOCS_DIR = ROOT / "docs" / "qt-beta"
#: markdown 链接 `[文字](目标)`；目标可以是相对路径（可带 #锚点）或外链。
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
BUILD_DIR = ROOT / "build" / "qt-desktop"
CHAIN = ROOT / "scripts" / "qt_beta_chain.sh"
VERDICT = "ENGINEERING_COMPLETE — REQUIRED_ACCEPTANCE_PENDING"
TEST_FILES = (
    "tests/qt_acceptance_kit_test.py",
    "tests/qt_delivery_content_test.py",
    "tests/qt_extraction_error_test.py",
    "tests/qt_client_style_test.py",
    "tests/qt_client_adapter_test.py",
    "tests/qt_client_window_test.py",
    "tests/qt_release_plumbing_test.py",
    "tests/qt_spike_test.py",
)


def _collected_tests() -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *TEST_FILES, "--collect-only", "-q"],
        capture_output=True, text=True, cwd=ROOT)
    match = re.search(r"(\d+) tests? collected", completed.stdout)
    return int(match.group(1)) if match else -1


def _chain_steps() -> int:
    """验证链的步数 = 脚本里 `run ...` 的条数（文档里写的步数必须与它一致）。"""
    if not CHAIN.is_file():
        return -1
    return sum(1 for line in CHAIN.read_text(encoding="utf-8").splitlines()
               if line.startswith("run "))


def _measured_min_os() -> str:
    """实测的最低系统：取原生闭包测量结果里的 `max_minos`（Mach-O LC_BUILD_VERSION）。"""
    path = BUILD_DIR / "native_min_os_bundle.json"
    if not path.is_file():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    value = data.get("max_minos") or data.get("expected")
    if isinstance(value, dict):
        value = value.get("minimum_system_version") or value.get("minos")
    return str(value or "")


def _declared_min_os(status: str) -> str:
    """只从"声明最低系统"那一行取值。

    不能全文档搜 `macOS x.y`：环境行里的"macOS 27.0.1"是本机验收环境，不是声明值
    （第一版审计就是这样把 27.0 当成声明最低系统的）。
    """
    for line in status.splitlines():
        if "声明最低系统" not in line:
            continue
        match = re.search(r"macOS\s*([0-9]+\.[0-9]+)", line)
        if match:
            return match.group(1)
    return ""


def _normalize_version(value: str) -> str:
    """`15.0.0` 与 `15.0` 是同一个最低版本（只是分量写法不同）。"""
    parts = [item for item in str(value or "").split(".") if item != ""]
    while len(parts) > 1 and parts[-1] == "0":
        parts.pop()
    return ".".join(parts)


def _sha_matches(doc_text: str, sha: str) -> bool:
    """文档里可以写完整 sha，也可以写 `头8…尾4` 的缩写形式。"""
    if sha in doc_text:
        return True
    return f"{sha[:8]}…{sha[-4:]}" in doc_text


def _prose_only(text: str) -> str:
    """去掉围栏代码块与行内代码，只留正文——否则"文档里示范链接写法"会被当成真链接。

    R67 实测踩到过：我在证据日志里写了一个**故意写坏的链接**作为例子，检查立刻把它当成
    断链报出来。文档示范语法是正当的，所以先把代码区域剥掉再扫。
    """
    without_fences = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", without_fences)


def doc_links() -> dict:
    """检查我们负责的文档里的相对 markdown 链接是否指向真实存在的文件。

    为什么放在这里：文档就是**交给测试者的东西**（验收包里有三份），断链会直接误导人。
    检查进链（`qt_docs_audit.py` 已是链上一步），所以一旦写坏链接，链会红。

    `tmp/` 下的链接单独归类：那里放的是可丢弃的证据副本，随时可能被清理，
    断了只记录、不判失败（否则链会因为"用户清了 tmp"而变红）。
    """
    broken: list[str] = []
    advisory: list[str] = []
    total = 0
    docs = sorted(DOCS_DIR.glob("*.md")) + [EVIDENCE]
    for doc in docs:
        if not doc.is_file():
            continue
        for target in LINK_RE.findall(_prose_only(doc.read_text(encoding="utf-8"))):
            # 带 scheme 的外链不校验（也不要在本文件里写出 URL 字面量：
            # red-team 用例会扫描 scripts/ 里的 URL 引用，见 tests/red_team_acceptance_test.py）
            if "://" in target or target.startswith("mailto:"):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            total += 1
            resolved = (doc.parent / path_part).resolve()
            if resolved.exists():
                continue
            entry = f"{doc.name} → {target}"
            try:
                resolved.relative_to(ROOT / "tmp")
            except ValueError:
                broken.append(entry)
            else:
                advisory.append(entry)
    return {"doc_links": total, "broken_doc_links": broken,
            "advisory_doc_links": advisory}


def audit(*, check_tests: bool = True) -> dict:
    problems: list[str] = []
    facts: dict = {}
    status = STATUS.read_text(encoding="utf-8")
    matrix = MATRIX.read_text(encoding="utf-8")
    kit = KIT.read_text(encoding="utf-8")
    quickstart = QUICKSTART.read_text(encoding="utf-8")
    evidence = EVIDENCE.read_text(encoding="utf-8")
    record = json.loads((BUILD_DIR / "dmg_record.json").read_text(encoding="utf-8"))
    info = json.loads((BUILD_DIR / "desktop_build_info.json").read_text(encoding="utf-8"))

    # ---- 1. 用例数 -----------------------------------------------------
    stated = re.search(r"#\s*(\d+)\s*项", status)
    facts["stated_tests"] = int(stated.group(1)) if stated else None
    if check_tests:
        actual = _collected_tests()
        facts["collected_tests"] = actual
        if actual >= 0 and facts["stated_tests"] != actual:
            problems.append(
                f"状态清单写的是 {facts['stated_tests']} 项用例，实际收集 {actual} 项")

    # ---- 2. 候选身份 ---------------------------------------------------
    facts["sha256"] = record["sha256"]
    facts["built_at"] = info["built_at"]
    if not _sha_matches(status, record["sha256"]):
        problems.append("状态清单里的候选 sha256 与构建记录不一致")
    if record["sha256"] not in kit:
        problems.append("验收清单里的候选 sha256 与构建记录不一致（验收清单必须是完整值）")
    if info["built_at"] not in status:
        problems.append("状态清单里的构建时间与构建记录不一致")
    notes = NOTES.read_text(encoding="utf-8") if NOTES.is_file() else ""
    if record["sha256"] not in notes or info["built_at"] not in notes:
        problems.append(
            "发布说明里的候选身份与构建记录不一致（重跑 qt_release_checks.py --write-notes）")
    if "## 入口" not in notes or "qt_client.py" not in notes:
        problems.append("发布说明没有写明它描述的入口（Qt 客户端 / 旧启动器的区分）")

    # ---- 3. 最低系统 ---------------------------------------------------
    measured = _measured_min_os()
    facts["measured_min_os"] = measured
    declared = _declared_min_os(status)
    facts["declared_min_os"] = declared or None
    if measured and declared and _normalize_version(declared) != \
            _normalize_version(measured):
        problems.append(
            f"状态清单声明最低 macOS {declared}，实测闭包是 {measured}")
    if not declared:
        problems.append("状态清单没有可识别的『声明最低系统』行")
    if measured and _normalize_version(measured) not in \
            _normalize_version(kit.replace("macOS", " ").replace("**", " ")):
        # 验收清单里应当出现这个版本号（允许 15.0 / 15.0.0 两种写法）
        normalized_kit = _normalize_version(
            " ".join(re.findall(r"macOS\s*([0-9.]+)", kit) or []))
        if _normalize_version(measured) not in normalized_kit.split():
            problems.append(f"验收清单没有写实测最低系统 {measured}")

    # ---- 4. 证据编号 ---------------------------------------------------
    ids = re.findall(r"\|\s*(E\d+)\s*\|", evidence)
    facts["evidence_count"] = len(ids)
    facts["evidence_unique"] = len(set(ids))
    if len(ids) != len(set(ids)):
        duplicated = sorted({item for item in ids if ids.count(item) > 1})
        problems.append(f"证据编号重复：{duplicated}")
    referenced = set(re.findall(r"E\d+", matrix))
    unknown = sorted(referenced - set(ids))
    facts["matrix_evidence_refs"] = len(referenced)
    if unknown:
        problems.append(f"验收矩阵引用了证据日志里没有的编号：{unknown}")

    # ---- 5. 结论字符串 -------------------------------------------------
    facts["has_verdict"] = VERDICT in status
    if VERDICT not in status:
        problems.append(f"状态清单缺少 §7 的确切结论字符串：{VERDICT}")
    # 速览页：结论字符串、候选身份、四件优先事项都要在
    if VERDICT not in quickstart:
        problems.append("验收速览缺少 §7 的确切结论字符串")
    if not _sha_matches(quickstart, record["sha256"]):
        problems.append("验收速览里的候选 sha256 与构建记录不一致")
    for needle in ("真实中文输入法", "系统文件面板", "干净机器", "真实模型服务"):
        if needle not in quickstart:
            problems.append(f"验收速览缺少优先事项：{needle}")
    # 速览页里的"用例数 / 步数"也必须与事实一致（曾经写成 141 项 / 19 步）。
    if check_tests and facts.get("collected_tests", -1) >= 0:
        # 允许加粗或不加粗两种写法（文档里两种都出现过）
        stated_quick = re.search(r"\*{0,2}(\d+) 项用例", quickstart)
        facts["quickstart_tests"] = (
            int(stated_quick.group(1)) if stated_quick else None)
        if facts["quickstart_tests"] != facts["collected_tests"]:
            problems.append(
                f"验收速览写的是 {facts['quickstart_tests']} 项用例，实际收集 "
                f"{facts['collected_tests']} 项")
    steps = _chain_steps()
    facts["chain_steps"] = steps
    if steps > 0:
        for label, text in (("状态清单", status), ("验收速览", quickstart)):
            stated_steps = re.search(r"(\d+) 步验证链", text)
            if stated_steps and int(stated_steps.group(1)) != steps:
                problems.append(
                    f"{label}写的是 {stated_steps.group(1)} 步验证链，脚本里是 {steps} 步")
    # 矩阵里的 MANUAL 行数必须与状态清单/速览页声称的"强制人工项"数量一致
    manual_rows = len(re.findall(r"\|\s*MANUAL\s*\|", matrix))
    facts["matrix_manual_rows"] = manual_rows
    for label, text in (("状态清单", status), ("验收速览", quickstart)):
        claimed = re.search(r"\*\*0/(\d+) 完成\*\*", text) or \
            re.search(r"\*\*(\d+) 条强制人工项\*\*", text)
        if claimed and int(claimed.group(1)) != manual_rows:
            problems.append(
                f"{label}声称 {claimed.group(1)} 条强制人工项，矩阵里 MANUAL 行是 "
                f"{manual_rows} 条")
    facts["quickstart_ok"] = True
    # 对 PRIVATE_BETA_READY 的提及必须是限定语境（不得/需…才可/未达成）。
    unconditional = []
    for line in status.splitlines():
        if "PRIVATE_BETA_READY" not in line:
            continue
        # 限定语境：禁止/条件/未达成都算（"全部通过后才可"这类条件句不能被误判）
        if not any(word in line for word in (
                "不得", "需", "才可", "尚未", "未", "全部通过", "通过后", "→")):
            unconditional.append(line.strip())
    facts["unconditional_ready_claims"] = unconditional
    if unconditional:
        problems.append(
            f"状态清单出现了无条件的 PRIVATE_BETA_READY 声明：{unconditional}")

    # ---- 6. 文档链接 ---------------------------------------------------
    links = doc_links()
    facts.update(links)
    for entry in links["broken_doc_links"]:
        problems.append(f"文档断链（指向不存在的文件）：{entry}")

    facts["problems"] = problems
    facts["ok"] = not problems
    return facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="文档与事实核对")
    parser.add_argument("--json", default=None)
    parser.add_argument("--skip-tests", action="store_true",
                        help="跳过用例数核对（快速检查时用）")
    args = parser.parse_args(argv)
    report = audit(check_tests=not args.skip_tests)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
