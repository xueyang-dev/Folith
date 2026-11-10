import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from folith.qt.selftest import open_job_and_wait
from qt_client_window_test import _make_docx, client, qt_app  # noqa

def test_probe(client):
    window, core, adapter = client["window"], client["core"], client["adapter"]
    job = client["job"] if "job" in client else None
    from qt_client_window_test import client as _c  # noqa
    # build a job like _book_job
    tmp = client["tmp"]
    source = tmp / "book.docx"
    source.write_bytes(_make_docx(["x"]))
    job_id = adapter.import_documents([source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    segments = 900
    state["paras"] = [f"p{i}" for i in range(segments)]
    state["pairs"] = [{"source": f"p{i}", "target": (f"t{i}" if i < 450 else ""),
                       "reviewed": False, "segment_uid": f"u{i}"} for i in range(segments)]
    core.save_job_state(job_id, state)
    open_job_and_wait(window, job_id)
    window.timer.stop()
    path = core._job_repo.get_job_state_path(job_id)
    before = path.stat()
    state = core.load_job_state(job_id)
    state["pairs"][450]["target"] = "仍然显示已提交的译文"
    core.save_job_state(job_id, state)
    after = path.stat()
    print(f"PROBE before={before.st_mtime_ns},{before.st_size} after={after.st_mtime_ns},{after.st_size} same={before.st_mtime_ns==after.st_mtime_ns and before.st_size==after.st_size}", file=sys.stderr, flush=True)
    print(f"PROBE reloaded={core.load_job_state(job_id)['pairs'][450]['target']!r}", file=sys.stderr, flush=True)
    print(f"PROBE feed={getattr(window,'_delta_feed',None)}", file=sys.stderr, flush=True)
