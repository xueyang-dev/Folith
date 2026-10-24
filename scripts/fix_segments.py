"""定点重译任务中的问题段落（截断/审校 blocking），并清洗翻译记忆。

默认**只生成候选**：当前译文、人工接受标记和旧 findings 都不会被改写。确认差异后
再用 ``--adopt`` 采用，也可以在工作台里逐段采用——两条路走的是同一个核心事务
（``core.adopt_translation_candidate``），因此都带身份与基线校验。

用法（项目根目录）：
    OPENCODE_GO_API_KEY=... .venv/bin/python scripts/fix_segments.py --job <job_id>
    OPENCODE_GO_API_KEY=... .venv/bin/python scripts/fix_segments.py --job <job_id> --adopt

``--clean-tm`` 才会剔除翻译记忆里的不完整译文（会备份原文件）；它是独立于候选流程
的显式动作，默认不动 TM。
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core


def _describe(view: dict) -> str:
    checks = view.get("checks") or {}
    notes = []
    if checks.get("blocking"):
        notes.append(f"必须处理 {checks['blocking']}")
    if checks.get("actionable"):
        notes.append(f"建议检查 {checks['actionable']}")
    before = len(view.get("current_target") or "")
    after = len(view.get("candidate_target") or "")
    return (f"段 {int(view.get('segment_index') or 0) + 1}: {before} -> {after} 字符"
            + (f"（{'、'.join(notes)}）" if notes else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--provider", default="OpenCode Go")
    ap.add_argument("--model", default="glm-5.2")
    ap.add_argument("--target-lang", default="简体中文")
    ap.add_argument("--adopt", action="store_true",
                    help="生成后立即采用这些候选（默认只生成，不改正文）")
    ap.add_argument("--clean-tm", action="store_true",
                    help="先剔除翻译记忆里的不完整译文（会备份）")
    args = ap.parse_args()

    env_key = {"OpenCode Go": "OPENCODE_GO_API_KEY",
               "DeepSeek": "DEEPSEEK_API_KEY", "OpenAI": "OPENAI_API_KEY",
               "Gemini": "GEMINI_API_KEY"}.get(args.provider, "FOLITH_API_KEY")
    api_key = os.environ.get(env_key) or os.environ.get("FOLITH_API_KEY")
    if not api_key:
        sys.exit(f"请设置 {env_key} 或 FOLITH_API_KEY")

    if args.clean_tm:
        tm_file = core.tm_path()
        if tm_file.is_file():
            shutil.copy(tm_file, tm_file.with_suffix(".json.bak"))
        tm = core.load_tm()
        poison = [k for k, v in tm.items()
                  if core.is_incomplete_translation(k, (v or {}).get("target", ""))]
        for k in poison:
            del tm[k]
        core.save_tm(tm)
        print(f"翻译记忆：剔除 {len(poison)} 条不完整译文，剩余 {len(tm)} 条")

    state = core.load_job_state(args.job)
    if not state:
        sys.exit(f"找不到任务 {args.job}")
    pairs = state["pairs"]

    # 1) 待修复集合：完整性规则命中 + 审校 blocking
    flagged = {i for i, pr in enumerate(pairs)
               if core.is_incomplete_translation(pr["source"], pr["target"])}
    review_blocked = {f["segment_index"] for f in state.get("findings", [])
                      if f.get("severity") == "blocking" and f.get("type") == "review"}
    to_fix = sorted(flagged | review_blocked)
    print(f"待重译段落：{len(to_fix)}（完整性 {len(flagged)} + 审校 blocking {len(review_blocked - flagged)}）")
    if not to_fix:
        return

    glossary = core.normalize_glossary(
        [{"source": k, "target": v, "behavior": "translate", "status": "provisional"}
         for k, v in (state.get("auto_terms") or {}).items()])
    # 2) 复用产品共享的定点重译：只生成候选，不写正文。
    state, created = core.retranslate_segments(
        args.job, to_fix, args.provider, api_key, args.model, args.target_lang,
        glossary=glossary, actor="system")
    if not created:
        sys.exit("没有生成任何候选；正文未变动。")

    views = [view for view in core.candidate_view(args.job)
             if view.get("segment_index") in set(created)]
    views.sort(key=lambda view: int(view.get("segment_index") or 0))
    print(f"已生成候选 {len(created)}/{len(to_fix)} 段（正文未变动）：")
    for view in views:
        print("  " + _describe(view))

    if not args.adopt:
        print("确认差异后重跑并加 --adopt 采用，或在工作台里逐段采用。")
        return

    # 3) 采用同样走核心事务：失败说明原因，不假定成功。
    adopted = 0
    for view in views:
        result = core.adopt_translation_candidate(
            args.job, view["candidate_id"], actor="system")
        number = int(view.get("segment_index") or 0) + 1
        if result.get("committed"):
            adopted += 1
            print(f"  采用段 {number}："
                  + ("同文采用，正文未变化" if not result.get("changed_text", True)
                     else "已写入正文，该段需要重新审校"))
        elif result.get("already_adopted"):
            print(f"  段 {number} 之前已采用过，未重复写入")
        else:
            print(f"  段 {number} 未采用：{result.get('reason') or '原因未知'}")

    state = core.load_job_state(args.job) or state
    stats = state.get("review_stats") or {}
    print(f"完成：采用 {adopted}/{len(views)} 段；"
          f"blocking {stats.get('blocking', 0)} · actionable {stats.get('actionable', 0)} · "
          f"informational {stats.get('informational', 0)}")
    remaining = core.pending_candidate_count(args.job)
    if remaining:
        print(f"仍有 {remaining} 条候选待处理。")


if __name__ == "__main__":
    main()
