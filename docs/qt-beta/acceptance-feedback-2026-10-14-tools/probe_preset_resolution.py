import json, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]; sys.path[:0] = [str(ROOT)]
tmp = Path(tempfile.mkdtemp()); data = tmp / "data"; data.mkdir()
os.environ.update(FOLITH_DESKTOP_DATA_DIR=str(data), FOLITH_OUTPUT_DIR=str(data))
import core; core.OUTPUT_DIR = data
from folith.qt import adapter
from folith.application import task_setup, settings as app_settings
from docx import Document
d = Document(); d.add_paragraph("Hello world. This is a probe."); src = tmp / "p.docx"; d.save(src)
cfg = adapter.EngineConfig(provider="自定义中转站", model="m", api_key="k", base_url="http://127.0.0.1:9/v1",
    target_lang="简体中文", enable_review=True, enable_report=False, enable_understanding=False, enable_source_cleanup=False)
job = adapter.import_documents([src], cfg)[0].job_id
setup = dict(adapter.task_setup_view(cfg, job)); setup["strategy"] = dict(task_setup.STRATEGY_PRESETS["快速"]); setup["strategy_preset"] = "快速"
setup["delivery"] = dict(task_setup.DELIVERY_PRESET_CONFIGS["standard"])
adapter.apply_task_setup(cfg, job, setup)
st = adapter.load_state(job)
print("after apply:", {k: st.get(k) for k in ["enable_review", "auto_term", "use_tm", "enable_understanding"]})
kw = adapter.pipeline_kwargs(cfg, st)
print("pipeline_kwargs(config, state):", {k: kw.get(k) for k in ["enable_review", "auto_term", "use_tm", "enable_understanding"]})
src_layers = adapter.settings_sources(cfg, {})
res = app_settings.resolve_settings(src_layers) if hasattr(app_settings, "resolve_settings") else None
print("start_job layers (state={}):", res and {k: getattr(res, k, None) for k in ["enable_review", "auto_term"]})
