"""设置里「存储」与「关于」两页背后的事实与动作（acceptance-feedback 8.3-3、12-H、D6）。

* 存储：数据目录在哪、各类数据占多少；清理**派生**缓存（只会多花 API 调用，不碰
  任务真值）；把全部数据打成一个备份包（**不含** API 密钥）。
* 关于：版本、构建、GitHub 链接、「报告问题」的预填地址、手动「检查更新」。

只读事实与文件操作都在这里；界面在 :mod:`folith.qt.settings_pages`。
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
from packaging.version import InvalidVersion, Version
import zipfile
from pathlib import Path

from folith.desktop import paths as desktop_paths

from . import adapter, platform_ui

PROJECT_URL = "https://github.com/xueyang-dev/Folith"
AUTHOR_URL = "https://github.com/xueyang-dev"
X_URL = "https://x.com/xueyang2216"
ISSUES_URL = "https://github.com/xueyang-dev/Folith/issues/new"
RELEASES_API = "https://api.github.com/repos/xueyang-dev/Folith/releases/latest"
#: 本机 OCR 是可选附加，不随应用打包；未安装时设置里给出这份官方安装说明。
TESSERACT_INSTALL_URL = "https://tesseract-ocr.github.io/tessdoc/Installation.html"

#: 派生缓存：删掉只会让下次多花调用，不改变任何译文、术语、记忆或交付。
CACHE_ENTRIES = ("ocr_profile_cache", "translation_cache.json", "knowledge_cache.json")
#: 备份里不放的东西（CX-06 第 1、3 项）：
#: * 密钥本体：`provider_config.json`（翻译/审校/视觉连接）、`ocr_online_config.json`
#:   （专用视觉连接，明文 key）。恢复后按 README 重新填写。
#: * 可能回显密钥的原始取证：`failures.jsonl` 与 `*-failure.json`（保存服务商
#:   原始回包）、`runtime_technical.log`（保留完整异常字符串）、`.performance/`
#:   采样目录（summary.json 实测回显过密钥）。
#: 锁文件与写到一半的临时文件照旧排除。
EXPORT_EXCLUDED_NAMES = frozenset({
    "provider_config.json",
    "ocr_online_config.json",
    "failures.jsonl",
    "runtime_technical.log",
})
EXPORT_EXCLUDED_PARTS = frozenset({".performance"})
EXPORT_EXCLUDED_PATTERNS = ("*-failure.json",)
#: CX-11 §3：操作者常把凭据文件留成 ``.bak``/``.old`` 等副本。匹配锚定在两个
#: 正式凭据文件名上（不匹配其它文件的任何副本后缀）。
EXPORT_EXCLUDED_CREDENTIAL_PATTERNS = (
    "provider_config.json.*", "ocr_online_config.json.*",
)
EXPORT_EXCLUDED_SUFFIXES = (".lock", ".tmp")
_JOB_DIR = re.compile(r"^[0-9a-f]{16}$")


def data_dir() -> Path:
    return Path(adapter.core.OUTPUT_DIR)


def _size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def storage_view() -> dict:
    """各类数据的占用（遍历目录，可能要几百毫秒：在 worker 里调用）。"""
    root = data_dir()
    jobs = caches = projects = other = 0
    job_count = 0
    if root.is_dir():
        for entry in root.iterdir():
            size = _size(entry)
            if entry.is_dir() and _JOB_DIR.match(entry.name):
                jobs += size
                job_count += 1
            elif entry.name in CACHE_ENTRIES:
                caches += size
            elif entry.name == "projects" or entry.name.startswith("translation_memory"):
                projects += size
            elif entry.name.endswith(EXPORT_EXCLUDED_SUFFIXES):
                continue
            else:
                other += size
    log_dir = desktop_paths.resolve_paths().log_dir
    logs = _size(log_dir) if log_dir.is_dir() else 0
    return {"data_dir": str(root), "log_dir": str(log_dir),
            "jobs": jobs, "job_count": job_count, "projects": projects,
            "caches": caches, "other": other, "logs": logs,
            "total": jobs + projects + caches + other + logs}


def human_size(value: int) -> str:
    size = float(max(0, int(value or 0)))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def running_jobs() -> list[str]:
    root = data_dir()
    if not root.is_dir():
        return []
    return [entry.name for entry in root.iterdir()
            if entry.is_dir() and _JOB_DIR.match(entry.name)
            and adapter.job_is_alive(entry.name)]


def clear_caches() -> int:
    """删除派生缓存，返回释放的字节数。有任务在运行时拒绝（它可能正在写缓存）。"""
    if running_jobs():
        raise adapter.AdapterError("有任务正在运行；等它结束或暂停后再清理缓存。")
    root = data_dir()
    freed = 0
    # A-65/A-68: project caches live under root/<PROJECTS_DIR>/<id>, never as
    # direct children of the data root.  Use the product's own path helpers;
    # only the fixed CACHE_ENTRIES names are touched, project TM and task
    # bodies are deliberately retained.
    from folith import project
    projects_root = project.projects_root(root)
    scopes = [root]
    if projects_root.is_dir():
        scopes += [project.project_dir(root, item.name)
                   for item in projects_root.iterdir() if item.is_dir()]
    for scope in scopes:
        for name in CACHE_ENTRIES:
            path = scope / name
            if not path.exists():
                continue
            freed += _size(path)
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    return freed


def _exported(name: str) -> bool:
    """One path component's verdict: credentials and raw forensics stay out."""
    if name in EXPORT_EXCLUDED_NAMES or name in EXPORT_EXCLUDED_PARTS:
        return False
    if name.endswith(EXPORT_EXCLUDED_SUFFIXES):
        return False
    patterns = EXPORT_EXCLUDED_PATTERNS + EXPORT_EXCLUDED_CREDENTIAL_PATTERNS
    return not any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


