"""Tests for #258: model capability classification and role warnings.

Covers:
- folith.qt.model_capability.classify_model
- capability_warning for each role/capability combination
- ConnectionLibrary shows warning for ill-suited models
- ConnectionLibrary does NOT show warning for normal models
- EngineDialog scan section is in the models page (no vision tab)
- show_page("vision") redirects to "models"
- Runtime error messages for OCR-only responses
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

import core  # noqa: E402
from folith.qt import adapter, dialogs  # noqa: E402
from folith.qt.model_capability import classify_model, capability_warning  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(core, "OUTPUT_DIR", data)
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data))
    monkeypatch.setattr(adapter.core, "load_ocr_online_config", lambda *a, **k: {})
    return data


class _SyncRunner:
    def submit(self, fn, *args, on_done=None, on_error=None, label="", **kwargs):
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if on_error:
                on_error(str(exc), "")
            return None
        if on_done:
            on_done(result)
        return None


def _config(**extra):
    base = dict(provider="自定义中转站", model="m1", api_key="k1",
                base_url="http://127.0.0.1:9/v1", target_lang="简体中文")
    base.update(extra)
    return adapter.EngineConfig(**base)


# ---------------------------------------------------------------- classify_model ---

class TestClassifyModel:
    """Conservative name matching — no false positives on general-purpose models."""

    # OCR-only family
    @pytest.mark.parametrize("name", [
        "PaddlePaddle/PaddleOCR-VL-1.5",
        "paddleocr-vl-1.5",
        "stepfun-ai/GOT-OCR2_0",
        "got-ocr",
        "olmOCR-7B",
        "allenai/olmocr-7b",
        "my-ocr-v2",
        "vendor/model-ocr",
    ])
    def test_ocr_only(self, name):
        assert classify_model(name) == "ocr_only", f"Expected ocr_only for {name!r}"

    # Translation-only family
    @pytest.mark.parametrize("name", [
        "tencent/Hunyuan-MT-7B",
        "Helsinki-NLP/opus-mt-en-zh",
        "facebook/m2m100_1.2B",
        "facebook/seamless-m4t-medium",
        "facebook/seamlessm4t-medium",
        "nllb-200-distilled-600M",
    ])
    def test_translation_only(self, name):
        assert classify_model(name) == "translation_only", f"Expected translation_only for {name!r}"

    # General-purpose: must NOT be mis-classified (the critical negatives)
    @pytest.mark.parametrize("name", [
        "qwen2.5-vl",
        "Qwen3-VL",
        "glm-4.5v",
        "gpt-5",
        "gpt-4o",
        "deepseek-v4.1-flash",
        "dots3-note-prev",
        "claude-opus-4",
        "gemini-2.0-flash",
        "llama-3.3-70b",
        "mistral-large",
        "phi-4-vision",
    ])
    def test_general(self, name):
        assert classify_model(name) == "general", f"Expected general for {name!r}"

    def test_empty_and_none(self):
        assert classify_model("") == "general"
        assert classify_model(None) == "general"  # type: ignore[arg-type]


# ---------------------------------------------------------------- capability_warning ---

class TestCapabilityWarning:
    def test_ocr_only_in_vision_role_warns(self):
        w = capability_warning("vision", "PaddlePaddle/PaddleOCR-VL-1.5")
        assert w  # must produce a warning
        assert "OCR" in w or "纯文本" in w or "结构" in w

    def test_mt_only_in_vision_role_warns(self):
        w = capability_warning("vision", "tencent/Hunyuan-MT-7B")
        assert w
        assert "翻译" in w or "图像" in w

    def test_ocr_only_in_translation_role_warns(self):
        w = capability_warning("translation", "PaddlePaddle/PaddleOCR-VL-1.5")
        assert w
        assert "翻译" in w or "OCR" in w

    def test_ocr_only_in_reviewer_role_warns(self):
        w = capability_warning("reviewer", "PaddlePaddle/PaddleOCR-VL-1.5")
        assert w

    def test_general_model_no_warning(self):
        for role in ("vision", "translation", "reviewer"):
            for name in ("qwen2.5-vl", "gpt-5", "deepseek-v4.1-flash"):
                assert capability_warning(role, name) == "", \
                    f"Unexpected warning for {name!r} in {role!r}"

    def test_mt_in_translation_warns(self):
        # MT-only models warn for translation: term extraction, profiling, etc. need structured output
        w = capability_warning("translation", "tencent/Hunyuan-MT-7B")
        assert w, "MT-only model in translation role should produce a warning"
        assert "翻译" in w or "格式" in w or "术语" in w

    def test_mt_in_reviewer_warns(self):
        # MT-only models warn for reviewer role too
        w = capability_warning("reviewer", "tencent/Hunyuan-MT-7B")
        assert w, "MT-only model in reviewer role should produce a warning"
        assert "翻译" in w or "格式" in w

    def test_empty_model_no_warning(self):
        for role in ("vision", "translation", "reviewer"):
            assert capability_warning(role, "") == ""


# ---------------------------------------------------------------- ConnectionLibrary warnings ---

class TestConnectionLibraryWarnings:
    def _make_dialog_with_vision(self, model_name):
        profile = {
            "id": "c1", "name": "Test", "provider": "OpenAI", "custom": False,
            "model": model_name, "models": [model_name],
            "api_key": "k", "base_url": "", "protocol": "openai_compat",
        }
        config = _config(
            connections=[profile],
            connection_roles={"translation": "c1", "vision": "c1"},
            connection_models={"vision": model_name},
        )
        return dialogs.EngineDialog(config, _SyncRunner())

    def test_no_red_warning_for_ocr_only_vision_model(self, app, isolated):
        """OCR-only vision models no longer show a red warning in ConnectionLibrary.

        The 0.8.0 redesign removed capability_warnings entirely for the vision role.
        Channel A (视觉模型) filters to VLM-capable models; OCR-only models trigger
        a config migration to channel B in EngineConfig.load() instead.
        """
        dialog = self._make_dialog_with_vision("PaddlePaddle/PaddleOCR-VL-1.5")
        lib = dialog.connection_library
        # capability_warnings no longer exists; capability_tags has translation/reviewer only
        assert not hasattr(lib, "capability_warnings"), \
            "capability_warnings was removed in 0.8.0; use capability_tags instead"
        assert "vision" not in lib.capability_tags, \
            "vision role must not appear in capability_tags (no inline tag for vision)"
        dialog.close()

    def test_no_warning_for_general_vision_model(self, app, isolated):
        dialog = self._make_dialog_with_vision("qwen2.5-vl")
        lib = dialog.connection_library
        # capability_warnings was removed in 0.8.0; no warning widget exists at all
        assert not hasattr(lib, "capability_warnings"), \
            "capability_warnings was removed in 0.8.0; use capability_tags instead"
        assert "vision" not in lib.capability_tags, \
            "General model in vision role must not show any capability tag"
        dialog.close()


# ---------------------------------------------------------------- Page structure ---

class TestSettingsPageStructure:
    def test_models_page_contains_scan_controls(self, app, isolated):
        """読取扫描页块在「模型」页里，不是单独一页。"""
        dialog = dialogs.EngineDialog(_config(), _SyncRunner())
        models_inner = dialog.pages["models"].widget()
        assert models_inner.isAncestorOf(dialog.ocr_backend_box), \
            "ocr_backend_box must be inside the models page"
        assert models_inner.isAncestorOf(dialog.vision_option), \
            "vision_option card must be inside the models page"
        assert models_inner.isAncestorOf(dialog.ocr_mode_picker), \
            "ocr_mode_picker must be inside the models page"
        dialog.close()

    def test_no_separate_vision_tab(self, app, isolated):
        dialog = dialogs.EngineDialog(_config(), _SyncRunner())
        assert "vision" not in dialog.pages
        assert "vision" not in dialog.tab_buttons
        dialog.close()

    def test_show_page_vision_redirects_to_models(self, app, isolated):
        dialog = dialogs.EngineDialog(_config(), _SyncRunner())
        dialog.show_page("vision")
        assert dialog.current_page() == "models"
        dialog.close()

    def test_vision_connection_box_in_models_page(self, app, isolated):
        dialog = dialogs.EngineDialog(_config(), _SyncRunner())
        models_inner = dialog.pages["models"].widget()
        assert models_inner.isAncestorOf(dialog.vision_connection_box)
        dialog.close()


# ---------------------------------------------------------------- Runtime messages ---

class TestOCROnlineRuntimeMessages:
    def test_plain_text_truncation_gives_ocr_hint(self):
        from folith.ocr_online import _truncation_message
        # Plain text (no JSON) → OCR-specific hint
        msg = _truncation_message("第一行\n第二行\n第三行")
        assert "OCR" in msg or "纯文本" in msg or "结构" in msg
        assert "PaddleOCR" in msg or "视觉语言" in msg or "格式" in msg

    def test_json_truncation_gives_standard_hint(self):
        from folith.ocr_online import _truncation_message
        # JSON-like response → standard truncation hint
        msg = _truncation_message('{"b": [{"t": "paragraph"')
        assert "输出上限" in msg or "截断" in msg or "truncated" in msg.lower()

    def test_empty_truncation_gives_standard_hint(self):
        from folith.ocr_online import _truncation_message
        msg = _truncation_message("")
        assert "输出上限" in msg or "截断" in msg

    def test_invalid_semantic_json_gives_ocr_hint(self):
        from folith.ocr_online import _parse_failure_message
        msg = _parse_failure_message("invalid_semantic_json")
        assert "OCR" in msg or "格式" in msg or "结构" in msg
        # Must not be the generic "请提高输出上限" message
        assert "输出上限" not in msg

    def test_page_failure_line_keeps_whole_reason_and_advice(self):
        """The per-page event cuts at a clause, never mid-word (it used to end in 「是 O」)."""
        from folith.ocr_online import _parse_failure_message, _truncation_message
        for message in (_truncation_message("附章 二千年の定説を覆す"),
                        _parse_failure_message("invalid_semantic_json")):
            line = core._scan_page_event_text({"page": 9, "code": "", "message": message})
            assert "请换用通用视觉模型" in line
            assert "如果你选择的是 O" not in line


# ---------------------------------------------------------------- capability_tag ---

class TestCapabilityTag:
    """Tests for the inline muted tag shown next to model selectors (#261)."""

    def test_translation_only_model_in_translation_role_gives_tag(self):
        from folith.qt.model_capability import capability_tag
        text, tooltip = capability_tag("translation", "tencent/Hunyuan-MT-7B")
        assert text == "专用翻译", f"Expected '专用翻译', got {text!r}"
        assert "Hunyuan" in tooltip or "翻译" in tooltip

    def test_ocr_only_model_in_translation_role_gives_tag(self):
        from folith.qt.model_capability import capability_tag
        text, tooltip = capability_tag("translation", "PaddlePaddle/PaddleOCR-VL-1.5")
        assert text == "仅 OCR", f"Expected '仅 OCR', got {text!r}"
        assert "OCR" in tooltip

    def test_general_model_gives_no_tag(self):
        from folith.qt.model_capability import capability_tag
        text, tooltip = capability_tag("translation", "qwen2.5-vl")
        assert text == "", f"Expected no tag for general model, got {text!r}"
        assert tooltip == ""

    def test_vision_role_never_gets_tag(self):
        from folith.qt.model_capability import capability_tag
        # Even an OCR-only model in the vision role gives no tag (channel A filters the list)
        text, tooltip = capability_tag("vision", "PaddlePaddle/PaddleOCR-VL-1.5")
        assert text == "", "vision role must never show inline tag"
        assert tooltip == ""

    def test_reviewer_translation_only_gives_tag(self):
        from folith.qt.model_capability import capability_tag
        text, tooltip = capability_tag("reviewer", "tencent/Hunyuan-MT-7B")
        assert text == "专用翻译"


# ---------------------------------------------------------------- Config migration ---

class TestOcrBackendMigration:
    """The migration logic in EngineConfig.load() must auto-switch ocr_backend from
    "online" to "ocr_api" when connection_models["vision"] is an OCR-only model (#261).

    EngineConfig.load() reads from the global config file on disk, so the migration
    logic is tested directly by constructing an EngineConfig and applying the same
    logic steps, rather than by calling load().
    """

    def _apply_migration(self, model_name: str) -> str:
        """Return the ocr_backend value after applying the migration logic."""
        cfg = adapter.EngineConfig(
            provider="openai", model="gpt-4o", api_key="k", base_url="",
        )
        cfg.ocr_backend = "online"
        cfg.connection_models = {"vision": model_name}
        # Replicate the migration block from EngineConfig.load()
        vision_model = str(cfg.connection_models.get("vision") or "")
        if vision_model:
            from folith.qt.model_capability import classify_model
            if classify_model(vision_model) == "ocr_only":
                cfg.ocr_backend = "ocr_api"
        return cfg.ocr_backend

    def test_ocr_only_vision_migrates_to_ocr_api(self):
        result = self._apply_migration("PaddlePaddle/PaddleOCR-VL-1.5")
        assert result == "ocr_api", (
            f"OCR-only vision model should migrate ocr_backend to 'ocr_api', "
            f"got {result!r}"
        )

    def test_general_vision_stays_online(self):
        result = self._apply_migration("qwen2.5-vl")
        assert result == "online", (
            f"General VLM should keep ocr_backend='online', got {result!r}"
        )

    def test_translation_only_stays_online(self):
        """Translation-only models (Hunyuan-MT) are not OCR-only; no migration."""
        result = self._apply_migration("tencent/Hunyuan-MT-7B")
        assert result == "online", (
            f"Translation-only model should keep ocr_backend='online', got {result!r}"
        )


# ---------------------------------------------------------------------------
# Fix 6: switching from OCR channel back to VLM channel with an OCR-only model
# ---------------------------------------------------------------------------

class TestOcrOnlyFallbackOnChannelSwitch:
    """Fix 6: When the user switches from OCR channel (B) back to VLM channel (A)
    with an OCR-only vision model selected, the vision selection must be cleared to
    「沿用翻译连接」because channel A's model list hides OCR-only models.
    """

    def _make_dialog_ocr_channel(self, model_name):
        """Create a dialog with OCR channel active and an OCR-only vision model."""
        profile = {
            "id": "p1", "name": "SFB", "provider": "SiliconFlow", "custom": False,
            "model": model_name, "models": [model_name],
            "api_key": "k", "base_url": "", "protocol": "openai_compat",
        }
        config = _config(
            connections=[profile],
            connection_roles={"translation": "p1", "vision": "p1"},
            connection_models={"vision": model_name},
            ocr_backend="ocr_api",
        )
        return dialogs.EngineDialog(config, _SyncRunner())

    def test_ocr_only_model_cleared_when_switching_to_vlm_channel(self, app, isolated):
        """Selecting the 视觉模型 card while an OCR-only model is active must
        clear the vision connection to 「沿用翻译模型」."""
        dialog = self._make_dialog_ocr_channel("PaddlePaddle/PaddleOCR-VL-1.5")
        assert dialog.ocr_backend_box.currentData() == "ocr_api", \
            "Dialog should start in OCR API channel"
        # Simulate clicking 视觉模型 card
        dialog.vision_option.setChecked(True)
        assert dialog.ocr_backend_box.currentData() == "online", \
            "Should have switched to VLM channel"
        # The vision connection should now be empty (沿用翻译连接 / index 0)
        lib = dialog.connection_library
        vision_conn = dialog.vision_connection_box.currentData()
        vision_model = lib.role_models.get("vision") or ""
        assert vision_conn == "" or vision_model == "", (
            f"After switching from OCR channel with OCR-only model, vision "
            f"connection must be cleared; got conn={vision_conn!r} model={vision_model!r}"
        )
        dialog.close()

    def test_general_model_kept_when_switching_to_vlm_channel(self, app, isolated):
        """Switching from OCR channel to VLM channel with a general VLM keeps the
        current vision selection intact."""
        profile = {
            "id": "p1", "name": "SFB", "provider": "SiliconFlow", "custom": False,
            "model": "Qwen/Qwen2.5-VL-72B-Instruct",
            "models": ["Qwen/Qwen2.5-VL-72B-Instruct"],
            "api_key": "k", "base_url": "", "protocol": "openai_compat",
        }
        config = _config(
            connections=[profile],
            connection_roles={"translation": "p1", "vision": "p1"},
            connection_models={"vision": "Qwen/Qwen2.5-VL-72B-Instruct"},
            ocr_backend="ocr_api",
        )
        dialog = dialogs.EngineDialog(config, _SyncRunner())
        dialog.vision_option.setChecked(True)
        assert dialog.ocr_backend_box.currentData() == "online"
        lib = dialog.connection_library
        vision_model = lib.role_models.get("vision") or ""
        assert vision_model == "Qwen/Qwen2.5-VL-72B-Instruct", (
            f"General VLM should be kept after channel switch; got {vision_model!r}"
        )
        dialog.close()


@pytest.mark.parametrize('has_vision', [True, False])
def test_switch_ocr_to_vision_keeps_role_model_and_ui_consistent(app, isolated, has_vision):
    ocr = 'PaddlePaddle/PaddleOCR-VL-1.5'
    vlm = 'Qwen/Qwen2.5-VL-72B-Instruct'
    profile = {'id': 'sf', 'name': 'SiliconFlow', 'provider': 'SiliconFlow',
               'custom': False, 'model': 'tencent/Hunyuan-MT-7B',
               'models': [ocr] + ([vlm] if has_vision else []),
               'model_modalities': {vlm: ['text', 'image']} if has_vision else {},
               'api_key': 'k', 'base_url': '', 'protocol': 'openai_compat'}
    dialog = dialogs.EngineDialog(_config(connections=[profile],
        connection_roles={'translation': 'sf', 'vision': 'sf'},
        connection_models={'vision': ocr}, ocr_backend='ocr_api'), _SyncRunner())
    dialog.vision_option.setChecked(True)
    lib = dialog.connection_library
    saved = dialog._collect()
    if has_vision:
        assert dialog.vision_connection_box.currentData() == lib.roles['vision'] == saved.connection_roles['vision'] == 'sf'
        assert lib.model_selectors['vision'].currentText() == lib.role_models['vision'] == saved.connection_models['vision'] == vlm
    else:
        from folith.model_connections import VISION_UNCONFIGURED
        assert dialog.vision_connection_box.currentData() == lib.roles['vision'] == saved.connection_roles['vision'] == VISION_UNCONFIGURED
        assert not lib.model_selectors['vision'].currentText()
        assert not saved.connection_models['vision']
        assert '未配置' in dialog.vision_connection_box.currentText()
    dialog.local_option.setChecked(True)
    saved = dialog._collect()
    assert saved.connection_roles['vision'] == 'sf'
    assert saved.connection_models['vision'] == ocr
    assert lib.model_selectors['vision'].currentText() == ocr
    dialog.close()


def test_vision_selector_excludes_known_ocr_and_text_only_models(app, isolated):
    ocr = 'PaddlePaddle/PaddleOCR-VL-1.5'
    vlm = 'Qwen/Qwen2.5-VL-72B-Instruct'
    text = 'tencent/Hunyuan-MT-7B'
    profile = {'id': 'sf', 'name': 'SiliconFlow', 'provider': 'SiliconFlow',
               'custom': False, 'model': text, 'models': [ocr, vlm],
               'model_modalities': {vlm: ['text', 'image'], text: ['text']},
               'api_key': 'k', 'base_url': '', 'protocol': 'openai_compat'}
    dialog = dialogs.EngineDialog(_config(connections=[profile],
        connection_roles={'translation': 'sf', 'vision': 'sf'},
        connection_models={'vision': vlm}, ocr_backend='online'), _SyncRunner())
    box = dialog.connection_library.model_selectors['vision']
    assert [box.itemText(i) for i in range(box.count())] == [vlm]
    dialog.close()
