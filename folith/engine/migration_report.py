"""导入校验报告（TMX / TBX / XLSX / CSV 共用一个结构）。

这个模块只做「把一次资产导入说清楚」这一件事，供
``folith.engine.glossary_checks`` 的四个入口共用：

* 语言对检查：文件声明的目标语言 vs 调用方（项目/任务）要求的目标语言；
* 分类计数：总单元 / 已导入 / 冲突 / 不合格 / 行内内容无法处理 / 语言未知 /
  语言不符 / 缺语言对；
* 元数据丢失清单：已导入单元里被丢弃的字段类别与受影响条目数；
* 持久化：JSON + Markdown 两份，写在项目翻译记忆旁边，可归档。

设计约束（与 #150 的验收对应）：

- **口径固定**：``COUNT_KEYS`` / ``LOSS_CATEGORIES`` 的顺序与字段名是稳定的，
  不因为某次导入没有某类问题而消失；报告可以被脚本和归档流程直接消费。
- **不猜语言**：语言对比较只做「写法不同、标签相同」的归一
  （``folith.textual.language_tag``），不做前缀/地区推断；无法解析就是无法
  解析，报告照实写。
- **不虚报**：元数据丢失只统计**真正入库**的单元——被冲突/不合格/语言不符
  拒绝的单元，它的元数据没有「丢失」，只是随单元一起被拒绝。
- **不留半成品**：解析失败的报告由调用方在写库之前产生；本模块只负责渲染与
  落盘，不写翻译记忆。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from folith.engine._link import core as _c
from folith.textual import language_tag

#: 报告结构的机器可读版本；字段增删必须改版本号。
REPORT_SCHEMA = "folith-import-report-v1"

#: 报告目录名。它是项目目录（翻译记忆所在目录）下的子目录，不是新的存储根。
REPORT_DIR_NAME = "import_reports"

KINDS: tuple[str, ...] = ("tmx", "tbx", "xlsx", "csv")

#: 分类计数的固定键。顺序即渲染顺序；总和必须等于 ``total``。
COUNT_KEYS: tuple[str, ...] = (
    "total",
    "imported",
    "skipped_conflict",
    "skipped_ineligible",
    "skipped_unsupported_inline",
    "skipped_language_unknown",
    "skipped_language_mismatch",
    "skipped_missing_pair",
    "skipped_write_failed",
)

COUNT_LABELS: Dict[str, str] = {
    "total": "总单元数 / Total units",
    "imported": "已导入 / Imported",
    "skipped_conflict": "冲突跳过（不覆盖已审校条目） / Skipped (conflict)",
    "skipped_ineligible": "不合格（_tm_eligible 未通过） / Ineligible",
    "skipped_unsupported_inline": "含无法处理的行内内容 / Unsupported inline content",
    "skipped_language_unknown": "语言未知 / Language unknown",
    "skipped_language_mismatch": "语言不符（文件声明 vs 目标） / Language mismatch",
    "skipped_missing_pair": "缺语言对 / Missing language pair",
    "skipped_write_failed": "写入失败（存储层错误） / Write failed",
}

#: 每种格式的元数据丢失类别（固定顺序；没有发生时 affected=0）。
LOSS_CATEGORIES: Dict[str, tuple[tuple[str, str], ...]] = {
    "tmx": (
        ("tmx_tu_attributes", "TU 属性（creationdate/changeid 等）"),
        ("tmx_prop", "TU 的 prop（客户/领域等）"),
        ("tmx_note", "TU 的 note"),
        ("tmx_tuv_attributes", "TUV 属性（xml:lang 以外）"),
        ("tmx_extra_languages", "第三种及以后的语言"),
    ),
    "tbx": (
        ("tbx_term_entry_attributes", "termEntry 属性（id 以外）"),
        ("tbx_term_note", "termNote"),
        ("tbx_extra_languages", "第三种及以后的语言"),
        ("tbx_multiple_terms", "同语言多词条"),
        ("tbx_descrip", "descrip（定义/领域等）"),
    ),
    "xlsx": (
        ("termbase_extra_columns", "术语库表头之外的列"),
    ),
    "csv": (
        ("termbase_extra_columns", "术语库表头之外的列"),
    ),
}

#: 状态取值（稳定枚举）。
STATUS_VALUES: tuple[str, ...] = (
    "imported", "partial", "nothing_imported", "blocked_language_pair",
    "empty", "failed",
)

#: 语言对检查的状态取值（稳定枚举）。
LANGUAGE_PAIR_STATUSES: tuple[str, ...] = (
    "match", "partial_mismatch", "mismatch", "undeclared",
    "unknown_declared", "unknown_expected", "not_checked",
)


class ImportReportError(ValueError):
    """带结构化报告的导入错误（仍可被 ``except ValueError`` 捕获）。"""

    def __init__(self, message: str, report: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.report = report


# ---------------------------------------------------------------- 基础字段

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stream_name(file_stream: Any, fallback: str = "") -> str:
    """上传流/文件对象的名字；取不到就返回 fallback（不编造文件名）。"""
    name = getattr(file_stream, "name", "")
    text = str(name or "").strip()
    if not text:
        text = str(fallback or "").strip()
    return Path(text).name if text else ""


def empty_counts() -> Dict[str, int]:
    return {key: 0 for key in COUNT_KEYS}


def new_report(
    kind: str,
    *,
    file_name: str = "",
    file_bytes: Optional[bytes] = None,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """一份空报告：字段齐全、计数为 0、丢失类别固定。"""
    if kind not in LOSS_CATEGORIES:
        raise ValueError(f"未知的导入格式：{kind}")
    payload = bytes(file_bytes or b"")
    return {
        "schema": REPORT_SCHEMA,
        "kind": kind,
        "file": {
            "name": str(file_name or ""),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest() if payload else "",
        },
        "generated_at": generated_at or _now_iso(),
        "status": "partial",
        "language_pair": empty_language_pair(),
        "counts": empty_counts(),
        "metadata_loss": {
            "total_affected": 0,
            "categories": [
                {"category": category, "label": label,
                 "affected": 0, "fields": []}
                for category, label in LOSS_CATEGORIES[kind]
            ],
        },
        "warnings": [],
        "error": "",
        "recovery": "",
        "persistence": {"directory": "", "json": "", "markdown": ""},
    }


# ---------------------------------------------------------------- 语言对

def language_token(value: Any) -> str:
    """语言标识 -> 可比较的 BCP-47 标签；无法解析时返回空串。

    ``language_tag`` 只把显示名映射成标签（``简体中文`` -> ``zh-CN``），
    它不做语言识别。这里额外把 ``und``（未知）当作空串，避免把"未知"当成
    一个可以和任何东西比较的语言。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    tag = language_tag(text)
    if not tag or tag.casefold() == "und":
        return ""
    return tag