#: CX-11 §2：升级前的构建把提供商原始异常（可能回显密钥、正文和路径）写进了
#: 任务错误字段。导出时无法事后识别旧回包里带过什么，所以这些**指定字段**在打包
#: 时一律替换为占位符；批号、偏移、状态、事件标识等恢复任务所需的全部保留。
EXPORT_CLEANED_ERROR_TEXT = "（升级前记录的错误文本，导出时已脱敏）"
#: 会被清理错误字段的成员（其余文件按原字节打包）。
_EXPORT_CLEANED_BASENAMES = frozenset({
    "state.json", "runtime_state.json", "events.jsonl", "runtime_events.jsonl",
    "document_synopsis.json",
})


#: CX-12 §2：旧事件日志是追加式，末行可能写了一半（JSON 不完整）。解析失败
#: 的行**不照抄原文**（它可能带着升级前写进的回显），换成可读占位记录；README
#: 对此有一句说明，原始文件留在本机不删。
EXPORT_OMITTED_LINE_EVENT = "legacy_unparsed_line_omitted"
EXPORT_OMITTED_LINE_TEXT = "无法解析的旧记录已省略（可能为写到一半的末行）"

#: CX-12 应修：events.jsonl 里只有失败类事件可能带着模型回包原文，快照与进度
#: 类事件的 reason 是正常诊断文案，导出时照带。
_EXPORT_FAILURE_PHASE_MARK = "failed"

