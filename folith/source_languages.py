"""原文语言目录：用户在「准备」页选择（或由智能画像识别）的原文语言。

每一项是 (保存值, 英文名, Tesseract 代码)：

* **保存值**写进任务的 ``source_lang``，用语言自己的写法（与目标语言列表一致）；
* **英文名**写进视觉模型的读页提示词。共用一套字母的语言（阿拉伯语 / 乌尔都语 /
  波斯语 / 维吾尔语）只看字形很容易互相混淆，明确告诉模型"这一页是哪种语言"
  能减少把一种语言的拼写转成另一种的错误；
* **Tesseract 代码**给本机 OCR 选语言包（本机没有该语言包时由 OCR 层明确报错）。

空值表示"自动识别"：不给模型任何语言提示，行为与以前一致。
"""
from __future__ import annotations

from typing import Optional, Tuple

from folith.textual import normalize_language

SOURCE_LANGUAGES: Tuple[Tuple[str, str, str], ...] = (
    ("English", "English", "eng"),
    ("简体中文", "Simplified Chinese", "chi_sim"),
    ("繁体中文", "Traditional Chinese", "chi_tra"),
    ("日本語", "Japanese", "jpn"),
    ("한국어", "Korean", "kor"),
    ("Deutsch", "German", "deu"),
    ("Français", "French", "fra"),
    ("Español", "Spanish", "spa"),
    ("Italiano", "Italian", "ita"),
    ("Português", "Portuguese", "por"),
    ("Русский", "Russian", "rus"),
    ("Türkçe", "Turkish", "tur"),
    ("العربية", "Arabic", "ara"),
    ("اردو", "Urdu", "urd"),
    ("فارسی", "Persian", "fas"),
    ("ئۇيغۇرچە", "Uyghur", "uig"),
    ("עברית", "Hebrew", "heb"),
)

#: 识别结果里常见的别名（中文名 / 英文名 / 代码）→ 保存值。
_ALIASES = {
    "英语": "English", "英文": "English", "en": "English",
    "中文": "简体中文", "汉语": "简体中文", "简体中文": "简体中文", "zh": "简体中文",
    "zh-cn": "简体中文", "chinese": "简体中文",
    "繁體中文": "繁体中文", "zh-tw": "繁体中文",
    "日语": "日本語", "日文": "日本語", "ja": "日本語",
    "韩语": "한국어", "韩文": "한국어", "ko": "한국어",
    "德语": "Deutsch", "de": "Deutsch", "法语": "Français", "fr": "Français",
    "西班牙语": "Español", "es": "Español", "意大利语": "Italiano", "it": "Italiano",
    "葡萄牙语": "Português", "pt": "Português", "俄语": "Русский", "ru": "Русский",
    "土耳其语": "Türkçe", "tr": "Türkçe",
    "阿拉伯语": "العربية", "ar": "العربية", "现代标准阿拉伯语": "العربية",
    "modern standard arabic": "العربية",
    "乌尔都语": "اردو", "ur": "اردو",
    "波斯语": "فارسی", "fa": "فارسی", "farsi": "فارسی",
    "维吾尔语": "ئۇيغۇرچە", "ug": "ئۇيغۇرچە",
    "希伯来语": "עברית", "he": "עברית",
}


def _index() -> dict:
    table = {}
    for value, english, code in SOURCE_LANGUAGES:
        for key in (value, english, code):
            table[normalize_language(key)] = value
    for alias, value in _ALIASES.items():
        table[normalize_language(alias)] = value
    return table


def match(text) -> str:
    """把识别结果 / 旧记录里的语言名对到目录里的保存值；对不上返回空串（不猜）。"""
    return _index().get(normalize_language(text), "")


def english_name(value) -> str:
    """保存值 → 写进读页提示词的英文名；不在目录里返回空串（不给模型猜来的提示）。"""
    key = match(value)
    for saved, english, _code in SOURCE_LANGUAGES:
        if saved == key:
            return english
    return ""


def ocr_code(value) -> Optional[str]:
    """保存值 → Tesseract 语言代码；不在目录里返回 ``None``。"""
    key = match(value)
    for saved, _english, code in SOURCE_LANGUAGES:
        if saved == key:
            return code
    return None


def display_name(value) -> str:
    """界面显示：语言自称 + 英文名（"اردو · Urdu"），让不认识这种文字的人也能认出来。"""
    key = match(value)
    for saved, english, _code in SOURCE_LANGUAGES:
        if saved == key:
            return saved if saved == english else f"{saved} · {english}"
    return str(value or "").strip()
