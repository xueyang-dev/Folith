"""场景门禁与 UI 控制台测试共用的离线 provider 替身。

一个实现，两个消费者：`scenario_gate_test.py`（后端闭环）与
`ui_console_test.py`（界面闭环）。重复一份 190 行的假 provider 会让两边
独立漂移，进而让"界面测的"和"后端测的"不再是同一件事。
"""

from __future__ import annotations

import json
import re

import core

_MARK_PROFILE = "文档画像"
_MARK_DIGEST = "文档理解器"
_MARK_SYNOPSIS = "全书理解器"
_MARK_TERMS = "长文翻译的术语编辑"
_MARK_KNOWLEDGE = "翻译流知识抽取器"
_MARK_REVIEWER = "独立的翻译审校专家"
_MARK_TRANSLATOR = "学术翻译专家"
_MARK_REPAIR = "以下译文未通过检查"

# 译文中的保留项回填（纯 ASCII 的非保留内容会触发"源语残留"检查）。
_SOURCE_SENTENCE_RE = re.compile(r"[^.!?…]+[.!?…]?")


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in _SOURCE_SENTENCE_RE.findall(text or "") if item.strip()]


class OfflineProvider:
    """确定性的离线 provider 替身，覆盖运行时会发出的每一种提示词。

    - 文档画像 / 单元摘要 / 全文概要 / 术语抽取：返回最小合法结构；
    - 翻译：逐句产出中文，回填全部 `core.PRESERVE_RE` 保留项，并遵守锁定术语；
    - 自动修复：默认真正修好问题；`repair_leaves_defect=True` 时故意不修，
      用于验证"模型修不了的问题必须留给人类"；
    - 独立审校：默认返回空 findings。
    """

    def __init__(
        self,
        *,
        glossary: list[dict] | None = None,
        drop_preserved_on: set[int] | None = None,
        repair_leaves_defect: bool = False,
        review_blocking_segments: set[int] | None = None,
    ) -> None:
        self.glossary = [dict(entry) for entry in (glossary or [])]
        self.drop_preserved_on = set(drop_preserved_on or ())
        self.repair_leaves_defect = repair_leaves_defect
        self.review_blocking_segments = set(review_blocking_segments or ())
        self.calls: dict[str, int] = {
            "profile": 0, "digest": 0, "synopsis": 0, "terms": 0,
            "translate": 0, "repair": 0, "review": 0, "knowledge": 0,
        }

    # ---------- 提示词分发 ----------

    def __call__(self, provider, api_key, model, system_prompt, user_prompt,
                 temperature=0.1, **kwargs):
        system = system_prompt or ""
        if _MARK_PROFILE in system:
            return self._profile()
        if _MARK_DIGEST in system:
            self.calls["digest"] += 1
            return self._digest(user_prompt)
        if _MARK_SYNOPSIS in system:
            self.calls["synopsis"] += 1
            return self._synopsis()
        if _MARK_TERMS in system:
            self.calls["terms"] += 1
            return self._terms(user_prompt)
        if _MARK_KNOWLEDGE in system:
            self.calls["knowledge"] += 1
            return "[]"
        if _MARK_REVIEWER in system:
            self.calls["review"] += 1
            return json.dumps(self._review(user_prompt))
        if _MARK_TRANSLATOR in system:
            if _MARK_REPAIR in (user_prompt or ""):
                self.calls["repair"] += 1
                return json.dumps(self._repair(user_prompt))
            self.calls["translate"] += 1
            return json.dumps(self._translate(user_prompt))
        raise AssertionError(
            f"OfflineProvider 遇到未识别的提示词，无法保证行为确定性：\n{system[:400]}")

    # ---------- 各阶段响应 ----------

    def _profile(self) -> str:
        self.calls["profile"] += 1
        return json.dumps({
            "domain": "生态学",
            "subdomain": "恢复生态学",
            "genre": "学术专著",
            "audience": "研究人员",
            "register": "正式书面语",
            "style_constraints": "保持术语一致，不使用口语化表达。",
            "confidence": 0.8,
            "sections": [],
        }, ensure_ascii=False)

    def _digest(self, user_prompt: str) -> str:
        return json.dumps({
            "summary": "本单元讨论生态恢复的过程与机制。",
            "translation_notes": ["保持术语一致"],
        }, ensure_ascii=False)

    def _synopsis(self) -> str:
        return json.dumps({
            "summary": "全文讨论生态恢复的过程、机制与不确定性。",
            "document_arc": "从现象描述推进到机制讨论。",
            "themes": ["生态恢复", "干扰机制"],
        }, ensure_ascii=False)

    def _terms(self, user_prompt: str) -> str:
        source, target = (("ecological succession", "生态演替")
                          if "ecological succession" in user_prompt.casefold()
                          else ("Ecological restoration", "生态恢复"))
        return json.dumps([{"Source": source, "Target": target, "domain": "生态学"}], ensure_ascii=False)

    def _review(self, user_prompt: str) -> dict:
        """独立审校响应：默认无问题；可对指定全局 segment_id 报告 blocking。"""
        findings = []
        if self.review_blocking_segments:
            # 审校提示词按 `segment_id: <全局ID>` 给出待审段落。
            present = {int(value) for value in
                       re.findall(r"^segment_id:\s*(\d+)\s*$", user_prompt or "",
                                  re.MULTILINE)}
            for segment_id in sorted(present & self.review_blocking_segments):
                findings.append({
                    "segment_id": segment_id,
                    "category": "semantic_accuracy",
                    "severity": "blocking",
                    "summary": "该段核心论断与原文不符，需要人工判断。",
                    "source_span": None,
                    "target_span": None,
                    "explanation": "译文改变了原文的限定条件，属于语义层面的判断，"
                                   "确定性检查无法自动修复。",
                    "recommendation": "对照原文确认限定条件后再决定是否接受。",
                    "confidence": 0.9,
                    "detector": "Semantic QA",
                    "evidence_refs": [],
                })
        return {"findings": findings, "evidence_requests": []}

    def _translate(self, user_prompt: str) -> list[str]:
        return [self._target(text, index)
                for index, text in enumerate(self._numbered(user_prompt))]

    def _repair(self, user_prompt: str) -> list[str]:
        """修复响应：默认把每条译文重建为合格译文；按选项故意保留缺陷。"""
        originals = re.findall(r"^\s+译文：(.*)$", user_prompt or "", re.MULTILINE)
        sources = re.findall(r"^\d+\.\s*原文：(.*)$", user_prompt or "", re.MULTILINE)
        if self.repair_leaves_defect:
            return [item.strip() for item in originals]
        return [self._target(src.strip(), index)
                for index, src in enumerate(sources or originals)]

    # ---------- 译文构造 ----------

    def _numbered(self, user_prompt: str) -> list[str]:
        """取出"待翻译段落"段落中的编号原文（上下文包与旧路径都适用）。"""
        marker = "待翻译段落"
        position = (user_prompt or "").rfind(marker)
        section = user_prompt[position:] if position >= 0 else (user_prompt or "")
        return re.findall(r"^\s*\d+\.\s*(.+)$", section, re.MULTILINE)

    def _target(self, source: str, index: int) -> str:
        source = source or ""
        parts: list[str] = []
        for sentence in _sentences(source):
            parts.append(self._sentence(sentence, index))
        body = "".join(parts) or "本段无可翻译内容。"
        preserved = list(core.extract_preserved_tokens(source).keys())
        if preserved:
            keep = [token for token in preserved
                    if not (self.drop_preserved_on and index in self.drop_preserved_on)]
            if keep:
                body += " " + " ".join(keep)
            elif "{{artifact_path}}" in preserved and self.drop_preserved_on:
                # 故意丢弃占位符：用于验证确定性检查能拦住结构破坏。
                body += " 已写入输出目录。"
        return body

    def _sentence(self, sentence: str, index: int) -> str:
        """逐句产出中文；锁定术语强制首选译名，保留项整句回填原文。"""
        for entry in self.glossary:
            term = str(entry.get("source") or "")
            if entry.get("behavior") == "preserve" and term and term.casefold() in sentence.casefold():
                return f"{sentence.strip()}"
        targets = [str(entry.get("target") or entry.get("preferred") or "")
                   for entry in self.glossary
                   if entry.get("behavior") != "preserve"
                   and str(entry.get("source") or "")
                   and str(entry["source"]).casefold() in sentence.casefold()]
        targets = [item for item in targets if item]
        stem = "；".join(dict.fromkeys(targets))
        if stem:
            return f"本段围绕{stem}展开讨论，并给出相应的观测结果。"
        return f"本句陈述了第{index + 1}项观测事实，并说明其与整体过程的关系。"