def empty_language_pair() -> Dict[str, Any]:
    return {
        "expected_target": "",
        "expected_tag": "",
        "declared_source": "",
        "declared_targets": [],
        "declared_target_tags": [],
        "status": "not_checked",
        "action": "none",
        "mismatch_units": 0,
        "undeclared_target_units": 0,
        "message": "",
    }


def language_pair_report(
    *,
    expected_target: str = "",
    declared_source: str = "",
    declared_targets: Iterable[str] = (),
    mismatch_units: int = 0,
    undeclared_units: int = 0,
) -> Dict[str, Any]:
    """构造语言对检查结果。

    比较口径（写进 docs，不在这里偷偷放宽）：

    * 两侧都用 ``language_tag`` 归一后再**整串**比较（大小写不计）；
    * 不比较主语言子标签、不把 ``zh`` 当成 ``zh-CN`` —— 保守的错配比静默
      的换算安全；
    * 文件没声明语言 -> ``undeclared``（警告，语言由调用方给出）；
    * 调用方没给目标语言 -> ``not_checked``（完全没有可比较的期望）；
    * 任一侧无法解析 -> ``unknown_expected`` / ``unknown_declared``（警告）。
    """
    expected = str(expected_target or "").strip()
    expected_tag = language_token(expected)
    raw_targets = sorted({str(value).strip() for value in declared_targets
                          if str(value or "").strip()})
    target_tags = sorted({language_token(value) for value in raw_targets
                          if language_token(value)})
    report = empty_language_pair()
    report.update({
        "expected_target": expected,
        "expected_tag": expected_tag,
        "declared_source": str(declared_source or "").strip(),
        "declared_targets": raw_targets,
        "declared_target_tags": target_tags,
        "mismatch_units": int(mismatch_units or 0),
        "undeclared_target_units": int(undeclared_units or 0),
    })

    if not expected:
        report["status"] = "not_checked"
        report["action"] = "none"
        report["message"] = (
            "调用方未提供目标语言：语言对无法比较，导入语言取文件声明的 xml:lang。"
            " / No expected target language: the pair cannot be checked.")
        return report
    if not expected_tag:
        report["status"] = "unknown_expected"
        report["action"] = "warned"
        report["message"] = (
            f"目标语言「{expected}」无法解析为 BCP-47 标签，语言对未校验；"
            f"导入仍按调用方给出的语言写入。 / Target language is not resolvable.")
        return report
    if not raw_targets:
        report["status"] = "undeclared"
        report["action"] = "warned"
        report["message"] = (
            "文件没有声明目标语言（术语库格式不携带语言），语言对未校验；"
            "语言完全由调用方决定。 / The file declares no target language.")
        return report
    if not target_tags:
        report["status"] = "unknown_declared"
        report["action"] = "warned"
        report["message"] = (
            f"文件声明的目标语言无法解析：{'、'.join(raw_targets)}；语言对未校验。"
            " / The declared target language is not resolvable.")
        return report

    expected_key = expected_tag.casefold()
    declared_keys = {tag.casefold() for tag in target_tags}
    if expected_key in declared_keys and declared_keys == {expected_key}:
        report["status"] = "match"
        report["action"] = "none"
        report["message"] = (
            f"语言对一致：文件声明 {'、'.join(target_tags)}，任务目标 {expected_tag}。"
            " / Declared and expected target languages match.")
    elif expected_key in declared_keys:
        report["status"] = "partial_mismatch"
        report["action"] = "warned"
        report["message"] = (
            f"文件同时声明了 {', '.join(target_tags)}，其中与任务目标 {expected_tag} "
            f"不符的 {int(mismatch_units)} 个单元已拒绝导入（不会改标为目标语言）。"
            " / Units whose declared target differs were rejected, never retagged.")
    else:
        report["status"] = "mismatch"
        report["action"] = "blocked"
        report["message"] = (
            f"语言对不一致：文件声明 {', '.join(target_tags)}，任务目标 {expected_tag}；"
            "整份文件不导入（不静默换算成目标语言）。"
            " / Declared target languages do not match; nothing was imported.")
    return report


