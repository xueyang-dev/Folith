"""One preset per fresh process + data dir; EngineConfig mirrors the preset so its flags really apply."""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]; sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ROOT / "tests")]
key = sys.argv[1]
tmp = Path(tempfile.mkdtemp(prefix=f"folith-fair-")); data = tmp / "data"; data.mkdir()
os.environ.update(NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
                  FOLITH_DESKTOP_DATA_DIR=str(data), FOLITH_OUTPUT_DIR=str(data), FOLITH_DESKTOP_LOG_DIR=str(tmp / "logs"))
import core; core.OUTPUT_DIR = data
from folith.qt import adapter
from folith.application import task_setup
from docx import Document
wl = json.loads((ROOT / "benchmark/translation-efficiency/workload.json").read_text())
doc = Document()
for s in wl["segments"]: doc.add_paragraph(s)
src = tmp / "workload.docx"; doc.save(src)
ready = tmp / "ready.json"
stub = subprocess.Popen([sys.executable, str(ROOT / "scripts/d0_fake_llm_server.py"), "--port", "0", "--ready-file", str(ready)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
while not ready.exists(): time.sleep(0.05)
url = json.loads(ready.read_text())["url"]
p = task_setup.STRATEGY_PRESETS[key]
cfg = adapter.EngineConfig(provider="自定义中转站", model="stub-model", api_key="k", base_url=url, target_lang="简体中文",
    enable_review=p["enable_review"], auto_term=p["auto_term"], use_tm=p["use_tm"], enable_understanding=p["enable_understanding"],
    enable_report=False, enable_source_cleanup=False)
job = adapter.import_documents([src], cfg)[0].job_id
setup = dict(adapter.task_setup_view(cfg, job)); setup["strategy"] = dict(p); setup["strategy_preset"] = key
setup["delivery"] = dict(task_setup.DELIVERY_PRESET_CONFIGS["standard"])
adapter.apply_task_setup(cfg, job, setup)
eff = adapter.pipeline_kwargs(cfg, adapter.load_state(job))
adapter.start_job(job, "workload.docx", src.read_bytes(), cfg)
for _ in range(1800):
    st = adapter.load_state(job); status = adapter.runtime_view(job, st).get("status")
    if status in ("completed", "failed", "cancelled", "interrupted"): break
    time.sleep(0.5)
st = adapter.load_state(job); u = st.get("llm_usage") or {}
roles = {r: {"calls": v.get("calls") or 0,
             "in": (v.get("prompt_tokens") or 0) + (v.get("estimated_prompt_tokens") or 0),
             "out": (v.get("completion_tokens") or 0) + (v.get("estimated_completion_tokens") or 0)}
         for r, v in (u.get("by_role") or {}).items()}
print(json.dumps({"preset": key, "status": status,
    "effective": {k: eff.get(k) for k in ["enable_review", "auto_term", "use_tm", "enable_understanding", "strict_terminology_governance"]},
    "roles": roles, "profile": bool(st.get("document_profile")), "glossary": len(st.get("glossary") or []),
    "review_events": len(st.get("review_evidence") or [])}, ensure_ascii=False), flush=True)
stub.terminate()
from qt_workers import drain_job_workers; drain_job_workers(core)
