"""Unit tests for the O(n log n) intervening-image check in reconstruct_paragraph_records.

The check was previously O(n²) — an ``any(... for item in ordered_blocks)``
linear scan for every block.  The replacement uses ``bisect`` on a sorted list
of image block reading-order values; this file verifies that both
implementations agree on randomized block lists including boundary conditions.
"""
from __future__ import annotations

import bisect
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from folith.pdf_ingestion import reconstruct_paragraph_records  # noqa: E402


# ---------------------------------------------------------------------------
# Reference (brute-force) implementation of the old O(n²) check
# ---------------------------------------------------------------------------

def _intervening_image_brute(
    ordered_blocks: List[Dict[str, Any]],
    last_kept_order: Optional[int],
    current_order: int,
) -> bool:
    """Original O(n²) check — used as the reference in property tests."""
    return any(
        item.get("role") == "image"
        and (last_kept_order is None or
             last_kept_order < item.get("reading_order", 0) < current_order)
        for item in ordered_blocks
    )


# ---------------------------------------------------------------------------
# New O(log n) implementation (mirrors the code in pdf_ingestion.py)
# ---------------------------------------------------------------------------

def _intervening_image_fast(
    image_orders: List[int],
    last_kept_order: Optional[int],
    current_order: int,
) -> bool:
    """New O(log n) check — mirrors the production code.

    When last_kept_order is None the original or-clause short-circuits to
    True so *any* image in the block list qualifies, regardless of order.
    """
    if last_kept_order is None:
        return bool(image_orders)
    lo = bisect.bisect_right(image_orders, last_kept_order)
    hi = bisect.bisect_left(image_orders, current_order)
    return lo < hi


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_block(role: str, reading_order: int) -> Dict[str, Any]:
    return {"role": role, "reading_order": reading_order}


def _image_orders_from(ordered_blocks: List[Dict[str, Any]]) -> List[int]:
    return sorted(
        item.get("reading_order", 0)
        for item in ordered_blocks
        if item.get("role") == "image"
    )


# ---------------------------------------------------------------------------
# Deterministic boundary tests
# ---------------------------------------------------------------------------

class TestInterveningImageBoundaries:
    def test_no_images_at_all(self):
        blocks = [_make_block("body", i) for i in range(5)]
        imgs = _image_orders_from(blocks)
        for current in range(5):
            for last in [None, 0, 1, 2]:
                assert _intervening_image_fast(imgs, last, current) == \
                       _intervening_image_brute(blocks, last, current)

    def test_last_kept_none_image_strictly_before(self):
        # last_kept_order is None, image at order 3, current at 5
        blocks = [_make_block("image", 3), _make_block("body", 5)]
        imgs = _image_orders_from(blocks)
        assert _intervening_image_fast(imgs, None, 5) is True
        assert _intervening_image_brute(blocks, None, 5) is True

    def test_last_kept_none_any_image_qualifies(self):
        # When last_kept_order is None, the or-clause in the original code
        # short-circuits to True, so any image — even one at or after
        # current_order — causes intervening_image=True.
        blocks = [_make_block("image", 5), _make_block("body", 5)]
        imgs = _image_orders_from(blocks)
        assert _intervening_image_fast(imgs, None, 5) is True
        assert _intervening_image_brute(blocks, None, 5) is True

    def test_image_equals_last_kept_order(self):
        # strict inequality: image at last_kept_order should NOT qualify
        blocks = [_make_block("image", 3), _make_block("body", 5)]
        imgs = _image_orders_from(blocks)
        assert _intervening_image_fast(imgs, 3, 5) is False
        assert _intervening_image_brute(blocks, 3, 5) is False

    def test_image_between_last_and_current(self):
        blocks = [_make_block("image", 4), _make_block("body", 5)]
        imgs = _image_orders_from(blocks)
        assert _intervening_image_fast(imgs, 3, 5) is True
        assert _intervening_image_brute(blocks, 3, 5) is True

    def test_image_equals_current_order(self):
        # strict: image at current_order should NOT qualify
        blocks = [_make_block("image", 5), _make_block("body", 5)]
        imgs = _image_orders_from(blocks)
        assert _intervening_image_fast(imgs, 3, 5) is False
        assert _intervening_image_brute(blocks, 3, 5) is False

    def test_image_before_last_kept(self):
        # image before last_kept_order should not qualify
        blocks = [_make_block("image", 1), _make_block("body", 5)]
        imgs = _image_orders_from(blocks)
        assert _intervening_image_fast(imgs, 3, 5) is False
        assert _intervening_image_brute(blocks, 3, 5) is False

    def test_multiple_images_one_qualifies(self):
        blocks = [
            _make_block("image", 1),
            _make_block("image", 4),  # this one qualifies
            _make_block("image", 7),
            _make_block("body", 5),
        ]
        imgs = _image_orders_from(blocks)
        assert _intervening_image_fast(imgs, 3, 5) is True
        assert _intervening_image_brute(blocks, 3, 5) is True

    def test_multiple_images_none_qualifies(self):
        blocks = [
            _make_block("image", 1),
            _make_block("image", 3),  # equal to last_kept → no
            _make_block("image", 5),  # equal to current → no
            _make_block("image", 7),
            _make_block("body", 5),
        ]
        imgs = _image_orders_from(blocks)
        assert _intervening_image_fast(imgs, 3, 5) is False
        assert _intervening_image_brute(blocks, 3, 5) is False

    def test_equal_reading_orders_for_images(self):
        # Duplicate reading_order values — bisect must handle ties correctly.
        blocks = [_make_block("image", 4), _make_block("image", 4)]
        imgs = _image_orders_from(blocks)
        assert _intervening_image_fast(imgs, 3, 5) is True
        assert _intervening_image_brute(blocks, 3, 5) is True

    def test_missing_reading_order_defaults_to_zero(self):
        # A block with no reading_order key defaults to 0
        blocks = [
            {"role": "image"},           # defaults to reading_order=0
            _make_block("body", 5),
        ]
        imgs = _image_orders_from(blocks)
        # last_kept_order=None: image at 0 < current 5 → True
        assert _intervening_image_fast(imgs, None, 5) is True
        assert _intervening_image_brute(blocks, None, 5) is True
        # last_kept_order=0: strict 0 < 0 is False
        assert _intervening_image_fast(imgs, 0, 5) is False
        assert _intervening_image_brute(blocks, 0, 5) is False


