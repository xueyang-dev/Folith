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