# ---------------------------------------------------------------- 计数与丢失

def set_counts(report: Dict[str, Any], counts: Mapping[str, int]) -> None:
    """整体写入分类计数，未知键直接报错（防止拼写错误悄悄丢一类）。"""
    unknown = set(counts) - set(COUNT_KEYS)
    if unknown:
        raise ValueError(f"未知的分类计数键：{sorted(unknown)}")
    report["counts"] = {key: int(counts.get(key, 0)) for key in COUNT_KEYS}


def increment(report: Dict[str, Any], key: str, amount: int = 1) -> None:
    if key not in COUNT_KEYS:
        raise ValueError(f"未知的分类计数键：{key}")
    report["counts"][key] = int(report["counts"].get(key, 0)) + int(amount)


def record_loss(
    report: Dict[str, Any],
    category: str,
    *,
    affected: int,
    fields: Iterable[str] = (),
) -> None:
    """记一类元数据丢失：``affected`` 是受影响的已导入单元数。"""
    entry = next((item for item in report["metadata_loss"]["categories"]
                  if item["category"] == category), None)
    if entry is None:
        raise ValueError(f"该格式没有元数据丢失类别：{category}")
    if affected:
        entry["affected"] += int(affected)
    cleaned = {str(field) for field in fields if str(field or "").strip()}
    entry["fields"] = sorted(set(entry["fields"]) | cleaned)


