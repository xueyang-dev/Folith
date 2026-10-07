"""Step 03 Deliverables Builder interaction regressions."""

from pathlib import Path
import re
import pytest
import core
from folith.ui.i18n import tr

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def _step3_app(*, research=False):
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.run()
    at.session_state["task_files"] = [{"name": "source.docx", "bytes": b"source"}]
    at.session_state["task_step"] = 3
    if research:
        at.session_state["translation_preset"] = "学术增强"
        at.session_state["strategy_config"] = {
            "auto_term": True,
            "use_tm": True,
            "enable_understanding": True,
            "enable_review": True,
            "strict_terminology_governance": True,
        }
    at.run()
    assert not at.exception, at.exception
    return at


def test_standard_delivery_is_recommended_and_counts_real_files():
    at = _step3_app()

    preset = next(item for item in at.selectbox if item.label == "交付方案")
    assert preset.value == "standard"
    assert any("标准交付" in option and "推荐" in option for option in preset.options)
    assert any("译文文件" in item.value and "已选 2" in item.value
               for item in at.markdown)
    assert any("语言资产" in item.value and "已选 1" in item.value
               for item in at.markdown)
    assert not any("研究产物" in item.value for item in at.markdown)
    assert any("将生成 3 个文件" in item.value and "DOCX ×2" in item.value
               and "XLSX ×1" in item.value for item in at.markdown)
    assert not at.toggle


def test_manual_delivery_change_marks_preset_and_complete_overwrites_it():
    at = _step3_app()

    next(item for item in at.checkbox if item.key == "deliver_pdf").check()
    at.run()
    assert at.session_state["delivery_preset"] == "standard"
    assert at.session_state["delivery_preset_modified"] is True
    assert any("将生成 4 个文件" in item.value for item in at.markdown)
    assert any("已修改" in item.value for item in at.markdown)

    next(item for item in at.selectbox if item.label == "交付方案").select("complete")
    at.run()
    config = at.session_state["output_config"]
    assert at.session_state["delivery_preset"] == "complete"
    assert at.session_state["delivery_preset_modified"] is False
    assert all(config[key] for key in (
        "deliver_plain_docx", "deliver_bilingual_docx", "deliver_pdf",
        "deliver_terms_xlsx", "deliver_tmx", "deliver_tbx"))
    assert not config["deliver_jsonl"]
    assert any("将生成 7 个文件" in item.value
               and "TMX ×1" in item.value and "TBX ×1" in item.value
               for item in at.markdown)


def test_annotation_is_a_disabled_child_of_bilingual_docx():
    at = _step3_app()
    next(item for item in at.checkbox if item.key == "output_annotate").check()
    at.run()
    assert at.session_state["output_config"]["enable_annotate"] is True

    next(item for item in at.checkbox if item.key == "deliver_bilingual_docx").uncheck()
    at.run()
    annotation = next(item for item in at.checkbox if item.key == "output_annotate")
    assert annotation.disabled is True
    assert annotation.value is False
    assert at.session_state["output_config"]["enable_annotate"] is False


def test_research_outputs_follow_step2_strategy_visibility():
    ordinary = _step3_app()
    assert not any(item.key == "output_report" for item in ordinary.checkbox)
    assert not any(item.label == "理论框架" for item in ordinary.selectbox)

    research = _step3_app(research=True)
    report = next(item for item in research.checkbox if item.key == "output_report")
    assert report.value is False
    assert not any(item.label == "理论框架" for item in research.selectbox)

    report.check()
    research.run()
    assert any(item.label == "理论框架" and item.value == "自动推荐（建议）"
               for item in research.selectbox)
    assert any("翻译实践报告" in item.label for item in research.checkbox)


def test_switching_delivery_preset_does_not_raise_keyerror_on_outputs():
    """切换交付方案与单独切换输出勾选（如 deliver_tmx）不抛出任何 KeyError。"""
    at = _step3_app()
    preset = next(item for item in at.selectbox if item.label == "交付方案")
    for choice in ("complete", "compact", "standard", "complete"):
        preset.select(choice)
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        tmx = next(item for item in at.checkbox if item.key == "deliver_tmx")
        if tmx.value:
            tmx.uncheck()
        else:
            tmx.check()
        at.run()
        assert not at.exception, [e.value for e in at.exception]