#: 画像失败和知识反馈失败的备份只保留本程序自己写过的整条。子串不够：旧回显
#: 里夹着「API 请求失败」或同时出现 ``class=`` / ``error=``，照样可能带着凭据。
_HTTP_SUFFIX = r"(?:（HTTP \d+）)?"
_AUXILIARY_GENERATED_INNERS = (
    re.compile(r"^模型未返回内容$"),
    re.compile(r"^返回内容不是合法 JSON 对象$"),
    re.compile(
        r"^画像缺少关键字段："
        r"(?:文档画像缺失|文档画像缺少 domain（领域）|文档画像置信度过低，结论仅供参考|"
        r"章节（序号已省略） 缺少 topic/domain)"
        r"(?:；(?:文档画像缺失|文档画像缺少 domain（领域）|文档画像置信度过低，结论仅供参考|"
        r"章节（序号已省略） 缺少 topic/domain))*$"
    ),
    re.compile(r"^余额不足，请充值或更换 API Key。$"),
    re.compile(rf"^API Key 无效或已过期{_HTTP_SUFFIX}，请检查密钥和服务商。$"),
    re.compile(r"^该连接的(?:本月)?用量额度已用尽，等一会儿不会恢复；请更换连接或提升额度。$"),
    re.compile(rf"^请求过于频繁或已达到服务商限额{_HTTP_SUFFIX}，请稍后重试。$"),
    re.compile(r"^服务商拒绝了请求（HTTP 403），请检查 API 权限、余额或接口地址。$"),
    re.compile(r"^无法连接服务商，请检查 API 地址和网络。$"),
    re.compile(rf"^服务商暂时不可用{_HTTP_SUFFIX}，请稍后重试。$"),
    re.compile(rf"^API 请求失败{_HTTP_SUFFIX}，请检查服务商配置后重试。$"),
    re.compile(r"^诊断未能生成（[A-Za-z_][A-Za-z0-9_]*）$"),
    re.compile(
        r"^class=[a-z_]+"
        r"(?:; http=\d+)?"
        r"(?:; code=[A-Za-z0-9_.\-]+)?"
        r"(?:; request_id=[A-Za-z0-9_.\-]+)?"
        r"(?:; period=[A-Za-z0-9_.\-]+)?"
        r"; error=[A-Za-z_][A-Za-z0-9_]*"
        r"(?:; stage=[A-Za-z0-9_]+)?$"
    ),
)
#: 历史警告把模型的 section_id 拼进了「章节 … 缺少 topic/domain」。导出时
#: 不论外壳是失败原因还是普通提示，这段 ID 都换成固定占位，警告本身保留。
_CHAPTER_TOPIC_PLACEHOLDER = "章节（序号已省略） 缺少 topic/domain"
_CHAPTER_TOPIC_GAP = re.compile(r"章节 .+? 缺少 topic/domain", re.DOTALL)
_PROFILE_FAILURE_LINE = re.compile(
    r"^文档画像失败（(.*)），已跳过；可在 UI 中人工填写。$", re.DOTALL)
_KNOWLEDGE_FAILURE_LINE = re.compile(r"^知识反馈调用失败：(.*)$", re.DOTALL)


def _generated_auxiliary_inner(text: str) -> bool:
    return any(pattern.fullmatch(text) for pattern in _AUXILIARY_GENERATED_INNERS)


def _legacy_auxiliary_echo(text) -> bool:
    """True when a persisted auxiliary failure is not one of our whole lines.

    Only a fixed prefix plus the complete shape this program writes is kept.
    A reply that merely contains a stable phrase, or both ``class=`` and
    ``error=``, is still a legacy echo.
    """
    if not isinstance(text, str) or not text:
        return False
    if text == "文档画像失败：无文本样本":
        return False
    profile = _PROFILE_FAILURE_LINE.fullmatch(text)
    if profile:
        return not _generated_auxiliary_inner(profile.group(1))
    knowledge = _KNOWLEDGE_FAILURE_LINE.fullmatch(text)
    if knowledge:
        return not _generated_auxiliary_inner(knowledge.group(1))
    return False


def _omit_model_chapter_ids(text: str) -> str:
    """Replace a model-supplied chapter id inside a topic/domain warning."""
    return _CHAPTER_TOPIC_GAP.sub(_CHAPTER_TOPIC_PLACEHOLDER, text)


