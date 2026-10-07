"""Check actionable measurement failures without making provider requests."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ocr_vision_pilot", ROOT / "benchmark/run_ocr_vision_pilot.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def test_alignment_does_not_mark_repetitive_correct_text_as_errors():
    reference = "هذا الكتاب " * 35 + "حركاتهم"
    before = "هذا الكتاب " * 35 + "حركاتم"
    result = pilot.metrics(reference, before, reference)
    assert result["reference_positions_corrected"] == 1
    assert result["reference_positions_introduced"] == 0
    assert result["net_edit_reduction"] == 1


def test_improvement_and_miscorrection_are_reported_separately():
    result = pilot.metrics("حركاتهم سكناتهم", "حركاتم سكناتم", "حركاتهم سكتاتهم")
    assert result["reference_positions_corrected"] == 2
    assert result["reference_positions_introduced"] == 1
    assert result["net_edit_reduction"] == 1
    assert result["character_errors"]["sub"] == 1


def test_inserted_model_text_is_counted_even_if_reference_matches():
    result = pilot.metrics("كتاب", "كتاب", "كتاب اضافي")
    assert result["reference_positions_introduced"] == 0
    assert result["extra_characters_after"] == 6
    assert result["net_edit_reduction"] == -6


def test_image_payload_contains_only_given_prompt_and_image(tmp_path):
    image = tmp_path / "scan.png"
    image.write_bytes(b"sample_image_bytes")
    content = pilot.request_content("OCR candidate only", image)
    assert len(content) == 2
    assert content[1] == {"type": "text", "text": "OCR candidate only"}
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_fresh_reference_pages_were_not_used_for_training_or_selection():
    fresh = json.loads((ROOT / "benchmark/ocr-vision-pilot/fresh-references.json").read_text())
    training = json.loads((ROOT / "benchmark/paddle-finetune/dataset-manifest.json").read_text())
    old_pages = {int(page) for page in training["page_splits"]}
    assert not {p["page_index"] for p in fresh["pages"]} & old_pages
    assert all(not p["training_used"] and not p["checkpoint_selection_used"] for p in fresh["pages"])
