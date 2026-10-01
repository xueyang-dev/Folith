"""段落"是不是正文"的判定必须按 Unicode 语义，而不是按语言区间枚举。

发布阻断级缺陷（本次修复）：判定用的是 `[A-Za-z0-9\\u4e00-\\u9fff]`，于是纯
西里尔 / 谚文 / 阿拉伯段落被判成"装饰行"，**原样保留、标成已审校、通过交付
检查**——译文就是原文，而且是静默的。

修法是换机制而不是加区间：任何 Unicode 字母（L*）或数字（N*）即正文。
按脚本列区间永远会漏掉下一个语言；"这一行是不是文字"本身就是一个 Unicode
类别问题。

运行：`python -m pytest tests/text_content_detection_test.py -q`
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import core  # noqa: E402
from offline_provider import OfflineProvider  # noqa: E402

# 覆盖需求点名的脚本，外加几个同样"非拉丁"但更少见的脚本：
# 漏掉哪一个，哪一种语言就会被静默原样保留。
CONTENT_SAMPLES = [
    ("拉丁", "The canopy closure index was recomputed."),
    ("汉字", "会议明天开始。"),
    ("西里尔", "Заседание начнётся завтра."),
    ("谚文", "회의는 내일 시작됩니다."),
    ("阿拉伯", "يبدأ الاجتماع غدًا."),
    ("天城文", "बैठक कल शुरू होगी।"),
    ("希腊", "Η συνάντηση ξεκινά αύριο."),
    ("泰文", "การประชุมจะเริ่มพรุ่งนี้"),
    ("希伯来", "הפגישה תתחיל מחר."),
    ("半角数字", "2026-09-19"),
    ("全角数字", "１２３４５"),
    ("罗马数字", "Ⅻ"),
]

# 真正没有正文的行：标点 / 分隔符 / 装饰符号，必须继续按装饰行处理。
DECORATIVE_SAMPLES = [
    "* * *",
    "***",
    "— — —",
    "◇◇◇",
    "···",
    "· · ·",
    "////",
    "†††",
    "\u3000",
    "————",
    "( ) [ ] { }",
    "...",
]


def test_content_detection_is_unicode_based():
    for label, text in CONTENT_SAMPLES:
        assert core.has_textual_content(text), f"{label} 必须被判定为正文：{text!r}"
    for text in DECORATIVE_SAMPLES:
        assert not core.has_textual_content(text), \
            f"装饰 / 分隔行不是正文：{text!r}"


def test_tm_eligibility_follows_the_same_rule():
    """记忆资格与正文判定同源：非拉丁正文同样有资格进入翻译记忆。"""
    for label, text in CONTENT_SAMPLES:
        assert core._tm_eligible(text, "某译文"), f"{label} 应具备记忆资格"
    for text in DECORATIVE_SAMPLES:
        assert not core._tm_eligible(text, "某译文"), \
            f"装饰行不得入库：{text!r}"
    assert not core._tm_eligible("Hello.", ""), "空译文不得入库"
    assert core._tm_eligible("Hello.", "你好。")


def test_checkpoint_eligibility_shares_the_same_predicate():
    """检查点恢复用的是同一个判定（重复一份正则 = 迟早漂移）。"""
    from folith import checkpoint

    for label, text in CONTENT_SAMPLES:
        assert checkpoint._eligible(text, "某译文"), label
    for text in DECORATIVE_SAMPLES:
        assert not checkpoint._eligible(text, "某译文"), text


def _write_docx(tmp: Path, paragraphs, name: str = "scripts.docx") -> Path:
    from docx import Document as Docx

    document = Docx()
    for text in paragraphs:
        document.add_paragraph(text)
    path = tmp / name
    document.save(str(path))
    return path


# 只解析"待翻译段落"小节：前后文上下文里可以合法出现装饰行（那是上下文，
# 不是待译内容），所以不能对整条提示词做子串搜索。
_SUBMITTED_MARKER = "待翻译段落"
_SUBMITTED_LINE = re.compile(r"^\s*\d+\.\s*(.+?)\s*$", re.MULTILINE)


def _submitted_segments(user_prompt: str) -> list[str]:
    """取出真正提交给模型的待译段落（与 OfflineProvider 的解析口径一致）。"""
    position = (user_prompt or "").rfind(_SUBMITTED_MARKER)
    if position < 0:
        return []
    return _SUBMITTED_LINE.findall(user_prompt[position:])


def test_non_latin_paragraphs_enter_the_translation_pipeline(tmp_path):
    """西里尔 / 谚文 / 阿拉伯段落必须真的被翻译，装饰行必须原样保留。"""
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    provider = OfflineProvider()
    prompts: list[tuple[str, str]] = []

    def llm(provider_name, api_key, model, system_prompt, user_prompt,
            temperature=0.1, **kwargs):
        prompts.append((system_prompt or "", user_prompt or ""))
        return provider(provider_name, api_key, model, system_prompt, user_prompt,
                        temperature, **kwargs)

    core.call_llm = llm
    try:
        body = ["Заседание начнётся завтра.", "회의는 내일 시작됩니다.",
                "يبدأ الاجتماع غدًا."]
        decorative = ["* * *", "— — —"]
        paragraphs = body + decorative
        path = _write_docx(tmp_path, paragraphs)
        state = core.new_job_state(path.name)
        state["paras"] = list(paragraphs)

        result = core.translate_stage(
            state, "unicode-scripts", [], "DeepSeek", "k", "m", "简体中文", "",
            enable_review=True, use_tm=True)
        pairs = result["pairs"]

        for index, source in enumerate(body):
            pair = pairs[index]
            assert pair["target"] != source, \
                f"{source!r} 必须被翻译，而不是原样保留"
            assert pair.get("from_tm") is not True
            assert pair["reviewed"] is True and \
                pair["review_status"] == "reviewed_clean"
        for index in range(len(body), len(pairs)):
            pair = pairs[index]
            assert pair["target"] == pair["source"], "装饰行必须原样保留"

        # 装饰行不得被提交翻译（它们不该消耗模型调用）。
        translation_prompts = [user for system, user in prompts
                               if "学术翻译专家" in system]
        assert translation_prompts, "正文段落必须产生翻译请求"
        submitted = [segment for user in translation_prompts
                     for segment in _submitted_segments(user)]
        assert submitted, "待翻译段落小节不得为空"
        for source in body:
            assert source in submitted, f"{source!r} 必须被提交翻译"
        for text in decorative:
            assert text not in submitted, f"装饰行不得被提交翻译：{text!r}"
        assert len(submitted) == len(body), \
            f"提交翻译的段落数应为正文数：{submitted!r}"

        # 非拉丁正文同样有资格进入翻译记忆（旧判定会把它拒之门外）。
        tm = core.load_tm()
        for source in body:
            assert core.tm_lookup(tm, source, target_lang="简体中文")[0] is not None, \
                f"{source!r} 必须能进入翻译记忆"
        for text in decorative:
            assert core.tm_lookup(tm, text, target_lang="简体中文")[0] is None
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call


def test_pipeline_translates_cyrillic_document_end_to_end(tmp_path):
    """整条管线（run_job_pipeline）对纯西里尔文档同样必须产出真译文。"""
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    core.call_llm = OfflineProvider()
    try:
        body = ["Заседание начнётся завтра.", "Структура полога была пересчитана."]
        path = _write_docx(tmp_path, body, name="cyrillic.docx")
        state = core.new_job_state(path.name)
        core.save_job_state("cyrillic-doc", state)

        result = core.run_job_pipeline(
            "cyrillic-doc", path.name, path.read_bytes(),
            provider="DeepSeek", api_key="k", model="m", target_lang="简体中文",
            auto_term=False, enable_report=False, translation_theory="",
            user_glossary=[], style_rules="", enable_review=True,
            enable_annotate=False, use_tm=True,
            delivery_config={"deliver_report": False})

        assert len(result["pairs"]) == len(body)
        for pair, source in zip(result["pairs"], body):
            assert pair["target"] != source, \
                f"{source!r} 必须被翻译（旧判定会让它原样通过）"
            assert pair["reviewed"] is True
        assert json.dumps(result["pairs"], ensure_ascii=False)
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call