def _redact_diagnostic_fields(data: dict) -> None:
    """CX-21: fixed diagnostic text for known error/warning/diagnostic fields.

    Batch-annotation errors, full-document understanding warnings, and the
    synopsis warning can echo a provider reply (credentials, document text, a
    path).  They are replaced with the fixed marker, like every other legacy
    error field — this also covers rotated credentials that no current value can
    match.  Structure and non-error fields are untouched.
    """
    batches = data.get("annotation_batches")
    if isinstance(batches, dict):
        for entry in batches.values():
            if isinstance(entry, dict) and entry.get("error"):
                entry["error"] = EXPORT_CLEANED_ERROR_TEXT
    elif isinstance(batches, list):
        for entry in batches:
            if isinstance(entry, dict) and entry.get("error"):
                entry["error"] = EXPORT_CLEANED_ERROR_TEXT
    # CX-22: the same understanding error is also written into the general
    # ``warnings`` list (core writes both). Capture the original values *before*
    # replacing the dedicated fields, then swap the matching entries in
    # ``warnings`` too — otherwise the general copy carries the remote echo
    # (and any rotated credential) into the ZIP.
    echoed_warnings = set()
    uw = data.get("understanding_warnings")
    if isinstance(uw, list):
        for item in uw:
            if isinstance(item, str) and item:
                echoed_warnings.add(item)
    synopsis = data.get("document_synopsis")
    if isinstance(synopsis, dict) and synopsis.get("warning"):
        echoed_warnings.add(synopsis["warning"])
    if isinstance(uw, list):
        for index, item in enumerate(uw):
            if isinstance(item, str) and item:
                uw[index] = EXPORT_CLEANED_ERROR_TEXT
    if isinstance(synopsis, dict) and synopsis.get("warning"):
        synopsis["warning"] = EXPORT_CLEANED_ERROR_TEXT
    if echoed_warnings:
        general = data.get("warnings")
        if isinstance(general, list):
            for index, item in enumerate(general):
                if isinstance(item, str) and item in echoed_warnings:
                    general[index] = EXPORT_CLEANED_ERROR_TEXT


def _redact_auxiliary_failure_copies(data: dict) -> None:
    """Replace legacy profile/knowledge failure text in the export copy.

    Profile warnings are stored twice: ``profile_warnings`` and the general
    ``warnings`` list.  Both copies are cleaned.  Knowledge observation events
    and the failure counters stay untouched.
    """
    for key in ("profile_warnings", "warnings"):
        items = data.get(key)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if isinstance(item, str):
                item = _omit_model_chapter_ids(item)
                items[index] = item
            if _legacy_auxiliary_echo(item):
                items[index] = EXPORT_CLEANED_ERROR_TEXT
    events = data.get("knowledge_events")
    if not isinstance(events, list):
        return
    for event in events:
        if (isinstance(event, dict) and event.get("type") == "extract_failed"
                and _legacy_auxiliary_echo(event.get("reason"))):
            event["reason"] = EXPORT_CLEANED_ERROR_TEXT


_EXPORT_TEXT_SUFFIXES = (".json", ".jsonl", ".md", ".txt", ".csv", ".tsv", ".yaml", ".yml")

#: CX-21：只对长度 ≥ 这个值的凭据做全包按值替换。更短的值（如 "false"、
#: "k"）在全量备份里全包替换会误伤布尔/短词，甚至把 JSON 变成非法——因此
#: 这里**不做**短值替换。这个限制是有意的：短凭据只在按字段的错误/日志诊断
#: 清理里处理。
_EXPORT_MIN_SCRUB_LEN = 8