# ── §10 Selection-state consistency ──────────────────────────────────────────


def _count_selected(at, keys):
    """Return the number of truthy checkbox widgets for the given key list."""
    return sum(
        1 for cb in at.checkbox
        if cb.key in keys and cb.value
    )


def _footer_file_count(at):
    """Parse the footer file count from the delivery summary markdown."""
    for m in at.markdown:
        if "将生成" in m.value and "个文件" in m.value:
            import re
            match = re.search(r"将生成\s*(\d+)\s*个文件", m.value)
            if match:
                return int(match.group(1))
    return None


def test_select_unselect_artifact_all_views_agree():
    """checkbox → selected count → footer file count → output_config are consistent."""
    at = _step3_app()

    # baseline: standard preset → deliver_plain_docx + deliver_bilingual_docx + deliver_terms_xlsx
    config = at.session_state["output_config"]
    translation_keys = ["deliver_plain_docx", "deliver_bilingual_docx",
                        "deliver_pdf", "output_annotate"]
    asset_keys = ["deliver_terms_xlsx", "deliver_tmx", "deliver_tbx", "deliver_jsonl"]

    checked_translation = _count_selected(at, translation_keys)
    checked_assets = _count_selected(at, asset_keys)

    # selected counts in markdown headers must match widget state
    assert any(
        "译文文件" in m.value and f"已选 {checked_translation}" in m.value
        for m in at.markdown
    ), f"译文文件 header count should be {checked_translation}"
    assert any(
        "语言资产" in m.value and f"已选 {checked_assets}" in m.value
        for m in at.markdown
    ), f"语言资产 header count should be {checked_assets}"

    # footer file count matches output_config
    footer = _footer_file_count(at)
    assert footer == 3, f"standard preset should generate 3 files, got {footer}"
    assert config["deliver_plain_docx"] is True
    assert config["deliver_bilingual_docx"] is True
    assert config["deliver_terms_xlsx"] is True

    # uncheck deliver_plain_docx
    next(cb for cb in at.checkbox if cb.key == "deliver_plain_docx").uncheck()
    at.run()
    assert not at.exception

    config2 = at.session_state["output_config"]
    assert config2["deliver_plain_docx"] is False

    footer2 = _footer_file_count(at)
    assert footer2 == 2, f"after uncheck, footer should show 2 files, got {footer2}"

    new_checked = _count_selected(at, translation_keys)
    assert any(
        "译文文件" in m.value and f"已选 {new_checked}" in m.value
        for m in at.markdown
    ), "译文文件 header count should update after uncheck"


def test_preset_complete_selected_counts_correct():
    """完整交付 preset → checkbox states, selected counts, footer all correct."""
    at = _step3_app()
    next(item for item in at.selectbox if item.label == "交付方案").select("complete")
    at.run()
    assert not at.exception

    config = at.session_state["output_config"]
    assert config["deliver_plain_docx"] is True
    assert config["deliver_bilingual_docx"] is True
    assert config["deliver_pdf"] is True
    assert config["deliver_original_pdf"] is True
    assert config["deliver_terms_xlsx"] is True
    assert config["deliver_tmx"] is True
    assert config["deliver_tbx"] is True

    # checkbox values match config
    for key in ("deliver_plain_docx", "deliver_bilingual_docx", "deliver_pdf",
                 "deliver_original_pdf", "deliver_terms_xlsx", "deliver_tmx",
                 "deliver_tbx"):
        cb = next((c for c in at.checkbox if c.key == key), None)
        assert cb is not None, f"checkbox {key} not found"
        assert cb.value is True, f"checkbox {key} should be checked for complete preset"

    footer = _footer_file_count(at)
    assert footer == 7, f"complete preset should generate 7 files, got {footer}"

    assert any("译文文件" in m.value and "已选 4" in m.value for m in at.markdown), \
        "complete preset: 译文文件 should show 已选 4"
    assert any("语言资产" in m.value and "已选 3" in m.value for m in at.markdown), \
        "complete preset: 语言资产 should show 已选 3"


