"""项目页读任务用的「摘要」：每个任务只提取页面要用的字段，并按状态文件的签名缓存。

为什么需要：``core.list_jobs()`` 每次调用都把每个任务的完整状态读出来并复制一份（几 MB 的
版面 IR 也在内），项目页一次切换要读好几遍，任务一多就成了秒级，还会占着 GIL 让界面卡住。
摘要只在任务的 ``state.json`` / 日志变了的时候重读，其余时候零读取：

- ``state``：精简状态（文件名、项目、语言方向、术语表、候选），足够 ``build_term_rows`` /
  ``resolved_project_id`` / 轻量候选计数使用；
- ``candidate_rows``：完整的待审核候选行（带原文上下文，需要整份状态才能算），
  同样只在任务变化时重算。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

SLIM_KEYS = ("filename", "project_id", "project_name", "source_lang", "target_lang",
             "glossary", "glossary_frozen", "auto_terms", "knowledge_candidates")

_CACHE: dict[str, tuple[tuple, dict]] = {}
_LOCK = threading.Lock()
#: 测试与诊断：loads = 真正读了几次任务状态，hits = 命中缓存几次。
STATS = {"loads": 0, "hits": 0}


def _signature(job_id: str, output_dir: Path) -> tuple | None:
    from folith.storage import state_journal
    state_path = output_dir / job_id / "state.json"
    try:
        stat = state_path.stat()
    except OSError:
        return None
    try:
        jstat = state_journal.journal_path(job_id, output_dir=output_dir).stat()
        journal = (jstat.st_mtime_ns, jstat.st_size)
    except OSError:
        journal = (0, 0)
    return (str(state_path), stat.st_mtime_ns, stat.st_size, *journal)


def _build(job_id: str, state: dict[str, Any]) -> dict:
    from folith import language_assets as assets
    slim = {key: state.get(key) for key in SLIM_KEYS if key in state}
    config = state.get("pipeline_config")
    if isinstance(config, dict):
        slim["pipeline_config"] = {"target_lang": config.get("target_lang")}
    job = {"job_id": job_id, "state": state}
    return {"job_id": job_id, "state": slim,
            "candidate_rows": assets.build_candidate_rows([job])}


def read_digests(core) -> list[dict]:
    """全部任务的摘要（按目录名排序）。只重读签名变了的任务。"""
    output_dir = Path(core.OUTPUT_DIR)
    if not output_dir.is_dir():
        return []
    digests, seen = [], set()
    for entry in sorted(output_dir.iterdir()):
        if not entry.is_dir():
            continue
        job_id = entry.name
        signature = _signature(job_id, output_dir)
        if signature is None:
            continue
        seen.add(job_id)
        with _LOCK:
            cached = _CACHE.get(job_id)
        if cached and cached[0] == signature:
            STATS["hits"] += 1
            digests.append(cached[1])
            continue
        # A3: read-only consumer -- a shallow projection avoids the pickle
        # round-trip of a book-sized state (and the gen2 GC it triggers).
        from folith.storage import job_repository as _repo
        # A-34: build_candidate_rows reads more than SLIM_KEYS -- candidate
        # context needs `pairs`, section labels need `semantic_units`.  Keep the
        # view complete so digest rows stay byte-identical to the full-state path.
        state = _repo.load_job_state_view(
            job_id, (*SLIM_KEYS, "pipeline_config", "pairs", "semantic_units"),
            output_dir=output_dir)
        if state is None:
            continue
        STATS["loads"] += 1
        digest = _build(job_id, state)
        with _LOCK:
            _CACHE[job_id] = (signature, digest)
        digests.append(digest)
        time.sleep(0)               # 冷读很多任务时让界面线程有机会跑
    with _LOCK:
        for stale in set(_CACHE) - seen:
            _CACHE.pop(stale, None)
    return digests


def grouped_by_project(core) -> dict[str, list[dict]] | None:
    """摘要按任务归属的项目分组；读不出来时返回 ``None``（调用方退回逐项目读取）。

    逐个项目推断语言方向时用它：``project_direction`` 不传任务时会走
    ``core.list_jobs()``，为每个项目把全部任务的完整状态读出并复制一遍。
    """
    try:
        digests = read_digests(core)
    except Exception:  # noqa: BLE001 - 列表必须仍能显示，只是变慢
        return None
    grouped: dict[str, list[dict]] = {}
    for job in digests:
        grouped.setdefault(core.resolved_project_id(job.get("state") or {}), []).append(job)
    return grouped


def jobs_of(grouped: dict[str, list[dict]] | None, project_id: str) -> list[dict] | None:
    """``grouped_by_project`` 里某个项目的任务；项目 ID 按任务归属同样的规则归一。"""
    if grouped is None:
        return None
    from folith import project as project_model
    key = project_model.canonical_project_id(project_id) \
        if str(project_id or "").strip() else project_model.SYSTEM_PROJECT_ID
    return grouped.get(str(key), [])


def clear() -> None:
    with _LOCK:
        _CACHE.clear()