def set_loss_total(report: Dict[str, Any], total: int) -> None:
    """带有任意一类元数据丢失的**已导入**单元数（不是各类影响数之和）。"""
    report["metadata_loss"]["total_affected"] = int(total)


def loss_fields(report: Dict[str, Any], category: str) -> List[str]:
    entry = next((item for item in report["metadata_loss"]["categories"]
                  if item["category"] == category), None)
    return list(entry["fields"]) if entry else []


def mark_write_failed(report: Dict[str, Any], units: int) -> None:
    """存储层写入失败：把本次「已导入」改记为写入失败，并清空丢失清单。

    写入没确认，就不存在「已导入」的条目，也就不存在它们的元数据丢失；
    分类计数总和仍然等于 ``total``。
    """
    counts = report["counts"]
    moved = max(0, int(units))
    counts["skipped_write_failed"] = int(
        counts.get("skipped_write_failed") or 0) + moved
    counts["imported"] = 0
    for category in report["metadata_loss"]["categories"]:
        category["affected"] = 0
        category["fields"] = []
    report["metadata_loss"]["total_affected"] = 0


# ---------------------------------------------------------------- 收尾与渲染

def _recovery_text(report: Mapping[str, Any]) -> str:
    counts = report["counts"]
    imported = int(counts.get("imported") or 0)
    if report["status"] == "failed":
        return ("解析/写入失败：未写入任何条目，现有翻译记忆保持不变。"
                "修正文件后重新导入即可。 / Nothing was written; the existing "
                "memory is unchanged. Fix the file and import again.")
    if imported == 0:
        return ("未写入任何条目，现有翻译记忆保持不变；报告已落盘可归档。"
                " / Nothing was written; the existing memory is unchanged.")
    if int(counts.get("total") or 0) == imported:
        return (f"已写入 {imported} 条；未覆盖任何既有条目。"
                " / All units were imported; no existing entry was overwritten.")
    return (
        f"已写入 {imported} 条；被跳过的单元没有入库，既有条目未被覆盖。"
        "同一文件重新导入不会重复写入（已存在的键计入冲突跳过）。"
        " / Partial import: skipped units were not stored and no existing entry "
        "was overwritten. Re-importing the same file does not duplicate entries.")


