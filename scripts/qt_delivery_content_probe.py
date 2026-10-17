#!/usr/bin/env python3
"""交付文件内容校验：把冻结产物写出的交付文件**读回来**逐项核对。

为什么需要它：链条里其它探针只证明"文件生成了"，而测试者拿到手第一个问题一定是
"这些文件能用吗、内容对不对"。本探针打开交付目录里的每个文件，和任务的
`state.json` 交叉核对：

* ``translation.docx``：标题 + 每段译文，且**逐段等于当前提交译文**（人工修改必须在里面）；
* ``bilingual.docx``：表格首行是 原文/译文 表头，之后每行一对，与 state 一致；
* ``terms.xlsx``：表头固定，行数等于术语条数（没有术语时只有表头——如实，不编内容）；
* ``delivery_manifest.json``：声明了生成资产、审校统计、提供方/模型，且状态为 final；
* ``segment_evidence.jsonl``：每段一条记录。

Usage:
    venv/bin/python scripts/qt_delivery_content_probe.py \
        --delivery-dir "tmp/qt-beta/final-real/交付 结果 中文" \
        --state tmp/qt-beta/final-real/data/<job>/state.json \
        --out tmp/qt-beta/delivery-content.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _paragraphs(path: Path) -> list[str]:
    import docx

    return [item.text.strip() for item in docx.Document(str(path)).paragraphs
            if item.text.strip()]


def _bilingual_rows(path: Path) -> list[list[str]]:
    import docx

    document = docx.Document(str(path))
    if not document.tables:
        return []
    return [[cell.text.strip() for cell in row.cells]
            for row in document.tables[0].rows]


def _terms(path: Path) -> tuple[list[str], list[list[str]]]:
    from openpyxl import load_workbook

    sheet = load_workbook(str(path)).active
    header = [sheet.cell(1, column).value for column in range(1, sheet.max_column + 1)]
    rows = [[sheet.cell(row, column).value
             for column in range(1, sheet.max_column + 1)]
            for row in range(2, sheet.max_row + 1)]
    return [str(item) for item in header], rows


def _asset(delivery_dir: Path, name: str) -> Path:
    """交付文件名带来源前缀（``<来源>_translation.docx``）；没有前缀的旧名也认。"""
    plain = delivery_dir / name
    if plain.exists():
        return plain
    prefixed = sorted(delivery_dir.glob(f"*_{name}"))
    return prefixed[0] if prefixed else plain


def check(delivery_dir: Path, state_path: Path) -> dict:
    problems: list[str] = []
    facts: dict = {}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    pairs = state.get("pairs") or []
    sources = [str(pair.get("source") or "") for pair in pairs]
    targets = [str(pair.get("target") or "") for pair in pairs]

    # ---- translation.docx ------------------------------------------------
    translation = _asset(delivery_dir, "translation.docx")
    if not translation.is_file():
        problems.append(f"缺少 translation.docx：{translation}")
    else:
        paragraphs = _paragraphs(translation)
        facts["translation"] = {"paragraphs": len(paragraphs),
                                "first": paragraphs[0] if paragraphs else ""}
        # 交付文档带一个标题行（"译文"），其后逐段对应译文。
        body = [text for text in paragraphs if text != paragraphs[0]] \
            if paragraphs else []
        if len(body) != len(targets):
            problems.append(
                f"translation.docx 正文段落数 {len(body)} != 任务段落数 {len(targets)}")
        for index, (got, expected) in enumerate(zip(body, targets)):
            if got != expected:
                problems.append(
                    f"translation.docx 第 {index + 1} 段与当前译文不一致："
                    f"{got[:40]!r} != {expected[:40]!r}")
        edited = [index for index, pair in enumerate(pairs)
                  if pair.get("human_edited")]
        for index in edited:
            if index < len(body) and body[index] not in paragraphs:
                problems.append(f"第 {index + 1} 段的人工修改没有进入交付文档")
        facts["translation"]["human_edited_segments"] = edited

    # ---- bilingual.docx --------------------------------------------------
    bilingual = _asset(delivery_dir, "bilingual.docx")
    if not bilingual.is_file():
        problems.append(f"缺少 bilingual.docx：{bilingual}")
    else:
        rows = _bilingual_rows(bilingual)
        facts["bilingual"] = {"rows": len(rows), "header": rows[0] if rows else []}
        if not rows:
            problems.append("bilingual.docx 没有表格（双语对照应有 原文/译文 表格）")
        else:
            header = rows[0]
            if header[:2] != ["原文", "译文"]:
                problems.append(f"bilingual.docx 表头不是 原文/译文：{header}")
            body = rows[1:]
            if len(body) != len(pairs):
                problems.append(
                    f"bilingual.docx 对照行数 {len(body)} != 任务段落数 {len(pairs)}")
            for index, (row, source, target) in enumerate(
                    zip(body, sources, targets)):
                if row[0] != source:
                    problems.append(f"bilingual.docx 第 {index + 1} 行原文不匹配")
                if row[1] != target:
                    problems.append(
                        f"bilingual.docx 第 {index + 1} 行译文不是当前译文")

    # ---- terms.xlsx ------------------------------------------------------
    terms = _asset(delivery_dir, "terms.xlsx")
    if not terms.is_file():
        problems.append(f"缺少 terms.xlsx：{terms}")
    else:
        header, rows = _terms(terms)
        glossary = state.get("glossary") or []
        facts["terms"] = {"header": header, "rows": len(rows),
                          "glossary_entries": len(glossary)}
        if header[:2] != ["Source", "Target"]:
            problems.append(f"terms.xlsx 表头不是 Source/Target：{header}")
        if rows and len(rows) != len(glossary):
            problems.append(
                f"terms.xlsx 数据行 {len(rows)} != 术语条数 {len(glossary)}")
        if not rows and glossary:
            problems.append("有术语但 terms.xlsx 只有表头")

    # ---- delivery_manifest.json -----------------------------------------
    manifest_path = _asset(delivery_dir, "delivery_manifest.json")
    if not manifest_path.is_file():
        problems.append(f"缺少 delivery_manifest.json：{manifest_path}")
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        facts["manifest"] = {
            key: manifest.get(key) for key in
            ("delivery_status", "generated_assets", "blocking", "actionable",
             "informational", "job_id", "provider", "model", "reviewer",
             "frozen_glossary")
        }
        assets = manifest.get("generated_assets") or []
        names = {str(item.get("name") if isinstance(item, dict) else item)
                 for item in assets}
        for required in ("translation.docx", "bilingual.docx", "terms.xlsx"):
            if required not in names:
                problems.append(f"manifest 未声明已生成的资产：{required}")
        if manifest.get("delivery_status") != "final":
            problems.append(
                f"manifest 的 delivery_status 不是 final："
                f"{manifest.get('delivery_status')!r}")

    # ---- segment_evidence.jsonl -----------------------------------------
    evidence = _asset(delivery_dir, "segment_evidence.jsonl")
    if not evidence.is_file():
        problems.append(f"缺少 segment_evidence.jsonl：{evidence}")
    else:
        records = [json.loads(line) for line in
                   evidence.read_text(encoding="utf-8").splitlines() if line.strip()]
        facts["evidence_records"] = len(records)
        if len(records) != len(pairs):
            problems.append(
                f"segment_evidence 记录数 {len(records)} != 任务段落数 {len(pairs)}")

    facts["problems"] = problems
    facts["ok"] = not problems
    facts["delivery_dir"] = str(delivery_dir)
    facts["segments"] = len(pairs)
    return facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="交付文件内容校验")
    parser.add_argument("--delivery-dir", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    report = check(Path(args.delivery_dir).expanduser(),
                   Path(args.state).expanduser())
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