def _known_export_secrets() -> list[str]:
    """CX-21: real credential values from the current configuration + environment.

    Sourced from provider api_key, reviewer.api_key, connections[].api_key, the
    OCR api_key, and the runtime environment fallback each connection provider
    exposes (`model_connections.environment_key`).  Config files themselves never
    enter the archive.  Short values are kept here but the export scrubber only
    applies the long ones (>= _EXPORT_MIN_SCRUB_LEN); see _scrub_export_credentials.
    """
    secrets: list[str] = []
    import core as _core  # 局部导入：avoid import cycle at module load
    from folith import model_connections as _mc

    def _add(value):
        text = str(value or "").strip()
        if text:
            secrets.append(text)

    def _add_env(provider):
        try:
            _add(_mc.environment_key(str(provider or "")))
        except Exception:  # noqa: BLE001 - env lookup must never break export
            pass

    try:
        cfg = _core.load_provider_config() or {}
    except Exception:  # noqa: BLE001 - a broken config must not break export
        cfg = {}
    _add(cfg.get("api_key"))
    _add_env(cfg.get("provider"))
    reviewer = cfg.get("reviewer")
    if isinstance(reviewer, dict):
        _add(reviewer.get("api_key"))
        _add_env(reviewer.get("provider"))
    for conn in cfg.get("connections") or ():
        if isinstance(conn, dict):
            _add(conn.get("api_key"))
            _add_env(conn.get("provider"))
    try:
        ocr = _core.load_ocr_online_config() or {}
    except Exception:  # noqa: BLE001
        ocr = {}
    _add(ocr.get("api_key"))
    _add_env(ocr.get("provider"))
    # Deduplicate, longest-first so a long key is replaced whole before a
    # shorter one that is a substring of it.
    seen, ordered = set(), []
    for value in sorted(secrets, key=len, reverse=True):
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _scrub_strings(value, secrets, changed):
    """Recursively replace long-credential substrings only inside string values.

    Returns the (possibly new) value.  Numbers, booleans, keys and structure are
    left untouched, so `"p2_done": false` and numeric fields stay valid JSON.
    ``changed`` is a one-element list used as a flag.
    """
    from folith.engine.redaction import PLACEHOLDER
    if isinstance(value, str):
        out = value
        for secret in secrets:
            if len(secret) >= _EXPORT_MIN_SCRUB_LEN and secret in out:
                out = out.replace(secret, PLACEHOLDER)
        if out != value:
            changed[0] = True
        return out
    if isinstance(value, list):
        return [_scrub_strings(item, secrets, changed) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_strings(val, secrets, changed) for key, val in value.items()}
    return value


def _scrub_export_credentials(name: str, payload: bytes,
                              secrets: list[str]) -> bytes:
    """CX-21: precise by-value scrubbing of long credentials, structure-preserving.

    Only string values are touched; never the raw serialized bytes, so a
    credential value inside a profile id, `section:<value>`, a semantic unit's
    `source_range_id`, an annotation error or a synopsis warning all change
    together and references stay consistent, while `token: punctuation` prose,
    translation-memory entry keys and booleans are untouched.  Short (<
    _EXPORT_MIN_SCRUB_LEN) credentials are intentionally not scrubbed here — a
    whole-archive replace of a value like `false` would corrupt real data; those
    are handled only by the field-anchored diagnostic cleaning.  JSON/JSONL that
    parse are edited structurally; nothing changed -> original bytes; a member
    that does not parse is carried through as-is (never guessed).
    """
    long_secrets = [s for s in secrets if len(s) >= _EXPORT_MIN_SCRUB_LEN]
    if not long_secrets:
        return payload
    if not name.lower().endswith(_EXPORT_TEXT_SUFFIXES):
        return payload
    lower = name.lower()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload
    if lower.endswith(".jsonl"):
        # CX-22: split on real LF only. str.splitlines() also breaks on
        # U+2028/U+2029, which a JSON string may legally contain, tearing a
        # valid record in two and breaking journal replay.
        lines = [line[:-1] if line.endswith("\r") else line
                 for line in text.split("\n")]
        trailing_newline = text.endswith("\n")
        if trailing_newline:
            lines = lines[:-1]  # drop the empty element after the final LF
        changed = [False]
        out_lines = []
        for line in lines:
            if not line.strip():
                out_lines.append(line)
                continue
            try:
                record = json.loads(line)
            except ValueError:
                out_lines.append(line)  # a torn tail: never guess
                continue
            out_lines.append(json.dumps(
                _scrub_strings(record, long_secrets, changed), ensure_ascii=False))
        result = "\n".join(out_lines) + ("\n" if trailing_newline else "")
        return result.encode("utf-8") if changed[0] else payload
    if lower.endswith(".json"):
        try:
            data = json.loads(text)
        except ValueError:
            return payload
        changed = [False]
        scrubbed = _scrub_strings(data, long_secrets, changed)
        if not changed[0]:
            return payload
        return (json.dumps(scrubbed, ensure_ascii=False) + ("\n" if text.endswith("\n") else "")).encode("utf-8")
    # Plain text (.md/.txt/.csv/...): only the long-value substring replace.
    from folith.engine.redaction import PLACEHOLDER
    out = text
    for secret in long_secrets:
        out = out.replace(secret, PLACEHOLDER)
    return out.encode("utf-8") if out != text else payload


