"""Changing extraction mode must requeue the profile before Step 2."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
import core


class _Session(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


@pytest.mark.parametrize("mode", ["force", "off"])
def test_changing_ocr_mode_requeues_profile_before_step_two(mode):
    source = Path(__file__).resolve().parents[1] / "app.py"
    names = {"_task_profile_signature", "_task_profile_is_ready", "_request_step"}
    functions = [node for node in ast.parse(source.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    state = _Session(task_files=[{"name": "scan.pdf", "bytes": b"profile-input"}],
                     target_lang="简体中文", ocr_language="eng", ocr_quality="standard",
                     ocr_mode="auto", doc_profile={"domain": "general"},
                     style_recommendation={"preset": "faithful"},
                     style_selection={"preset": "faithful"})
    namespace = {"st": SimpleNamespace(session_state=state), "core": core}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
    state.task_profile_signature = namespace["_task_profile_signature"]()
    namespace["_request_step"](2)
    assert state.task_step == 2
    assert "pending_profile_step" not in state
    state.task_step = 1
    state.ocr_mode = mode
    assert not namespace["_task_profile_is_ready"]()
    namespace["_request_step"](2)
    assert state.task_step == 1
    assert state.style_profiling_state == "running"
    assert state.pending_profile_step == 2