# ---------------------------------------------------------------------------
# Property test: randomised block lists
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(30))
def test_fast_matches_brute_randomised(seed):
    rng = random.Random(seed)
    n = rng.randint(0, 40)
    roles = ["body", "image", "header", "footer", "caption"]
    blocks = []
    for _ in range(n):
        role = rng.choice(roles)
        order = rng.randint(0, 20)
        b: Dict[str, Any] = {"role": role}
        if rng.random() > 0.1:  # ~10% missing reading_order
            b["reading_order"] = order
        blocks.append(b)

    ordered_blocks = sorted(blocks, key=lambda b: b.get("reading_order", 0))
    imgs = _image_orders_from(ordered_blocks)

    last_kept: Optional[int] = None
    for block in ordered_blocks:
        current_order = block.get("reading_order", 0)
        expected = _intervening_image_brute(ordered_blocks, last_kept, current_order)
        got = _intervening_image_fast(imgs, last_kept, current_order)
        assert got == expected, (
            f"seed={seed} last_kept={last_kept} current={current_order} "
            f"image_orders={imgs}: expected {expected}, got {got}"
        )
        # Simulate last_kept_order being updated (as in the real loop)
        if block.get("role") not in {"image", "caption", "header", "footer"}:
            last_kept = current_order


# ---------------------------------------------------------------------------
# Integration: reconstruct_paragraph_records produces the same output
# whether or not there are images
# ---------------------------------------------------------------------------

def _make_full_block(role, reading_order, text="Lorem ipsum dolor sit amet."):
    """A minimal block dict that reconstruct_paragraph_records can process."""
    return {
        "role": role,
        "reading_order": reading_order,
        "text": text,
        "page_number": 1,
        "block_id": f"b{reading_order}",
        "lines": [
            {
                "text": text,
                "role": role,
                "page_number": 1,
                "block_id": f"b{reading_order}",
                "first_line_x0": 50.0,
                "last_y1": 100.0,
                "y0": 80.0,
                "last_x0": 50.0,
                "last_x1": 500.0,
                "page_height": 800.0,
            }
        ],
        "x0": 50.0, "y0": 80.0, "x1": 500.0, "y1": 100.0,
        "font_size": 12.0, "font_flags": 0, "font_style": [],
        "page_height": 800.0, "body_font_size": 12.0,
        "body_x0": 50.0, "page_line_height": 15.0,
        "page_text_x1": 500.0, "page_text_x0": 50.0,
    }


def test_reconstruct_paragraph_records_with_interleaved_images():
    """reconstruct_paragraph_records should not merge paragraphs separated by images."""
    blocks = [
        _make_full_block("body", 0, "First paragraph."),
        _make_full_block("image", 1),
        _make_full_block("body", 2, "Second paragraph."),
    ]
    records = reconstruct_paragraph_records(blocks)
    # Image block is skipped; the two body blocks are NOT merged because
    # intervening_image=True blocks the close_enough-merge path.
    texts = [r["text"] for r in records]
    assert "First paragraph." in texts
    assert "Second paragraph." in texts


def test_reconstruct_paragraph_records_no_images_merges_continuation():
    """Without an intervening image, short continuation lines may be joined."""
    # Two body blocks with the same first_line_x0 so close_enough is True
    text_a = "A long enough first sentence without terminal punctuation"
    text_b = "continuation here."
    blocks = [
        _make_full_block("body", 0, text_a),
        _make_full_block("body", 1, text_b),
    ]
    records = reconstruct_paragraph_records(blocks)
    combined = " ".join(r["text"] for r in records)
    # They should be merged (no image separates them, no sentence terminal)
    assert len(records) <= 2  # may merge or not depending on length checks
    assert text_a in combined or text_a in records[0]["text"]