def _language_warning(report: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    pair = report["language_pair"]
    status = pair["status"]
    if status in ("match", "not_checked"):
        return None
    code = {
        "partial_mismatch": "language_pair_mismatch",
        "mismatch": "language_pair_mismatch",
        "undeclared": "language_pair_undeclared",
        "unknown_declared": "language_pair_unresolvable",
        "unknown_expected": "language_pair_unresolvable",
    }[status]
    return {"code": code, "message": pair["message"],
            "count": int(pair.get("mismatch_units") or 0)}


_COUNT_WARNINGS = (
    ("skipped_conflict", "conflict_skipped"),
    ("skipped_ineligible", "ineligible_units"),
    ("skipped_unsupported_inline", "unsupported_inline_content"),
    ("skipped_language_unknown", "language_unknown_units"),
    ("skipped_language_mismatch", "units_rejected_for_language_mismatch"),
    ("skipped_missing_pair", "missing_language_pair_units"),
    ("skipped_write_failed", "units_write_failed"),
)

_COUNT_WARNING_MESSAGES = {
    "conflict_skipped": "与现有翻译记忆冲突，未覆盖已审校条目。",
    "ineligible_units": "源文不含字母/数字或译文不可用，未入库。",
    "unsupported_inline_content": "含无法可靠处理的行内内容，整单元不导入。",
    "language_unknown_units": "语言无法确定（缺 xml:lang 且调用方未给目标语言），未入库。",
    "units_rejected_for_language_mismatch": "单元声明的目标语言与任务目标不一致，已拒绝。",
    "missing_language_pair_units": "缺少双语（语言对）内容，未入库。",
    "units_write_failed": "写入翻译记忆失败，条目未确认写入。",
}


def _build_warnings(report: Mapping[str, Any]) -> List[Dict[str, Any]]:
    warnings: List[Dict[str, Any]] = []
    language = _language_warning(report)
    if language:
        warnings.append(language)
    counts = report["counts"]
    for key, code in _COUNT_WARNINGS:
        count = int(counts.get(key) or 0)
        if not count:
            continue
        # A partial language mismatch is already reported by the pair warning.
        if code == "units_rejected_for_language_mismatch" \
                and report["language_pair"]["status"] == "partial_mismatch":
            continue
        warnings.append({"code": code, "message": _COUNT_WARNING_MESSAGES[code],
                         "count": count})
    if int(report["metadata_loss"]["total_affected"] or 0):
        warnings.append({
            "code": "metadata_dropped",
            "message": "部分单元元数据未保留（见元数据丢失清单）。",
            "count": int(report["metadata_loss"]["total_affected"]),
        })
    if report["status"] == "failed":
        warnings.append({"code": "import_failed",
                         "message": report.get("error") or "导入失败。", "count": 0})
    return warnings


def finalize(
    report: Dict[str, Any],
    *,
    status: Optional[str] = None,
    error: str = "",
    recovery: str = "",
) -> Dict[str, Any]:
    """填写状态、恢复说明与警告；``status`` 缺省时按计数推断。"""
    counts = report["counts"]
    if status is None:
        if error:
            status = "failed"
        elif int(counts.get("total") or 0) == 0:
            status = "empty"
        elif int(counts.get("imported") or 0) == 0:
            status = (
                "blocked_language_pair"
                if report["language_pair"]["status"] == "mismatch"
                else "nothing_imported")
        elif int(counts.get("imported") or 0) == int(counts.get("total") or 0):
            status = "imported"
        else:
            status = "partial"
    if status not in STATUS_VALUES:
        raise ValueError(f"非法报告状态：{status}")
    report["status"] = status
    report["error"] = str(error or "")
    report["recovery"] = str(recovery or _recovery_text(report))
    report["warnings"] = _build_warnings(report)
    return report


def _cell(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ")


def _table(rows: Sequence[Sequence[Any]], header: Sequence[str]) -> List[str]:
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
    return lines


def render_markdown(report: Mapping[str, Any]) -> str:
    """报告的人类可读版本（结构稳定，可归档为 Gate 1 证据）。"""
    counts = report["counts"]
    pair = report["language_pair"]
    lines: List[str] = [
        "# 导入校验报告 / Import verification report",
        "",
        f"- schema `{report['schema']}`",
        f"- 格式 / Format: `{report['kind']}`",
        f"- 文件 / File: `{report['file']['name'] or '(unknown)'}`"
        f"（{report['file']['bytes']} bytes，sha256 "
        f"`{report['file']['sha256'] or '(empty)'}`）",
        f"- 生成时间 / Generated: `{report['generated_at']}`",
        f"- 状态 / Status: `{report['status']}`",
        "",
        "## 语言对 / Language pair",
        "",
    ]
    lines += _table(
        [
            ("expected_target", pair["expected_target"] or "(未提供 / not set)"),
            ("expected_tag", pair["expected_tag"] or "(unresolved)"),
            ("declared_source", pair["declared_source"] or "(未声明 / undeclared)"),
            ("declared_targets", ", ".join(pair["declared_targets"]) or
             "(未声明 / undeclared)"),
            ("declared_target_tags", ", ".join(pair["declared_target_tags"]) or
             "(unresolved)"),
            ("status", pair["status"]),
            ("action", pair["action"]),
            ("mismatch_units", pair["mismatch_units"]),
            ("undeclared_target_units", pair["undeclared_target_units"]),
        ],
        ("field", "value"),
    )
    lines += ["", pair["message"], "", "## 分类计数 / Classified counts", ""]
    lines += _table(
        [(key, COUNT_LABELS[key], counts[key]) for key in COUNT_KEYS],
        ("count", "meaning", "value"),
    )
    lines += ["", "## 元数据丢失 / Metadata loss", ""]
    loss_rows = []
    for category in report["metadata_loss"]["categories"]:
        loss_rows.append((
            category["category"],
            category["label"],
            category["affected"],
            ", ".join(category["fields"]) or "(none)",
        ))
    lines += _table(loss_rows, ("category", "meaning", "affected", "fields"))
    lines += ["", f"受影响条目总数 / Total affected entries: "
                  f"{report['metadata_loss']['total_affected']}", ""]
    lines += ["## 警告 / Warnings", ""]
    if report["warnings"]:
        for warning in report["warnings"]:
            lines.append(f"- `{warning['code']}` ({warning['count']}): "
                         f"{warning['message']}")
    else:
        lines.append("- (none)")
    lines += ["", "## 失败与恢复 / Failure & recovery", ""]
    if report["error"]:
        lines.append(f"- error: {report['error']}")
    else:
        lines.append("- error: (none)")
    lines.append(f"- recovery: {report['recovery']}")
    lines += ["", "## 持久化 / Persistence", ""]
    persistence = report["persistence"]
    lines.append(f"- directory: `{persistence['directory'] or '(not written)'}`")
    lines.append(f"- json: `{persistence['json'] or '(not written)'}`")
    lines.append(f"- markdown: `{persistence['markdown'] or '(not written)'}`")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 落盘

def report_directory(project_id: Optional[str] = None) -> Path:
    """报告目录 = 该项目翻译记忆所在目录下的 ``import_reports/``。

    翻译记忆的位置由 ``core.tm_path`` 决定（系统工作区是
    ``outputs/translation_memory.json``，命名项目是
    ``outputs/projects/<project_id>/translation_memory.json``），因此报告与它
    描述的数据永远在同一处，不需要新的存储根。
    """
    return Path(_c.tm_path(project_id)).parent / REPORT_DIR_NAME


_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_stem(report: Mapping[str, Any]) -> str:
    name = str(report["file"]["name"] or "").strip()
    stem = _SLUG_RE.sub("-", Path(name).stem) if name else ""
    stem = stem.strip(".-_")
    return stem or "import"


def _resolve_paths(directory: Path, report: Mapping[str, Any]) -> tuple[Path, Path]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{stamp}-{report['kind']}-{_safe_stem(report)}"
    candidate = base
    counter = 2
    while (directory / f"{candidate}.json").exists() \
            or (directory / f"{candidate}.md").exists():
        candidate = f"{base}-{counter}"
        counter += 1
    return directory / f"{candidate}.json", directory / f"{candidate}.md"


def write_report(
    report: Dict[str, Any],
    *,
    directory: Optional[Any] = None,
    project_id: Optional[str] = None,
) -> Dict[str, str]:
    """写 JSON + Markdown 两份报告，返回（并写回）持久化位置。"""
    target = Path(directory) if directory is not None else report_directory(project_id)
    target.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = _resolve_paths(target, report)
    report["persistence"] = {
        "directory": str(target),
        "json": str(json_path),
        "markdown": str(markdown_path),
    }
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return dict(report["persistence"])


def attach_failure(
    report: Dict[str, Any],
    error: str,
    *,
    directory: Optional[Any] = None,
    project_id: Optional[str] = None,
    write: bool = True,
) -> ImportReportError:
    """收尾为失败报告（可选落盘），返回可抛出的 ValueError 子类。"""
    finalize(report, error=str(error))
    if write:
        try:
            write_report(report, directory=directory, project_id=project_id)
        except OSError:
            # 报告落盘失败不能盖住真正的导入错误。
            pass
    return ImportReportError(str(error), report)


__all__ = [
    "REPORT_SCHEMA", "REPORT_DIR_NAME", "KINDS", "COUNT_KEYS", "COUNT_LABELS",
    "LOSS_CATEGORIES", "STATUS_VALUES", "LANGUAGE_PAIR_STATUSES",
    "ImportReportError", "stream_name", "empty_counts", "new_report",
    "language_token", "empty_language_pair", "language_pair_report",
    "set_counts", "increment", "record_loss", "set_loss_total", "loss_fields",
    "mark_write_failed",
    "finalize", "render_markdown", "report_directory", "write_report",
    "attach_failure",
]
