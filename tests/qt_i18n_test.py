"""桌面端界面语言（`folith.qt.i18n`）的用例。

这一层替换的是"整客户端写死中文"，所以用例钉住的是：

* 基准语言（简体中文）下 `tr()` 必须原样返回，**老用例的文本断言不因此变化**；
* 其他语言读 Qt 目录（`qt-<code>.json`），缺失时回退到 Web 目录，再缺就回退原文；
* 占位符 `{v0}` 正常格式化，翻译里写坏占位符也不能让界面炸掉；
* 五个语言的目录**键集完全一致**（否则会悄悄出现"这门语言少一句"）；
* 阿拉伯语是 RTL，窗口要整体镜像。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from folith.qt import i18n  # noqa: E402

LANGUAGES = [code for code, _label in i18n.available_languages()]


@pytest.fixture(autouse=True)
def restore_language():
    yield
    i18n.set_language("zh-CN")
    i18n._catalog.cache_clear()


def test_default_language_returns_the_source_text():
    i18n.set_language("zh-CN")
    assert i18n.tr("导入文档…") == "导入文档…"
    assert i18n.tr("已导入 {v0} 个任务", v0=3) == "已导入 3 个任务"


def test_unknown_language_falls_back_to_chinese():
    assert i18n.normalize("klingon") == "zh-CN"
    assert i18n.normalize("") == "zh-CN"
    assert i18n.normalize("EN") == "en"
    assert i18n.normalize("FR") == "fr"
    assert i18n.normalize("DE") == "de"


def test_translation_uses_the_qt_catalog(tmp_path, monkeypatch):
    (tmp_path / "qt-en.json").write_text(
        json.dumps({"导入文档…": "Import documents…"}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(i18n, "LOCALES_DIR", tmp_path)
    i18n._catalog.cache_clear()
    i18n.set_language("en")
    assert i18n.tr("导入文档…") == "Import documents…"
    # 目录里没有的条目回退原文，而不是显示空字符串
    assert i18n.tr("这个键不存在") == "这个键不存在"


def test_translation_keeps_placeholders(tmp_path, monkeypatch):
    (tmp_path / "qt-en.json").write_text(
        json.dumps({"已导入 {v0} 个任务": "Imported {v0} task(s)"},
                   ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(i18n, "LOCALES_DIR", tmp_path)
    i18n._catalog.cache_clear()
    i18n.set_language("en")
    assert i18n.tr("已导入 {v0} 个任务", v0=7) == "Imported 7 task(s)"


def test_broken_translation_placeholder_does_not_crash(tmp_path, monkeypatch):
    (tmp_path / "qt-en.json").write_text(
        json.dumps({"已导入 {v0} 个任务": "Imported {wrong} task(s)"},
                   ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(i18n, "LOCALES_DIR", tmp_path)
    i18n._catalog.cache_clear()
    i18n.set_language("en")
    # 翻坏了的占位符：退回原文模板并照常填值，界面不能炸
    assert i18n.tr("已导入 {v0} 个任务", v0=7) == "已导入 7 个任务"


def test_arabic_is_rtl_and_others_are_not():
    assert i18n.is_rtl("ar") is True
    assert i18n.is_rtl("zh-CN") is False
    assert i18n.is_rtl("en") is False
    assert i18n.is_rtl("fr") is False
    assert i18n.is_rtl("de") is False
    assert i18n.is_rtl(None) is False


def test_french_and_german_runtime_catalogs_and_pluralization():
    i18n.set_language("fr")
    assert i18n.tr("导入文档…") == "Importer un document…"
    assert i18n.tr_n("{v0} 个任务", 1, v0=1) == "1 tâche"
    assert i18n.tr_n("{v0} 个任务", 3, v0=3) == "3 tâches"

    i18n.set_language("de")
    assert i18n.tr("导入文档…") == "Dokument importieren…"
    assert i18n.tr_n("{v0} 个任务", 1, v0=1) == "1 Aufgabe"
    assert i18n.tr_n("{v0} 个任务", 3, v0=3) == "3 Aufgaben"


def test_every_locale_catalog_has_the_same_keys():
    directories = {code: i18n.catalog_path("qt", code) for code in LANGUAGES}
    for code, path in directories.items():
        assert path.is_file(), f"缺少 Qt 语言目录：{path.name}"
    base = json.loads(directories["zh-CN"].read_text(encoding="utf-8"))
    assert len(base) >= 200, f"Qt 目录太小（{len(base)} 条），疑似没生成"
    for code, path in directories.items():
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = set(base) - set(data)
        # "<原文>#one" 是单数形式（i18n.tr_n），只有需要它的语言才有。
        extra = {key for key in set(data) - set(base) if not key.endswith("#one")}
        assert not missing, f"{code} 缺 {len(missing)} 条：{sorted(missing)[:3]}"
        assert not extra, f"{code} 多 {len(extra)} 条：{sorted(extra)[:3]}"


#: 语言自称在所有界面里都保持原样，是"正确的不翻译"。
ENDONYMS = {"简体中文", "繁體中文", "日本語", "한국어"}


def test_every_locale_translates_all_strings():
    """非中文语言不允许"值是中文原文"——那等于没翻译（语言自称除外）。"""
    base = json.loads(i18n.catalog_path("qt", "zh-CN").read_text(encoding="utf-8"))
    for code, _label in i18n.available_languages():
        if code == "zh-CN":
            continue
        data = json.loads(i18n.catalog_path("qt", code).read_text(encoding="utf-8"))
        untranslated = [key for key, value in data.items()
                        if value == key and key not in ENDONYMS]
        assert not untranslated, \
            f"{code} 有 {len(untranslated)} 条未翻译：{untranslated[:3]}"


def test_window_has_no_untranslated_chinese_literals():
    """FND-5：``window.py`` 里用户可见的中文必须经过 ``i18n.tr`` / ``tr_dynamic``。

    允许清单里是有意保留中文的位置（写进本机日志的文本、自动化运行的排除原因、
    作为数据传给适配层的任务名兜底），不是界面文案。
    """
    import ast
    import re

    path = Path(__file__).resolve().parents[1] / "folith" / "qt" / "window.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cjk = re.compile(r"[一-鿿]")
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    allowed = {"（内容含可能的凭据，已隐去；完整原因见界面提示）", "自动化运行排除", "当前任务"}

    def translated(node) -> bool:
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name in ("tr", "tr_dynamic"):
                    return True
            if isinstance(node, ast.FunctionDef):
                return False
        return False

    offenders = [
        f"{node.lineno}: {node.value[:40]!r}" for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and cjk.search(node.value) and node.value not in allowed
        and not isinstance(parents.get(node), ast.Expr)  # docstrings
        and not translated(node)
    ]
    assert not offenders, "window.py 里有未走 i18n.tr 的中文文案：\n" + "\n".join(offenders)


@pytest.mark.parametrize("code", LANGUAGES)
def test_runtime_request_purposes_translate_through_the_activity_view(code):
    import re
    from folith import runtime_events_view

    i18n.set_language(code)
    messages = [
        "已向模型发送请求（正在翻译第 1–3 段）",
        "已收到模型响应（自动标注批次 #2），仍有 1 个请求进行中",
        "已收到模型响应（全文语义理解（unit-7））",
        "已收到模型响应（文档画像）",
        "已收到模型响应（原文清理批次 #2）",
        "已收到模型响应（提取术语与知识）",
        "已收到模型响应（抽取全文核心术语）",
        "已收到模型响应（辅助分析）",
    ]
    for message in messages:
        event = "llm_request_started" if message.startswith("已向") else "llm_response_received"
        row = runtime_events_view.activity_rows([{"event": event, "message": message}])[0]
        translated = i18n.tr_dynamic(row["label"])
        if code == "zh-CN":
            assert translated == message
        else:
            assert not re.search(r"[一-鿿]", translated), (code, translated)
    if code == "en":
        assert i18n.tr_dynamic(messages[0]) == "Model request sent (Translating segments 1–3)"
        assert i18n.tr_dynamic(messages[1]) == "Model response received (Annotation batch #2); 1 request(s) still in progress"
        assert i18n.tr_dynamic(messages[2]) == "Model response received (Document understanding (unit-7))"
        assert i18n.tr_dynamic("等待模型响应：自动标注批次 #2（1 个请求进行中）") == "Waiting for the model: Annotation batch #2 (1 request(s) in progress)"
        # Other dynamic labels must keep user/document values untouched.
        assert i18n.tr_dynamic("全文语义理解（中文标题）") == "Document understanding (中文标题)"


# ---------------------------------------------------------------------------
# F16 词表（docs/ui-overhaul/12-glossary.md）
# ---------------------------------------------------------------------------

def _locale_checker():
    """`scripts/check_ui_locales.py` 是脚本不是包：按路径加载，和覆盖率脚本同一种做法。"""
    import importlib.util

    path = ROOT / "scripts" / "check_ui_locales.py"
    spec = importlib.util.spec_from_file_location("check_ui_locales", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_glossary_rejects_retired_words_in_chinese_ui_text():
    checker = _locale_checker()
    retired = [
        "审核候选",                                   # 候选关卡写成「审核」
        "待审核",                                     # 候选状态写成「待审核」
        "正在记录审核决定…",
        "审查候选术语，锁定已确认的译名",              # setup_panel 的第三种动词
        "审定并锁定术语",
        "严格术语治理：还有 {v0} 个候选术语未确认。",   # 候选状态写成「未确认」
        "已确认的术语会自动用于新任务（{v0}）",         # 术语状态写成「已确认」
        "{v0} 条术语，{v1} 条已确认",
        "已确认，新任务自动使用",
        "冻结并继续翻译",                              # 术语写成「冻结」
        "术语版本 v{v0} 已冻结；仍可人工修改",
        "冻结译法",
        "未用冻结译法",
        "解除冻结",
        "点击「导入文档」选择 PDF 或 DOCX，即可开始翻译。",  # 格式清单只写两种
    ]
    for text in retired:
        assert checker.zh_glossary_hits(text), f"词表没有拦住废弃说法：{text!r}"


def test_glossary_keeps_the_canonical_words_and_the_delivery_freeze():
    checker = _locale_checker()
    canonical = [
        "审校",
        "待审校",
        "已审校",
        "待确认",
        "候选",
        "暂定",
        "已锁定",
        "锁定全部候选",
        "确认候选",
        "正在记录确认决定…",
        "已锁定的术语会自动用于新任务（{v0}）",
        "已锁定，新任务自动使用",
        "点击「导入文档」选择 PDF、DOCX、EPUB、TXT 或 Markdown，即可开始翻译。",
        # 「已确认」保留给人工决定 / 风格规则 / 作者说明，不是术语状态
        "已确认问题解决。该决定已记录。",
        # 裸「已确认 / 未确认」是 Word/作者最终复核的人工确认状态
        # （folith/finalization.py 的 final_qa_label），按词表 1.2 保留
        "已确认",
        "未确认",
        "作者视觉复核尚未确认",
        "原文已确认，正在重新翻译第 {v0} 段…",
        "可提升：锁定术语 {v0} 条 · 已确认风格 {v1} 条",
        # 交付快照保留「冻结」（词表 1.3）
        "冻结交付",
        "审校完成后冻结交付",
        "已冻结交付 v{v0}",
        "工作版本已偏离冻结交付 v{v0}",
        "冻结交付（生成 v{v0}）",
        "已冻结",
        "已冻结 v{v0}",
    ]
    for text in canonical:
        assert not checker.zh_glossary_hits(text), f"词表误伤标准说法：{text!r}"


def test_glossary_splits_the_review_and_confirm_families_in_every_catalog():
    checker = _locale_checker()
    # 候选关卡若沿用「审校」的词，两件事在译文里就分不开（en/es/ru/de 都犯过）
    for code in ("en", "es", "ru", "ar", "fr", "de"):
        mixed = checker.catalog_glossary_violations(
            code, {"待审核": {"en": "To review", "es": "Por revisar", "ru": "Ожидает проверки",
                              "ar": "بانتظار المراجعة", "fr": "À réviser", "de": "Zur Prüfung"}[code]})
        assert mixed, f"{code}: 候选状态仍用「审校」族词却没被拦住"
        clean = checker.catalog_glossary_violations(
            code, {"待确认": {"en": "To confirm", "es": "Por confirmar", "ru": "Ожидает подтверждения",
                              "ar": "بانتظار التأكيد", "fr": "À confirmer", "de": "Zu bestätigen"}[code]})
        assert not clean, f"{code}: 标准说法被误伤：{clean}"
        # 术语状态不得继续沿用交付快照的「冻结」
        frozen = checker.catalog_glossary_violations(
            code, {"术语已冻结": {"en": "Terms frozen", "es": "Términos congelados",
                                  "ru": "Термины заморожены", "ar": "تم تجميد المصطلحات",
                                  "fr": "Termes figés", "de": "Begriffe eingefroren"}[code]})
        assert frozen, f"{code}: 术语仍用「冻结」族词却没被拦住"


def test_glossary_gate_is_green():
    """`scripts/check_ui_locales.py` 就是门禁：词表基线与目录必须一致。"""
    checker = _locale_checker()
    assert checker.check() == 0


def test_glossary_document_covers_every_concept():
    """词表文档必须逐个概念给出中英西俄对照，不能只有一张 TODO 表。"""
    text = (ROOT / "docs" / "ui-overhaul" / "12-glossary.md").read_text(encoding="utf-8")
    for phrase in ("审校", "待确认", "已锁定", "解除锁定", "冻结交付",
                   "PDF、DOCX、EPUB、TXT 或 Markdown", "MTI", "待改调用点清单"):
        assert phrase in text, f"词表缺条目：{phrase}"


def test_new_mti_explanation_is_translated_in_every_qt_catalog():
    """MTI 不重命名，但解释文案必须进 7 个目录，否则非中文界面会掉回中文。"""
    keys = (
        "MTI（翻译硕士）",
        "MTI 是翻译硕士（Master of Translation and Interpreting）专业学位；"
        "打开后，学术写作、案例与合规页面才会出现。",
    )
    for key in keys:
        for code, _label in i18n.available_languages():
            data = json.loads(i18n.catalog_path("qt", code).read_text(encoding="utf-8"))
            assert key in data, f"{code}: 目录里没有 MTI 的解释词条 {key!r}"
            if code == "zh-CN":
                assert data[key] == key
            else:
                assert data[key] != key, f"{code}: MTI 词条没有翻译：{key!r}"
