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
        state = core.load_job_state(job_id)
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


def clear() -> None:
    with _LOCK:
        _CACHE.clear()
