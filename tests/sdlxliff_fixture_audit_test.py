"""Issue #145 Gate A 看门测试：入库的真实 SDLXLIFF 样本结构必须稳定。

这些样本来自 RWS（Trados 厂商）Apache-2.0 官方仓库，是 Gate B（受限 round
trip）将来要依据的证据基线。本测试只读校验样本的关键结构事实与
docs/sdlxliff-gate-a-compatibility-matrix.md 的记载一致：样本被意外替换或
损坏时先在这里失败。不做任何导入/写回，不承诺格式支持。

运行：python tests/sdlxliff_fixture_audit_test.py
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "sdlxliff"
SDL_NS = "http://sdl.com/FileTypes/SdlXliff/1.0"
XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def audit_quotes_sample() -> None:
    root = ET.parse(FIXTURES / "QuotesSample.docx.sdlxliff").getroot()
    _check(root.tag == f"{{{XLIFF_NS}}}xliff", "根元素必须是 XLIFF 1.2 xliff")
    _check(root.get("version") == "1.2", "xliff version 必须是 1.2")
    _check(
        root.get(f"{{{SDL_NS}}}version") == "1.0",
        "根元素 sdl:version 必须是 1.0（矩阵 §2.1）",
    )

    # 状态值域（矩阵 M1–M4）
    confs = {
        seg.get("conf")
        for seg in root.iter(f"{{{SDL_NS}}}seg")
        if seg.get("conf") is not None
    }
    _check(
        confs == {"Draft", "Translated", "ApprovedTranslation", "ApprovedSignOff"},
        f"QuotesSample 状态值域变化：{sorted(confs)}",
    )

    # locked 段（矩阵 M6）
    locked = [s for s in root.iter(f"{{{SDL_NS}}}seg") if s.get("locked") == "true"]
    _check(len(locked) == 2, f"locked 段数应为 2，实际 {len(locked)}")

    # 注释必须成对：根级 cmt-def 载荷 + 正文 x-sdl-comment 锚点（矩阵 §2.3/P7/P8）
    cmt_defs = {
        d.get("id") for d in root.iter(f"{{{SDL_NS}}}cmt-def") if d.get("id")
    }
    cids = {
        mrk.get(f"{{{SDL_NS}}}cid")
        for mrk in root.iter(f"{{{XLIFF_NS}}}mrk")
        if mrk.get("mtype") == "x-sdl-comment"
    }
    _check(len(cmt_defs) == 2, f"cmt-def 数应为 2，实际 {len(cmt_defs)}")
    _check(cids == cmt_defs, f"注释锚点 cid 与 cmt-def 不成对：{cids} vs {cmt_defs}")

    # 段溯源 sdl:value（矩阵 P9）
    seg_values = [
        v
        for seg in root.iter(f"{{{SDL_NS}}}seg")
        for v in seg.findall(f"{{{SDL_NS}}}value")
    ]
    keys = {v.get("key") for v in seg_values}
    _check(
        {"SegmentIdentityHash", "tuid", "tuguid", "created_by"} <= keys,
        f"段溯源 value 缺键：{sorted(keys)}",
    )

    # 原始文件 base64 必须存在（矩阵 P1）
    _check(
        root.find(f".//{{{XLIFF_NS}}}internal-file") is not None,
        "header 必须含 internal-file（原始文件 base64）",
    )


def audit_tts_sample() -> None:
    # 状态缺省场景：seg-defs 存在但无 conf（矩阵 M5）
    root = ET.parse(FIXTURES / "TTSExample.docx.sdlxliff").getroot()
    segs = list(root.iter(f"{{{SDL_NS}}}seg"))
    _check(len(segs) == 4, f"TTSExample 段数应为 4，实际 {len(segs)}")
    _check(
        all(seg.get("conf") is None for seg in segs),
        "TTSExample 的 seg 不应带 conf（缺省状态场景）",
    )


def audit_structure_tags() -> None:
    # sdl:st 结构标签不可当作译文（矩阵 P12）
    root = ET.parse(FIXTURES / "SamplePhotoPrinter.doc.sdlxliff").getroot()
    sts = list(root.iter(f"{{{SDL_NS}}}st"))
    _check(len(sts) >= 80, f"sdl:st 结构标签数应 ≥80，实际 {len(sts)}")
    _check(
        any(st.get("name") == "paragraph" for st in sts),
        "sdl:st 应包含 paragraph 结构标签",
    )


def main() -> int:
    _check(FIXTURES.is_dir(), "缺少 SDLXLIFF fixtures 目录")
    audit_quotes_sample()
    audit_tts_sample()
    audit_structure_tags()
    print("sdlxliff fixture audit: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
