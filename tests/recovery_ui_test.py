"""Recovery summary and History/workspace resume-surface regressions."""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core
from folith import checkpoint


def _interrupted_job(job_id):
    state = core.new_job_state("interrupted.docx")
    state.update(
        p1_done=True, p2_done=False, stage="TRANSLATING",
        pairs=[
            {"source": "First", "target": "第一"},
            {"source": "Second", "target": "第二"},
        ],
        tm_recovered_count=2,
    )
    core.save_job_state(job_id, state)
    checkpoint.append_event(core.job_dir(job_id), {
        "batch": 0, "offset": 0, "phase": "generation_started",
        "segment_count": 2,
    })
    checkpoint.append_event(core.job_dir(job_id), {
        "batch": 0, "offset": 0, "phase": "state_commit_done",
        "pairs_count": 2,
    })
    checkpoint.append_event(core.job_dir(job_id), {
        "batch": 1, "offset": 2, "phase": "generation_started",
        "segment_count": 2,
    })
    return state


def test_recovery_ui():
    from streamlit.testing.v1 import AppTest

    root = Path(__file__).resolve().parent.parent
    tmp = Path(tempfile.mkdtemp(prefix="recovery-ui-"))
    old_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    original_pipeline = core.run_job_pipeline
    try:
        job_id = "recoveryui0000001"
        state = _interrupted_job(job_id)
        summary = core.recovery_summary(job_id, state)
        assert summary["auto_save_enabled"]
        assert summary["completed_batch_count"] == 1
        assert summary["total_batches"] == 2
        assert summary["current_batch"] == {
            "number": 2, "start_segment": 2, "end_segment": 3,
            "completed_segments": 0,
            "segment_count": 2, "regenerate_segments": 2,
        }
        assert summary["can_resume"] and summary["recovered_tm_entries"] == 2
        assert core.task_status_label(state) == "处理中断"

        at = AppTest.from_file(str(root / "app.py"), default_timeout=30)
        at.run()
        next(b for b in at.sidebar.button if b.label == "历史任务").click()
        at.run()
        assert not at.exception, f"历史任务渲染异常：{at.exception}"
        assert any("处理中断" in m.value for m in at.markdown)
        assert any("自动保存已开启" in c.value for c in at.caption)
        resume_buttons = [b for b in at.button if b.label == "继续处理"]
        assert len(resume_buttons) == 1
        assert not any(b.label == "从断点继续" for b in at.button)
        assert any("0/2 段" in w.value for w in at.warning)

        def fake_pipeline(job_id, filename, file_bytes, **kwargs):
            resumed = core.load_job_state(job_id)
            resumed.update(p2_done=True, p3_done=False, report_enabled=False,
                           stage="TRANSLATED", delivery_status="draft")
            core.save_job_state(job_id, resumed)
            return resumed

        core.run_job_pipeline = fake_pipeline
        resume_buttons[0].click()
        at.run()
        assert not at.exception, f"从断点继续后异常：{at.exception}"
        assert at.session_state["active_job_id"] == job_id
        # worker 是**后台线程**：「继续处理」返回时它仍在跑。任务状态（state.json）
        # 由流水线写、运行状态（runtime_state.json）由 worker 收尾时写，两者之间
        # 有一个真实窗口。不在这个窗口里断言，否则「任务是否已完成」会变成一个
        # 计时问题而不是一个事实（`runtime_status_test.py` 用同样的有界等待）。
        deadline = time.time() + 5
        while core.is_job_worker_alive(job_id) and time.time() < deadline:
            time.sleep(0.02)
        at.run()
        assert not at.exception, f"恢复后的当前任务面板异常：{at.exception}"
        assert any("<h2>翻译</h2>" in s.value for s in at.markdown)
        # 断点继续后该任务已完成（p2_done=True 且未启用报告）：任务 Banner 应显示
        # 完成状态，而**不应**再渲染运行区里的引擎细节与「继续处理」。
        # 这条断言是回归防线：缺失 enable_annotate 的旧任务曾被误判为
        # idle_incomplete，导致已完成的任务仍显示运行区与继续处理按钮。
        assert core._runtime_business_complete(core.load_job_state(job_id))
        assert core.build_job_runtime_view(job_id).get("runtime_status") == "completed"
        assert not any("最近活动" in s.value for s in at.markdown)
        assert not any("worker id" in c.value for c in at.caption)
        assert not any(b.label == "继续处理" for b in at.button), \
            "已完成的任务不应再出现「继续处理」"
        print("  ✓ Recovery UI：History 状态、断点继续与 workspace 恢复可见")
    finally:
        core.run_job_pipeline = original_pipeline
        core.OUTPUT_DIR = old_dir
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_recovery_ui()
