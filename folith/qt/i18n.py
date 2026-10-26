"""Qt 客户端的界面语言（桌面端 i18n）。

约定照搬 Web 端那份目录：**中文原文就是键**（`{"理论框架": "Theoretical framework"}`）。
这样两端可以共享同一批文案，而 Qt 端特有的措辞单独放在 `qt-<code>.json`。

查找顺序（永不抛异常，也永不显示空字符串）：

1. Qt 目录 `folith/resources/locales/qt-<code>.json`（桌面端自己的文案）；
2. Web 目录 `folith/resources/locales/<code>.json`（两边一字不差的那部分，直接复用）；
3. 中文原文本身。

设计取舍：

* 不做 `.ts`/`lupdate` 那一套：仓库已经有 1500 条文案的 JSON 目录，另起一套只会分叉；
* 语言在**启动时**确定（`FOLITH_UI_LANGUAGE` 环境变量优先，其次是设置里保存的值）；
  切换语言需要重建窗口，所以设置页明确写「重启后生效」，不假装能热切换；
* 缺条目**不是错误**：回退到中文原文，由 `scripts/qt_i18n_coverage.py` 如实报告覆盖率。
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from itertools import chain
from pathlib import Path

#: (代码, 下拉框里显示的名称)。简体中文是基准语言，也是回退语言。
LANGUAGES: tuple[tuple[str, str], ...] = (
    ("zh-CN", "简体中文"),
    ("en", "English (experimental)"),
    ("es", "Español (experimental)"),
    ("ru", "Русский (experimental)"),
    ("ar", "العربية (experimental)"),
    ("fr", "Français (experimental)"),
    ("de", "Deutsch (experimental)"),
)

#: 需要从右到左排版的语种。
RTL_LANGUAGES = frozenset({"ar", "he", "fa", "ur"})

ENV_VAR = "FOLITH_UI_LANGUAGE"
DEFAULT_LANGUAGE = "zh-CN"

LOCALES_DIR = Path(__file__).resolve().parents[1] / "resources" / "locales"

_state: dict[str, str] = {"code": DEFAULT_LANGUAGE}


def available_languages() -> tuple[tuple[str, str], ...]:
    return LANGUAGES


def language_label(code: str) -> str:
    return dict(LANGUAGES).get(code, code)


def normalize(code: str | None) -> str:
    """未知/空值回退到基准语言，避免因为一个拼错的环境变量把界面变成原文。"""
    value = str(code or "").strip()
    for known, _label in LANGUAGES:
        if value.lower() == known.lower():
            return known
    return DEFAULT_LANGUAGE


def language() -> str:
    return _state["code"]


def set_language(code: str | None) -> str:
    _state["code"] = normalize(code)
    return _state["code"]


def is_rtl(code: str | None = None) -> bool:
    return normalize(code or _state["code"]) in RTL_LANGUAGES


@lru_cache(maxsize=32)
def _catalog(kind: str, code: str) -> dict[str, str]:
    path = LOCALES_DIR / f"{kind}-{code}.json" if kind == "qt" else \
        LOCALES_DIR / f"{code}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def catalog_path(kind: str, code: str) -> Path:
    return (LOCALES_DIR / f"qt-{code}.json" if kind == "qt"
            else LOCALES_DIR / f"{code}.json")


def has_entry(code: str, text: str) -> bool:
    return text in _catalog("qt", code) or text in _catalog("web", code)


_TEMPLATE_RE_CACHE: dict[str, "re.Pattern[str]"] = {}


def _template_pattern(template: str):
    """把 `已冻结交付 v{v0}` 这样的模板编译成一条可匹配实际标签的正则。

    为什么需要它：状态标签由**核心**拼出来（`已冻结交付 v3`），Qt 端只在显示时
    才拿到整串；如果只做精确匹配，这类"带数字的状态"永远翻译不了。这里把模板的
    字面部分转义、占位符变成捕获组，就能在不改核心的前提下翻译整串。
    """
    import re as _re

    cached = _TEMPLATE_RE_CACHE.get(template)
    if cached is not None:
        return cached
    parts = _re.split(r"(\{v\d\})", template)
    pattern = ""
    names: list[str] = []
    for part in parts:
        match = _re.fullmatch(r"\{(v\d)\}", part)
        if match:
            names.append(match.group(1))
            pattern += "(.+?)"
        else:
            pattern += _re.escape(part)
    compiled = _re.compile("^" + pattern + "$")
    _TEMPLATE_RE_CACHE[template] = compiled
    return compiled


# 只有这些运行时模板的 v0 是另一个受控用途标签；段号、单元 id 和
# 其他模板中的文件名/正文/用户输入保持原值。
_RUNTIME_PURPOSE_TEMPLATES = frozenset({
    "已向模型发送请求（{v0}）",
    "已收到模型响应（{v0}）",
    "已收到模型响应（{v0}），仍有 {v1} 个请求进行中",
    "等待模型响应：{v0}（{v1} 个请求进行中）",
})


_RUNTIME_PURPOSE_LABEL_TEMPLATES = (
    "正在翻译第 {v0}–{v1} 段",
    "原文清理批次 #{v0}",
    "自动标注批次 #{v0}",
    "全文语义理解（{v0}）",
)


def tr_dynamic(value: str) -> str:
    """翻译**运行时**才出现的标签（核心产出的状态、语言名、段落状态）。

    先精确查目录；查不到再用带 `{v0}` 的模板去匹配（例如 `已冻结交付 v3`）；
    仍然不匹配就原样返回——宁可显示中文，也不能显示空白或半截。
    """
    text = str(value)
    code = _state["code"]
    if code == DEFAULT_LANGUAGE or not text:
        return text
    direct = (_catalog("qt", code).get(text) or _catalog("web", code).get(text))
    if direct:
        return direct
    import re as _re

    catalog = _catalog("qt", code)
    # 用途模板先于“{v0} 段”等宽泛模板匹配，防止只翻译到句尾。
    runtime_templates = (*_RUNTIME_PURPOSE_TEMPLATES, *_RUNTIME_PURPOSE_LABEL_TEMPLATES)
    candidates = chain(((template, catalog[template]) for template in runtime_templates
                        if template in catalog), catalog.items())
    for template, translated in candidates:
        if "{v" not in template:
            continue
        match = _template_pattern(template).match(text)
        if not match:
            continue
        names = _re.findall(r"\{(v\d)\}", template)
        try:
            values = dict(zip(names, match.groups()))
            if template in _RUNTIME_PURPOSE_TEMPLATES:
                values["v0"] = tr_dynamic(values["v0"])
            return translated.format(**values)
        except (KeyError, IndexError, ValueError):
            continue
    return text


def tr(text: str, **fmt) -> str:
    """把中文原文翻成当前语言；`fmt` 用于 `{name}` 占位符。"""
    source = str(text)
    code = _state["code"]
    if code == DEFAULT_LANGUAGE:
        translated = source
    else:
        translated = (_catalog("qt", code).get(source)
                      or _catalog("web", code).get(source)
                      or source)
    if not fmt:
        return translated
    try:
        return translated.format(**fmt)
    except (KeyError, IndexError, ValueError):
        # 翻译里占位符写坏了不能让界面炸掉：退回原文并保持可读。
        try:
            return source.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return source


def tr_n(text: str, count: int, **fmt) -> str:
    """Like :func:`tr`, but picks the singular form when ``count == 1``.

    A catalog may carry ``"<原文>#one"`` for languages that change the noun
    ("1 task", "1 tarea"); languages without it fall back to the normal entry.
    """
    if int(count) == 1 and _state["code"] != DEFAULT_LANGUAGE:
        singular = _catalog("qt", _state["code"]).get(f"{text}#one")
        if singular:
            try:
                return singular.format(**fmt) if fmt else singular
            except (KeyError, IndexError, ValueError):
                pass
    return tr(text, **fmt)