def _clean_export_member(name: str, payload: bytes) -> bytes:
    """The export copy of a task file carries no legacy error free text.

    Cleaning is field-anchored, never a content guess: the job state's
    ``translation_failures[].reason``, legacy document-profile failure text
    in both ``profile_warnings`` and ``warnings``, ``knowledge_events``
    whose type is ``extract_failed``, the segment event log's failure-event
    ``reason``, the runtime state's ``error.message`` / ``last_event``
    (failed runs) and every ``category == "error"`` runtime event message
    become the marker.  Classified quota/rate-limit copy, ordinary profile
    hints and knowledge observation events ride untouched, as do normal
    snapshot/progress reasons (CX-12).  An events line that cannot be parsed
    (a torn append tail) is never copied verbatim — it becomes an explicit
    omission record, because
    it may carry a legacy echo nobody can sanitise after the fact.  A state
    member that cannot be parsed is carried byte-for-byte (atomic writes
    mean a torn state.json cannot exist on disk).
    """
    if name not in _EXPORT_CLEANED_BASENAMES:
        return payload
    if name.endswith(".jsonl"):
        # CX-22: split on real LF only (U+2028/U+2029 are legal inside a string).
        raw = payload.decode("utf-8", "replace")
        split = raw.split("\n")
        if raw.endswith("\n"):
            split = split[:-1]
        lines = []
        for line in split:
            if line.endswith("\r"):
                line = line[:-1]
            if line.strip():
                try:
                    record = json.loads(line)
                except ValueError:
                    lines.append(json.dumps({
                        "event": EXPORT_OMITTED_LINE_EVENT,
                        "message": EXPORT_OMITTED_LINE_TEXT,
                    }, ensure_ascii=False))
                    continue
                if (name == "events.jsonl" and record.get("reason")
                        and _EXPORT_FAILURE_PHASE_MARK
                        in str(record.get("phase") or "").lower()):
                    record["reason"] = EXPORT_CLEANED_ERROR_TEXT
                if (name == "runtime_events.jsonl"
                        and record.get("category") == "error"
                        and record.get("message")):
                    record["message"] = EXPORT_CLEANED_ERROR_TEXT
                line = json.dumps(record, ensure_ascii=False)
            lines.append(line)
        return ("\n".join(lines) + "\n").encode("utf-8")
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return payload
    if name == "state.json":
        for failure in data.get("translation_failures") or ():
            if isinstance(failure, dict) and failure.get("reason"):
                failure["reason"] = EXPORT_CLEANED_ERROR_TEXT
        if isinstance(data, dict):
            _redact_auxiliary_failure_copies(data)
            _redact_diagnostic_fields(data)
    elif name == "document_synopsis.json" and isinstance(data, dict):
        if data.get("warning"):
            data["warning"] = EXPORT_CLEANED_ERROR_TEXT
    elif name == "runtime_state.json" and isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            error["message"] = EXPORT_CLEANED_ERROR_TEXT
            data["last_event"] = EXPORT_CLEANED_ERROR_TEXT
        for event in data.get("events") or ():
            if (isinstance(event, dict) and event.get("category") == "error"
                    and event.get("message")):
                event["message"] = EXPORT_CLEANED_ERROR_TEXT
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def export_all_data(destination: str | os.PathLike[str]) -> dict:
    """把数据目录打成 zip（不含 API 密钥与锁文件）。返回 ``{"path", "files", "bytes"}``。"""
    root = data_dir()
    target = Path(destination)
    if target.suffix.lower() != ".zip":
        target = target.with_suffix(".zip")
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        pass
    else:
        raise adapter.AdapterError("备份包不能放在数据目录里面。")
    files = 0
    secrets = _known_export_secrets()
    partial = target.with_name(target.name + ".partial")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or not all(map(_exported, path.relative_to(root).parts)):
                continue
            member = Path("Folith-data") / path.relative_to(root)
            # CX-11 §2：任务错误字段在导出副本里替换为占位符（原文件不动）。
            # CX-20：再对所有文本成员按凭据值/形状统一擦除，兜住画像、术语
            # scope、语义单元、警告、标注错误、概要等所有字段。
            cleaned = _clean_export_member(path.name, path.read_bytes())
            cleaned = _scrub_export_credentials(path.name, cleaned, secrets)
            archive.writestr(member.as_posix(), cleaned)
            files += 1
        archive.writestr("Folith-data/README.txt", (
            "Folith 数据备份（私有备份：包含文档、译文、术语与记忆等数据）。\n"
            "不含 API 密钥：模型与视觉连接的配置文件、以及可能记录了服务商原始回包的\n"
            "取证文件都不会打包；在新机器上恢复后，需要在设置里重新填写模型连接与视觉连接。\n"
            "旧任务里升级前写下的错误文本与无法解析的末行记录，在包内以脱敏占位替代。\n"
            "恢复：退出 Folith，把 Folith-data 里的内容放回新机器的数据目录\n"
            "（设置的「存储」页会显示当前数据目录的位置）。\n"))
    partial.replace(target)
    return {"path": str(target), "files": files, "bytes": target.stat().st_size}


