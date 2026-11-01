"""Role-assignment warnings for ill-suited models (#258).

The classification itself lives in :mod:`folith.model_capability` so the engine
and the desktop client share one definition.  This module only turns a
(role, model) pair into a short, translated warning for the settings UI.

Saving is still allowed: these are warnings, not blocks.  A ``general`` model
(including every model we do not recognise) produces no warning — no evidence
means no claim.
"""
from __future__ import annotations

from folith.model_capability import CAPABILITY_CLASSES, classify_model  # noqa: F401

from . import i18n


def capability_warning(role_category: str, model_name: str) -> str:
    """Return a translated warning if *model_name* is ill-suited for the role.

    *role_category* is one of ``"vision"``, ``"translation"`` or ``"reviewer"``.
    An empty string means "no warning" (general or unrecognised model).
    """
    capability = classify_model(model_name)
    if capability == "general":
        return ""
    if role_category == "vision":
        if capability == "ocr_only":
            return i18n.tr(
                "此模型只输出纯文本，无法按视觉理解所需的结构化格式返回；"
                "请选择能看图并按格式回答的通用视觉语言模型。")
        return i18n.tr(
            "此模型是机器翻译专用模型，不能看图；"
            "请选择支持图像输入的通用视觉语言模型。")
    if capability == "ocr_only":
        if role_category == "reviewer":
            return i18n.tr("此模型是 OCR 专用模型，不能审校译文；请选择通用语言模型。")
        return i18n.tr("此模型是 OCR 专用模型，不能翻译；请选择通用语言模型。")
    if role_category == "reviewer":
        return i18n.tr("这是专用翻译模型，读不懂审校要求；建议选择通用模型。")
    return i18n.tr(
        "这是专用翻译模型，读不懂术语抽取、文档画像等需要按格式回答的要求；"
        "建议选择通用模型。")
