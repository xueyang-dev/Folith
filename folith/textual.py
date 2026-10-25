"""文本的 Unicode 语义判定（语言无关）。

只放"这一行是不是文字"这类**与具体语言无关**的判定，供核心层与各领域模块
共用：同一份判定如果在多处各写一遍，就会出现"这里认西里尔、那里不认"的漂移，
而漂移的方向恰好是"真实语言被判成装饰行、原样保留、标成已审校"——静默错误。
"""
from __future__ import annotations

import re
import unicodedata


def text_direction(text, language=None) -> str:
    """Use the declared language, then Unicode strong-character predominance.

    This is layout detection, not language identification or TM normalization.
    Unknown language names remain valid: their text supplies the direction.
    Counting strong characters avoids an English product-name prefix turning
    an otherwise Arabic paragraph into LTR. Digits and marks do not vote.
    """
    label = normalize_language(language)
    rtl_names = {"arabic", "modern standard arabic", "阿拉伯语", "现代标准阿拉伯语", "العربية", "hebrew", "希伯来语", "עברית",
                 "persian", "farsi", "波斯语", "فارسی", "urdu", "乌尔都语", "اردو",
                 "pashto", "普什图语", "پښتو", "uyghur", "维吾尔语", "ئۇيغۇرچە"}
    if label in rtl_names:
        return "rtl"
    # Language tags have an explicit direction even for an empty editor.
    if re.fullmatch(r"[a-z]{2,3}(?:[-_][a-z0-9]{2,8})*", label):
        parts = label.replace("_", "-").split("-")
        scripts = [part for part in parts[1:] if len(part) == 4 and part.isalpha()]
        if scripts:
            return "rtl" if scripts[0] in {"arab", "hebr", "thaa", "nkoo", "adlm",
                                               "rohg", "syrc", "samr", "mand"} else "ltr"
        return "rtl" if label.split("-")[0].split("_")[0] in {
            "ar", "ara", "he", "heb", "iw", "fa", "fas", "per", "ur", "urd",
            "ps", "pus", "ug", "uig", "yi", "yid", "sd", "snd", "dv", "div", "ckb", "syr",
        } else "ltr"
    rtl = ltr = 0
    for char in str(text or ""):
        direction = unicodedata.bidirectional(char)
        rtl += direction in {"R", "AL"}
        ltr += direction == "L"
    return "rtl" if rtl > ltr else "ltr"


def has_textual_content(text) -> bool:
    """段落是否含正文内容：任何 Unicode 字母（L*）或数字（N*）即算正文。

    刻意**不**枚举语言区间（拉丁 / 汉字 / 西里尔 / 谚文 / 阿拉伯 …）：按脚本
    列区间永远会漏掉下一个语言，而"这一行是不是文字"本身就是一个 Unicode
    类别问题，不是一个语言清单问题。纯标点、分隔符与装饰符号（Po / Pd / Sm /
    So / Cf …）不是正文，仍然按装饰行原样保留。
    """
    for character in str(text or ""):
        if unicodedata.category(character)[0] in ("L", "N"):
            return True
    return False


def normalize_language(value) -> str:
    """语言标识的比较键（NFKC + 去空白 + casefold）；无法解析时返回空串。

    只吸收"同一种语言写法不同"的排版差异（`Français` / `français` /
    `FrançAIS`），**不做语言识别**：证明不了的语言一律保持空，绝不猜——
    猜错的代价是把一条已审校的错译文复用到另一种语言的任务里。
    """
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip().casefold()


def language_tag(value: str) -> str:
    languages = {"简体中文": "zh-CN", "繁体中文": "zh-TW", "中文": "zh", "英语": "en", "英文": "en",
                 "日语": "ja", "韩语": "ko", "法语": "fr", "德语": "de", "西班牙语": "es", "阿拉伯语": "ar", "俄语": "ru",
                 "English": "en", "日本語": "ja", "한국어": "ko", "Deutsch": "de", "Français": "fr",
                 "Español": "es", "Русский": "ru", "Português": "pt", "Italiano": "it", "العربية": "ar",
                 "Arabic": "ar", "Modern Standard Arabic": "ar", "现代标准阿拉伯语": "ar",
                 "Nederlands": "nl", "Dutch": "nl", "荷兰语": "nl", "荷兰文": "nl", "nld": "nl",
                 "Polski": "pl", "Polish": "pl", "波兰语": "pl", "波兰文": "pl", "pol": "pl",
                 "Українська": "uk", "Ukrainian": "uk", "乌克兰语": "uk", "乌克兰文": "uk", "ukr": "uk",
                 "Tiếng Việt": "vi", "Vietnamese": "vi", "越南语": "vi", "越南文": "vi", "vie": "vi",
                 "Türkçe": "tr", "Turkish": "tr", "土耳其语": "tr", "tur": "tr",
                 "اردو": "ur", "Urdu": "ur", "乌尔都语": "ur", "urd": "ur",
                 "فارسی": "fa", "Persian": "fa", "Farsi": "fa", "波斯语": "fa", "fas": "fa",
                 "ئۇيغۇرچە": "ug", "Uyghur": "ug", "维吾尔语": "ug", "维文": "ug", "uig": "ug",
                 "עברית": "he", "Hebrew": "he", "希伯来语": "he", "heb": "he"}
    value = str(value or "").strip()
    labels = {normalize_language(key): tag for key, tag in languages.items()}
    if normalize_language(value) in labels:
        return labels[normalize_language(value)]
    return value if re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", value or "") else "und"
