"""合成夹具：项目 / 未分类 / 候选，供界面测试与截图使用（不含任何真实文档）。"""
from __future__ import annotations


def _term(source, target, status="locked", occurrences=(), domain=""):
    return {"source": source, "target": target, "preferred": target,
            "status": status, "behavior": "translate", "domain": domain,
            "occurrences": list(occurrences)}


_SEGMENT = iter(range(100, 10_000))


def _candidate(source, target, *, hits=3, reviewed=True):
    segment = next(_SEGMENT)    # 每条候选各占一段，互不牵连
    return {"source": source, "observed_target": target,
            "first_observed_segment": segment, "occurrences": list(range(hits)),
            "observed_segments": [segment], "status": "emergent_candidate",
            "origin": "translation_observation", "scope": "document",
            "kind": "term", "confidence": 0.7 if reviewed else 0.35}


def add_job(core, job_id, filename, *, project_id=None, terms=(), candidates=(),
            pairs=(), source="English", target="简体中文"):
    state = core.new_job_state(filename)
    state["project_id"] = project_id
    state["source_lang"], state["target_lang"] = source, target
    state["glossary"] = list(terms)
    state["knowledge_candidates"] = list(candidates)
    state["pairs"] = [{"source": s, "target": t, "reviewed": True,
                       "segment_uid": f"{job_id}-{i}"} for i, (s, t) in enumerate(pairs)]
    core.save_job_state(job_id, state)
    return state


def build(core):
    """一个有方向、有术语、有待审核的项目；一个方向不同的项目；两个未分类任务。"""
    device = core.create_project("设备说明书项目", source_lang="English", target_lang="简体中文")
    device_id = device["project_id"]
    middle = core.create_project("中东市场资料", source_lang="简体中文", target_lang="العربية")
    annual = core.create_project("年度报告 2026", source_lang="English", target_lang="简体中文")
    core.set_project_term(device_id, "firmware update", "固件更新", domain="软件")
    core.set_project_term(device_id, "status light", "状态指示灯", domain="硬件",
                          note="指机身正面的 LED，不是 App 里的图标")
    core.set_project_term(device_id, "pairing mode", "配对模式", domain="操作")

    add_job(core, "manual-ch3", "产品手册 第 3 章.docx", project_id=device_id,
            terms=[_term("status light", "状态灯", occurrences=[1, 2, 3])],
            candidates=[_candidate("hub", "集线器", hits=4),
                        _candidate("hubs", "集线器", hits=3),
                        _candidate("night mode", "夜间模式", hits=4),
                        _candidate("quick start guide", "快速入门指南", hits=3, reviewed=False),
                        _candidate("once only", "只一次", hits=1, reviewed=False),
                        _candidate("status light", "状态灯", hits=1)],
            pairs=[("Plug the hub into a power outlet.", "将集线器插入电源插座。")])
    add_job(core, "help-center", "帮助中心文章.docx", project_id=device_id,
            candidates=[_candidate("motion sensor", "运动传感器", hits=5),
                        _candidate("motion sensors", "运动传感器", hits=2)])
    add_job(core, "site-copy", "官网产品页文案.md", project_id=device_id)
    add_job(core, "letter", "季度投资者信.pdf", project_id=device_id)
    add_job(core, "report-a", "年度报告.docx", project_id=annual["project_id"])
    add_job(core, "arabic", "展会海报文案.docx", project_id=None, source="简体中文",
            target="العربية", terms=[_term("展会", "معرض")])
    add_job(core, "notes", "Release notes 4.2.md", project_id=None,
            terms=[_term("rollback", "回滚", occurrences=range(6)),
                   _term("feature flag", "功能开关", occurrences=range(4)),
                   _term("deprecation", "弃用", "provisional", occurrences=range(3)),
                   _term("hotfix", "热修复", occurrences=range(2))],
            candidates=[_candidate("canary build", "金丝雀版本", hits=3)],
            pairs=[("Roll back the release.", "回滚该版本。"), ("Ship a hotfix.", "发布热修复。")])
    add_job(core, "warranty", "Warranty terms.pdf", project_id=None,
            terms=[_term("limited warranty", "有限保修", occurrences=range(9)),
                   _term("proof of purchase", "购买凭证", "provisional", occurrences=range(3))])
    return {"device": device_id, "middle": middle["project_id"], "annual": annual["project_id"]}
