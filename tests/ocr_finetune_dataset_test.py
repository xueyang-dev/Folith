"""Guard page separation and image/label alignment in the book pilot."""
import pytest

from benchmark.prepare_paddle_finetune import align_lines, page_split, similarity


def test_split_keeps_every_page_in_one_partition_and_is_reproducible():
    pages = [20, 100, 50, 140, 180, 169, 211, 7, 220, 5]
    split = page_split(pages)
    assert split == page_split(pages[::-1])
    partitions = [{p for p, group in split.items() if group == name}
                  for name in ("train", "validation", "test")]
    assert list(map(len, partitions)) == [6, 2, 2]
    assert set.union(*partitions) == set(pages)
    assert all(not a & b for i, a in enumerate(partitions) for b in partitions[i + 1:])


def test_alignment_skips_spurious_rows_without_reordering_human_labels():
    references = ["هذا سطر أول", "هذا سطر ثان", "عنوان قصير"]
    candidates = [{"texts": [text]} for text in
                  ("رقم الصفحة", "هذا سطر أول", "نقطة حبر", "هذا سطر ثان", "عنوان قصير", "سطر لاحق")]
    assert align_lines(references, candidates) == [1, 3, 4]
    assert references == ["هذا سطر أول", "هذا سطر ثان", "عنوان قصير"]


def test_alignment_uses_best_evidence_geometry_without_using_ocr_as_label():
    references = ["النص الصحيح", "١٢٣ Ali Smith"]
    candidates = [{"texts": ["نص تالف", "النص الصحيح"]},
                  {"texts": ["١٢٣ Ali Smith"]}]
    assert align_lines(references, candidates) == [0, 1]
    assert references[0] == "النص الصحيح"
    assert similarity(references[1], "Smith Ali ٣٢١") < 1


def test_insufficient_image_rows_are_rejected_instead_of_reusing_labels():
    with pytest.raises(ValueError, match="Only 1 image rows"):
        align_lines(["سطر أول", "سطر ثان"], [{"texts": ["سطر أول"]}])