def test_preset_compact_selected_counts_correct():
    """精简交付 preset → only deliver_plain_docx checked, counts correct."""
    at = _step3_app()
    next(item for item in at.selectbox if item.label == "交付方案").select("compact")
    at.run()
    assert not at.exception

    config = at.session_state["output_config"]
    assert config["deliver_plain_docx"] is True
    assert config["deliver_bilingual_docx"] is False
    assert config["deliver_terms_xlsx"] is False

    footer = _footer_file_count(at)
    assert footer == 1, f"compact preset should generate 1 file, got {footer}"

    assert any("译文文件" in m.value and "已选 1" in m.value for m in at.markdown), \
        "compact preset: 译文文件 should show 已选 1"
    assert any("语言资产" in m.value and "已选 0" in m.value for m in at.markdown), \
        "compact preset: 语言资产 should show 已选 0"


def test_dual_panel_groups_both_present():
    """Both 译文文件 and 语言资产 group headers appear on step 3."""
    at = _step3_app()
    assert any("译文文件" in m.value and "已选" in m.value for m in at.markdown), \
        "译文文件 panel header must appear"
    assert any("语言资产" in m.value and "已选" in m.value for m in at.markdown), \
        "语言资产 panel header must appear"


def test_plain_docx_and_pdf_are_visually_disambiguated():
    """The two 纯译文 rows are shown with distinct format labels in the HTML."""
    at = _step3_app()
    # Both rows have their format badge rendered; the format strings differ
    # (DOCX vs PDF) so users can tell them apart.
    html_values = " ".join(m.value for m in at.markdown)
    assert "DOCX" in html_values and "PDF" in html_values, \
        "Both DOCX and PDF format badges must be present"
    # Both 纯译文 rows show their disambiguated title in the markdown copy
    assert html_values.count("纯译文") >= 2, \
        "Both 纯译文 rows must render their title in markdown"


@pytest.mark.parametrize('locale', ['en', 'es', 'ru', 'ar'])
@pytest.mark.parametrize('research', [False, True])
def test_delivery_cards_and_confirmation_localize_visible_copy(tmp_path, monkeypatch, locale, research):
    monkeypatch.setattr(core, 'OUTPUT_DIR', tmp_path)
    at = _step3_app(research=research)
    at.session_state['ui_locale'] = locale
    at.run()
    assert not at.exception
    assert at.selectbox(key='delivery_preset').value == 'standard'
    assert tr('标准交付', locale=locale) in at.selectbox(key='delivery_preset').options[1]
    cards = [item.value for item in at.markdown if any(f'class="{marker}' in item.value for marker in
             ('tp-delivery-row-copy', 'tp-delivery-group-head', 'tp-delivery-preset-help', 'tp-delivery-summary'))]
    assert cards
    untranslated = [value for value in cards if re.search('[\u4e00-\u9fff]', value)]
    assert not untranslated, untranslated
    # A single-file plan must use a whole translated label, not "1 files".
    at.checkbox(key='deliver_bilingual_docx').uncheck().run()
    at.checkbox(key='deliver_terms_xlsx').uncheck().run()
    assert any(tr('将生成 {v0} 个文件', locale=locale, v0=1) in item.value for item in at.markdown)
    at.checkbox(key='deliver_plain_docx').uncheck().run()
    assert any(tr('将生成 {v0} 个文件', locale=locale, v0=0) in item.value for item in at.markdown)
    at.checkbox(key='deliver_plain_docx').check().run()
    # User data matching a catalog key must remain unchanged and HTML-safe.
    at.session_state['task_files'] = [{'name': '原稿<script>.docx', 'bytes': b'source'}]
    at.session_state['task_glossary_name'] = '理论框架'
    at.session_state['style_template'] = '纸面模式'
    at.button(key='next_to_4').click().run()
    assert not at.exception
    summary = next(item.value for item in at.markdown if 'class="tp-confirm-stack"' in item.value)
    assert '原稿&lt;script&gt;.docx' in summary and '<script>' not in summary
    assert '理论框架' in summary and '纸面模式' in summary
    assert tr('仅含译文文本', locale=locale) in summary
    assert tr('按自然句', locale=locale) in summary
    assert tr('基础检查', locale=locale) in summary
    runtime = next(item.value for item in at.markdown if 'class="tp-confirm-card tp-runtime-card"' in item.value)
    assert not re.search('[\u4e00-\u9fff]', runtime)

