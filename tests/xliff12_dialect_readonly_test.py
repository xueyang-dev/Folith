"""P3-4：SDLXLIFF / MQXLIFF 只读配置。数字钉在 P3-3 报告 §3.5。

``excl`` 那一列是 ``translate="no"`` 的排除行。SamplePhotoPrinter 的 26 条
原因是 ``translate_no_placeholder``，不是 ``translate_no``。每份文件的拒绝数
是 0。数字变了要先解释，不能直接改这张表。
"""
from __future__ import annotations

import glob
import os

import pytest

from folith.interop.xliff12_dialect import (
    MQXLIFF,
    SDLXLIFF,
    classify_structural_diffs,
    export_unchanged,
    prove_readonly,
    write_target,
    DialectWriteError,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MQ = os.path.join(ROOT, "tests", "fixtures", "mqxliff")
SDL = os.path.join(ROOT, "tests", "fixtures", "sdlxliff")

# P3-3 §3.5，2026-11-13，基线当时是 6477e52f 上的内核。
EXPECTED = {
    "document.mqxliff": (16, 1, 0, 0, 0),
    "locked.mqxliff": (3, 1, 0, 0, 0),
    "mt.mqxliff": (51, 0, 0, 0, 0),
    "pe.mqxliff": (51, 0, 0, 0, 0),
    "translation_status.mqxliff": (12, 6, 0, 0, 0),
    "QuotesSample.docx.sdlxliff": (5, 2, 0, 0, 0),
    "SamplePhotoPrinter.doc.sdlxliff": (46, 0, 0, 26, 0),
    "TTSExample.docx.sdlxliff": (4, 0, 0, 0, 0),
    "quickplace.docx.sdlxliff": (5, 0, 0, 0, 0),
}


def _samples(directory: str, suffix: str) -> list[str]:
    return sorted(glob.glob(os.path.join(directory, "*" + suffix)))


SDL_SAMPLES = _samples(SDL, ".sdlxliff")
MQ_SAMPLES = _samples(MQ, ".mqxliff")
ALL = [(SDLXLIFF, path) for path in SDL_SAMPLES] + [(MQXLIFF, path) for path in MQ_SAMPLES]


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def test_corpus_is_still_four_sdl_and_five_mq():
    assert [os.path.basename(path) for path in SDL_SAMPLES] == [
        "QuotesSample.docx.sdlxliff",
        "SamplePhotoPrinter.doc.sdlxliff",
        "TTSExample.docx.sdlxliff",
        "quickplace.docx.sdlxliff",
    ]
    assert [os.path.basename(path) for path in MQ_SAMPLES] == [
        "document.mqxliff",
        "locked.mqxliff",
        "mt.mqxliff",
        "pe.mqxliff",
        "translation_status.mqxliff",
    ]


@pytest.mark.parametrize(
    "config,path",
    ALL,
    ids=[os.path.basename(path) for _, path in ALL],
)
def test_readonly_round_trip_matches_p3_3_counts(config, path):
    name = os.path.basename(path)
    data = _read(path)
    report = prove_readonly(config, data)
    units, readonly, translate_no, placeholder, rejections = EXPECTED[name]
    assert (report["units"], report["readonly_rows"], report["translate_no"],
            report["translate_no_placeholder"], report["rejections"]) == (
                units, readonly, translate_no, placeholder, rejections)
    assert report["ok"], {
        "unexplained": report["unexplained"],
        "missing_namespaces": report["missing_namespaces"],
        "locked": report["locked_targets_ok"],
        "verification": report["verification_ok"],
    }
    assert report["byte_identical"]
    assert report["structural_diffs"] == []
    assert report["explained"] == []
    assert export_unchanged(config, data) == data


def test_pinned_totals_are_the_p3_3_table():
    totals = {"units": 0, "readonly": 0, "placeholder": 0}
    for config, path in ALL:
        report = prove_readonly(config, _read(path))
        totals["units"] += report["units"]
        totals["readonly"] += report["readonly_rows"]
        totals["placeholder"] += report["translate_no_placeholder"]
    assert totals == {"units": 193, "readonly": 10, "placeholder": 26}


def test_the_other_dialect_drops_the_real_locks():
    """两份配置必须真的不同。用错一份，P3-3 的锁定数字就对不上。"""
    quotes = _read(os.path.join(SDL, "QuotesSample.docx.sdlxliff"))
    document = _read(os.path.join(MQ, "document.mqxliff"))
    assert prove_readonly(SDLXLIFF, quotes)["readonly_rows"] == 2
    assert prove_readonly(MQXLIFF, quotes)["readonly_rows"] == 0
    assert prove_readonly(MQXLIFF, document)["readonly_rows"] == 1
    assert prove_readonly(SDLXLIFF, document)["readonly_rows"] == 0


def test_dialect_config_has_no_translation_write_entry():
    data = _read(MQ_SAMPLES[0])
    with pytest.raises(DialectWriteError, match="只读"):
        write_target(MQXLIFF, data, "mq/1", "新译文")
    assert export_unchanged(MQXLIFF, data) == data


def test_line_ending_rule_accepts_only_a_bare_cr_change():
    rules = SDLXLIFF.whitelist
    crlf = classify_structural_diffs([{
        "path": "/xliff:0/file:0#text",
        "kind": "changed",
        "before": "a\r\nb",
        "after": "a\nb",
    }], rules)
    assert crlf["unexplained"] == []
    assert crlf["explained"][0]["rule_id"] == "crlf-lf"
    assert "CR" in crlf["explained"][0]["reason"]
    changed = classify_structural_diffs([{
        "path": "/xliff:0/file:0#text",
        "kind": "changed",
        "before": "alpha",
        "after": "beta",
    }], rules)
    assert changed["explained"] == []
    assert changed["unexplained"][0]["before"] == "alpha"
    attribute = classify_structural_diffs([{
        "path": "/xliff:0@id",
        "kind": "changed",
        "before": "a\r",
        "after": "a",
    }], rules)
    assert attribute["explained"] == []