# ----- 关于 ---------------------------------------------------------------------
def about_view() -> dict:
    identity = adapter.app_identity()
    return {"version": identity.get("app_version") or "",
            "build": identity.get("build_id") or "",
            "frozen": bool(identity.get("frozen")),
            "system_name": platform_ui.system_name(),
            "system_version": platform_ui.system_version()}


def issue_url(view: dict | None = None) -> str:
    """「报告问题」：只预填版本、构建与系统版本——不带任何文档内容、路径或密钥。"""
    view = view or about_view()
    body = ("**发生了什么 / What happened**\n\n\n"
            "**怎样复现 / Steps to reproduce**\n\n\n"
            "---\n"
            f"Folith {view['version']} ({view['build']}) · {view['system_name']} {view['system_version']}\n")
    return ISSUES_URL + "?" + urllib.parse.urlencode({"body": body})


def check_for_update(current: str, *, timeout: float = 8.0, opener=None) -> dict:
    """只在用户点「检查更新」时调用：读 GitHub Releases 的最新版本，不下载任何东西。

    返回 ``{"state": "newer"|"current"|"failed", "latest", "url", "message"}``。
    """
    request = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"Folith/{current or 'dev'}"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - offline, rate-limited, no releases yet
        return {"state": "failed", "latest": "", "url": PROJECT_URL + "/releases",
                "message": f"无法检查更新：{type(exc).__name__}"}
    latest = str(payload.get("tag_name") or payload.get("name") or "").strip()
    url = str(payload.get("html_url") or PROJECT_URL + "/releases")
    if not latest:
        return {"state": "failed", "latest": "", "url": url, "message": "还没有发布的版本。"}
    try:
        newer = Version(latest) > Version(current)
    except InvalidVersion:
        return {"state": "failed", "latest": latest, "url": url, "message": "无法识别版本号。"}
    if newer:
        return {"state": "newer", "latest": latest.lstrip("v"), "url": url,
                "notes": str(payload.get("body") or ""),
                "message": f"有新版本 {latest.lstrip('v')}。"}
    return {"state": "current", "latest": latest.lstrip("v"), "url": url,
            "message": "已经是最新版本。"}
