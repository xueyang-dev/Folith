#!/usr/bin/env python3
"""Generate high-resolution, privacy-clean showcase screenshots of Folith for documentation."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "tmp" / "showcase_outputs"
ASSETS = ROOT / "docs" / "assets"
PORT = 8588

os.environ["FOLITH_OUTPUT_DIR"] = str(OUTPUT)
sys.path.insert(0, str(ROOT))

import core
from folith import translation_core


def setup_showcase_fixtures():
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / ".onboarded").touch()

    # 1. Project 1: 技术专著本地化
    proj1 = core.create_project("技术专著本地化", description="面向大模型、系统工程与开源生态的专业翻译工作区")
    proj1_id = proj1["project_id"]

    # 2. Project 2: 金融科技白皮书
    proj2 = core.create_project("金融科技白皮书", description="国际金融架构与监管科技白皮书本地化")
    proj2_id = proj2["project_id"]

    # 3. Job 1 in Project 1: Agentic Translation Workspace Architecture
    pairs1 = [
        {
            "segment_id": "seg-001",
            "source": "The translation workspace preserves source context, terminology rules, and human review history.",
            "target": "翻译工作区完整保留原文上下文、术语规范与人工审校历史。",
            "initial_target": "初译：翻译工作区保留上下文和历史。",
            "reviewed": True,
            "review_status": "reviewed_clean",
            "target_provenance": "reviewed",
        },
        {
            "segment_id": "seg-002",
            "source": "Terminology decisions remain visible and editable throughout the entire localization lifecycle.",
            "target": "术语决策在整个本地化生命周期中对人工审校者保持清晰可见且可随时编辑。",
            "initial_target": "初译：术语决策在整个周期可见。",
            "reviewed": True,
            "review_status": "reviewed_clean",
            "target_provenance": "reviewed",
        },
        {
            "segment_id": "seg-003",
            "source": "Downstream delivery artifacts are verified against structured quality gates before final export.",
            "target": "在最终导出前，下游交付资产将由结构化质量门禁进行全面验证。",
            "initial_target": "初译：交付文件在导出前会验证质量门禁。",
            "reviewed": True,
            "review_status": "reviewed_clean",
            "target_provenance": "reviewed",
        },
        {
            "segment_id": "seg-004",
            "source": "Interrupted long-document translation tasks can seamlessly resume from the last saved breakpoint.",
            "target": "长文档翻译任务中断后，可随时从最近的保存断点无缝恢复继续执行。",
            "initial_target": "初译：中断的任务可以从断点恢复。",
            "reviewed": False,
            "review_status": "not_reviewed",
            "target_provenance": "generated",
        },
    ]

    glossary1 = [
        {
            "id": "term-01",
            "source": "translation workspace",
            "target": "翻译工作区",
            "preferred": "翻译工作区",
            "status": "locked",
            "domain": "本地化技术",
            "scope": "项目",
            "occurrences": [0],
            "note": "规范产品实体命名",
        },
        {
            "id": "term-02",
            "source": "quality gate",
            "target": "质量门禁",
            "preferred": "质量门禁",
            "status": "locked",
            "domain": "软件工程",
            "scope": "项目",
            "occurrences": [2],
            "note": "保持交付工程概念统一",
        },
        {
            "id": "term-03",
            "source": "localization lifecycle",
            "target": "本地化生命周期",
            "preferred": "本地化生命周期",
            "status": "locked",
            "domain": "本地化流程",
            "scope": "项目",
            "occurrences": [1],
            "note": "标准行业术语",
        },
    ]

    core.save_provider_config("DeepSeek", "deepseek-v4-flash", "sk-folio-demo-mock-key")

    state1 = core.new_job_state("Agentic Translation Workspace Architecture.docx")
    state1.update({
        "p1_done": True,
        "p2_done": True,
        "p3_done": False,
        "report_enabled": False,
        "target_lang": "简体中文",
        "project_id": proj1_id,
        "paras": [p["source"] for p in pairs1],
        "pairs": pairs1,
        "glossary": glossary1,
        "glossary_frozen": {"version": 1, "entries": glossary1, "frozen_at": "2026-09-22T10:00:00+08:00"},
        "document_profile": {
            "display_name": "Agentic Translation Architecture Guide",
            "domain": "计算机科学与人工智能",
            "subdomain": "大语言模型与本地化工程",
            "genre": "技术白皮书",
            "audience": "专业本地化工程师与译审专家",
            "register": "正式书面语",
            "style_constraints": "保持专业术语一致性，句式清晰严谨，保留专有名词与技术边界",
        },
        "profile_done": True,
        "translation_core_review_required": True,
        "review_evidence": [{
            "review_event_id": "rev-showcase-01",
            "review_scope": "current_translation",
            "segment_ids": [0, 1, 2, 3],
            "created_at": "2026-09-22T10:00:00+08:00",
            "completion_receipt": {
                "status": "completed",
                "reviewed_segment_ids": [0, 1, 2, 3],
            },
        }],
        "findings": [],
        "human_actions": [],
        "review_stats": {
            "reviewed_segments": 3,
            "batches_reviewed": 1,
            "blocking": 1,
            "actionable": 0,
            "informational": 0,
            "review_failed": 0,
        },
        "has_blocking": True,
        "translation_truth": {
            "authority": "CURRENT_TRANSLATION",
            "version": 1,
            "last_changed_at": "2026-09-22T10:00:00+08:00",
            "last_change": None,
        },
        "provider": "DeepSeek",
        "model": "deepseek-v4-flash",
        "stage": "TRANSLATED",
        "style_rules": "正式技术书面语",
        "delivery_config": core.default_delivery_config(),
    })

    # Add a realistic review finding on segment 4
    fp = translation_core.fingerprint({"segment": 3, "target": pairs1[3]["target"]})
    raw_finding = {
        "type": "review",
        "category": "terminology",
        "severity": "blocking",
        "status": "open",
        "segment_id": 3,
        "segment_index": 3,
        "location_key": "term:long-document",
        "entry_id": "term-01",
        "requires_human_confirmation": True,
        "summary": "建议核对长文档断点恢复机制在中文表述中的术语准确性",
        "source_span": "saved breakpoint",
        "target_span": "保存断点",
        "explanation": "技术白皮书中应统一对应 breakpoint 为「检查点」或「断点」，建议与项目术语表保持同步。",
        "recommendation": "可采纳建议译文或保留人工决议，以消除交付风险。",
        "suggested_target": "长文档翻译任务中断后，可随时从最近的保存检查点无缝恢复继续执行。",
        "detector": "Folith Agent Reviewer",
        "confidence": 0.94,
        "review_event_id": "rev-showcase-01",
        "reason": "术语一致性核验",
    }
    normalized = translation_core.normalize_finding(raw_finding, input_fingerprint=fp)
    normalized.update({
        "type": "review",
        "segment_index": 3,
        "review_event_id": "rev-showcase-01",
        "created_at": "2026-09-22T10:00:00+08:00",
    })
    state1["findings"].append(normalized)

    core.save_job_state("showcase-job", state1)
    core.save_source("showcase-job", b"Folith Architecture Guide Mock Source")
    core.update_runtime_state("showcase-job",
                              status="completed",
                              phase="completed",
                              phase_label="待审校",
                              started_at="2026-09-22T10:00:00+08:00",
                              last_heartbeat_at="2026-09-22T10:00:00+08:00",
                              last_progress_at="2026-09-22T10:00:00+08:00",
                              last_event="showcase")
    # Touch mtime to ensure showcase-job is the most recently updated task
    showcase_file = core.job_dir("showcase-job") / "state.json"
    future_time = time.time() + 300
    os.utime(showcase_file, (future_time, future_time))

    # 4. Job 2 in Project 1: Cloud-Native Service Mesh Architecture (Completed)
    pairs2 = [
        {
            "segment_id": "seg-001",
            "source": "A service mesh manages east-west traffic between microservices with mutual TLS encryption.",
            "target": "服务网格通过双向 TLS 加密管理微服务之间的东西向流量。",
            "initial_target": "初译：服务网格管理服务间流量并加密。",
            "reviewed": True,
            "review_status": "reviewed_clean",
            "target_provenance": "reviewed",
        },
        {
            "segment_id": "seg-002",
            "source": "Decoupling observability and traffic routing from application code simplifies operations.",
            "target": "将可观测性与流量路由从业务代码中解耦，能够显著简化运维复杂度。",
            "initial_target": "初译：解耦可观测性和路由简化运维。",
            "reviewed": True,
            "review_status": "reviewed_clean",
            "target_provenance": "reviewed",
        },
    ]
    state2 = core.new_job_state("Cloud-Native Service Mesh Architecture.docx")
    state2.update({
        "p1_done": True,
        "p2_done": True,
        "p3_done": False,
        "report_enabled": False,
        "target_lang": "简体中文",
        "project_id": proj1_id,
        "paras": [p["source"] for p in pairs2],
        "pairs": pairs2,
        "glossary": glossary1,
        "profile_done": True,
        "document_profile": {
            "display_name": "Cloud-Native Service Mesh Architecture",
            "domain": "云计算与系统架构",
            "subdomain": "分布式基础设施",
            "genre": "工程架构指南",
            "audience": "架构师与云原生工程师",
            "register": "技术工程规范",
        },
        "review_stats": {
            "reviewed_segments": 2,
            "batches_reviewed": 1,
            "blocking": 0,
            "actionable": 0,
            "informational": 0,
            "review_failed": 0,
        },
        "has_blocking": False,
        "provider": "DeepSeek",
        "model": "deepseek-v4-flash",
        "stage": "TRANSLATED",
        "delivery_config": core.default_delivery_config(),
    })
    core.save_job_state("service-mesh-job", state2)
    core.save_source("service-mesh-job", b"Service Mesh Architecture Mock Source")
    core.update_runtime_state("service-mesh-job",
                              status="completed",
                              phase="completed",
                              phase_label="已完成",
                              started_at="2026-09-22T09:00:00+08:00",
                              last_heartbeat_at="2026-09-22T09:30:00+08:00",
                              last_progress_at="2026-09-22T09:30:00+08:00",
                              last_event="completed")

    # 5. Job 3 in Project 2: Global Financial Data Governance Standard
    pairs3 = [
        {
            "segment_id": "seg-001",
            "source": "Cross-border financial transactions require rigorous data governance and continuous regulatory compliance.",
            "target": "跨境金融交易需要严格的数据治理与持续的监管合规。",
            "initial_target": "初译：跨境金融交易需要数据治理与合规。",
            "reviewed": True,
            "review_status": "reviewed_clean",
            "target_provenance": "reviewed",
        },
        {
            "segment_id": "seg-002",
            "source": "Every administrative operation is permanently recorded in the immutable audit trail.",
            "target": "每项管理操作均永久记录在不可篡改的审计追踪中。",
            "initial_target": "初译：管理操作记录在审计追踪中。",
            "reviewed": True,
            "review_status": "reviewed_clean",
            "target_provenance": "reviewed",
        },
    ]
    glossary2 = [
        {
            "id": "term-fin-01",
            "source": "data governance",
            "target": "数据治理",
            "preferred": "数据治理",
            "status": "locked",
            "domain": "金融科技",
            "scope": "项目",
            "occurrences": [0],
            "note": "合规核心概念",
        },
        {
            "id": "term-fin-02",
            "source": "audit trail",
            "target": "审计追踪",
            "preferred": "审计追踪",
            "status": "locked",
            "domain": "监管合规",
            "scope": "项目",
            "occurrences": [1],
            "note": "监管强制要求",
        },
    ]
    state3 = core.new_job_state("Global Financial Data Governance Standard.docx")
    state3.update({
        "p1_done": True,
        "p2_done": True,
        "p3_done": False,
        "report_enabled": False,
        "target_lang": "简体中文",
        "project_id": proj2_id,
        "paras": [p["source"] for p in pairs3],
        "pairs": pairs3,
        "glossary": glossary2,
        "profile_done": True,
        "document_profile": {
            "display_name": "Global Financial Data Governance Standard",
            "domain": "金融科技与合规",
            "subdomain": "国际银行监管标准",
            "genre": "监管合规白皮书",
            "audience": "合规官与风控总监",
            "register": "法律法规书面语",
        },
        "review_stats": {
            "reviewed_segments": 2,
            "batches_reviewed": 1,
            "blocking": 0,
            "actionable": 0,
            "informational": 0,
            "review_failed": 0,
        },
        "has_blocking": False,
        "provider": "DeepSeek",
        "model": "deepseek-v4-flash",
        "stage": "TRANSLATED",
        "delivery_config": core.default_delivery_config(),
    })
    core.save_job_state("fintech-job", state3)
    core.save_source("fintech-job", b"Fintech Governance Mock Source")
    core.update_runtime_state("fintech-job",
                              status="completed",
                              phase="completed",
                              phase_label="已完成",
                              started_at="2026-09-22T08:00:00+08:00",
                              last_heartbeat_at="2026-09-22T08:45:00+08:00",
                              last_progress_at="2026-09-22T08:45:00+08:00",
                              last_event="completed")

    # 6. Save Project Knowledge (Glossary & Translation Memory)
    proj1["glossary"] = glossary1
    core.save_project(proj1)

    proj2["glossary"] = glossary2
    core.save_project(proj2)

    tm1 = {}
    for p in pairs1[:3]:
        core.tm_put(tm1, p["source"], p["target"], "简体中文")
    for p in pairs2:
        core.tm_put(tm1, p["source"], p["target"], "简体中文")
    core.save_tm(tm1, project_id=proj1_id)

    tm2 = {}
    for p in pairs3:
        core.tm_put(tm2, p["source"], p["target"], "简体中文")
    core.save_tm(tm2, project_id=proj2_id)

    # Touch mtime to ensure showcase-job is the most recently updated task
    showcase_file = core.job_dir("showcase-job") / "state.json"
    future_time = time.time() + 300
    os.utime(showcase_file, (future_time, future_time))

    print("[OK] Showcase fixtures created in", OUTPUT)


def wait_port(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def run_screenshot(url: str, out_path: Path, section: str = "", view: str = ""):
    cmd = [
        str(ROOT / "venv" / "bin" / "python"),
        str(ROOT / "scripts" / "ui_screenshot.py"),
        "--url", url,
        "--out", str(out_path),
        "--section", section,
        "--view", view,
        "--wait-ms", "3500",
    ]
    env = {
        **os.environ,
        "FOLIO_PLAYWRIGHT_CORE": str(Path("~/.hermes/hermes-agent/node_modules/playwright-core").expanduser()),
        "FOLIO_CHROME": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "DEVICE_SCALE_FACTOR": "2",
    }
    subprocess.run(cmd, env=env, check=True)


def main():
    setup_showcase_fixtures()
    ASSETS.mkdir(parents=True, exist_ok=True)

    env = {**os.environ, "FOLITH_OUTPUT_DIR": str(OUTPUT)}
    server_cmd = [
        str(ROOT / "venv" / "bin" / "python"),
        "-m", "streamlit", "run", str(ROOT / "app.py"),
        "--server.headless", "true",
        "--server.port", str(PORT),
        "--server.address", "127.0.0.1",
        "--theme.primaryColor", "#004cfd",
        "--theme.textColor", "#131c2e",
        "--browser.gatherUsageStats", "false",
    ]

    print(f"Starting Streamlit server on port {PORT} with showcase fixtures...")
    proc = subprocess.Popen(server_cmd, env=env)
    try:
        url = f"http://127.0.0.1:{PORT}"
        if not wait_port(PORT, timeout=40):
            print("Streamlit failed to become ready in time.")
            return 1
        print("Streamlit ready! Capturing screenshots...")

        # 1. Projects Hub / Project Center
        p1 = ASSETS / "01-projects-preview.png"
        run_screenshot(url, p1, view="projects")

        # 2. New Task Wizard / Pipeline Setup
        p2 = ASSETS / "02-new-task-preview.png"
        run_screenshot(url, p2, view="new")

        # 3. Translation Workbench
        p3 = ASSETS / "03-workbench-preview.png"
        run_screenshot(url, p3, section="翻译")
        shutil.copyfile(p3, ASSETS / "workbench-preview.png")

        # 4. Review & Quality Gate
        p4 = ASSETS / "04-review-preview.png"
        run_screenshot(url, p4, section="审校")
        shutil.copyfile(p4, ASSETS / "review-preview.png")

        # 5. Delivery & Manifest
        p5 = ASSETS / "05-delivery-preview.png"
        run_screenshot(url, p5, section="交付")
        shutil.copyfile(p5, ASSETS / "delivery-preview.png")

        # 6. Task History / Jobs Archive
        p6 = ASSETS / "06-history-preview.png"
        run_screenshot(url, p6, view="history")

        # 7. Language Assets / Glossary & TM
        p7 = ASSETS / "07-language-assets.png"
        run_screenshot(url, p7, view="library")
        shutil.copyfile(p7, ASSETS / "assets-preview.png")

        # Also replace the legacy audit images in docs/ui-audit/24-minimal-workspace
        audit_dir = ROOT / "docs" / "ui-audit" / "24-minimal-workspace"
        if audit_dir.exists():
            shutil.copyfile(p3, audit_dir / "03-workbench-current.png")
            shutil.copyfile(p5, audit_dir / "04-overview-current.png")
            shutil.copyfile(p7, audit_dir / "05-terms-workbench.png")
            shutil.copyfile(p4, audit_dir / "13-review-blocker-workbench.png")
            print("[OK] Replaced legacy leaked audit images in 24-minimal-workspace")

        print("All showcase screenshots successfully generated!")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
