"""P3-2a：真实 memoQ / Trados 语料的状态词回归防线。

数据源是仓库里已入库的真实 CAT 导出样本（只读，来源与许可见各目录
``README.md``）：

- ``tests/fixtures/mqxliff/*.mqxliff``：memoQ 的状态在
  ``trans-unit/@mq:status``（历史版本容器 ``mq:historical-unit`` 上也有），
  5 个文件实测 **363 个 token / 7 种取值**；
- ``tests/fixtures/sdlxliff/*.sdlxliff``：Trados 的状态在
  ``sdl:seg/@conf``，4 个文件实测 **8 个 token / 4 种取值**。

本测试把语料里出现的每一个状态词参数化，断言它**必须**映射到规范词表。
它是 P3-2/P3-3 的回归防线：任何状态词落在词表外都会在这里变红，而不是等到
真实导入时整批 ``UNMAPPABLE_STATUS``。

本测试只读 fixture；不做导入、不写回，也不承诺格式支持。
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from folith.interop.tu_contract import (  # noqa: E402
    STATUS_MAP,
    map_cat_status,
)

FIXTURES = REPO_ROOT / "tests" / "fixtures"
MQ_DIR = FIXTURES / "mqxliff"
SDL_DIR = FIXTURES / "sdlxliff"
XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
MQ_NS = "MQXliff"

#: 语料实测值 → 契约规范状态。新增真实取值时必须同时改契约词表与
#: ``docs/tu-contract-v1.md`` §3；本表是"每个映射都能指到文档定义"的锚点。
EXPECTED_CANONICAL = {
    # memoQ mq:status
    "NotStarted": "untranslated",
    "ManuallyConfirmed": "confirmed",
    "MachineTranslated": "translated",
    "PreTranslated": "translated",
    "PartiallyEdited": "translated",
    "Proofread": "confirmed",
    "Reviewer1Confirmed": "confirmed",
    # Trados sdl:seg/@conf
    "Draft": "translated",
    "Translated": "translated",
    "ApprovedTranslation": "confirmed",
    "ApprovedSignOff": "confirmed",
}


def _localname(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def _status_tokens(path: Path, attr_names: set[str]) -> list[str]:
    """按属性本地名读取状态词；不解析文本内容，不匹配任何内嵌 XML 文本。"""
    root = ET.parse(path).getroot()
    tokens: list[str] = []
    for element in root.iter():
        for key, value in element.attrib.items():
            if _localname(key) in attr_names:
                tokens.append(str(value))
    return tokens


def _per_file_counts(path: Path, attr_names: set[str]) -> Counter:
    return Counter(_status_tokens(path, attr_names))


def _corpus_cases() -> list:
    cases = []
    for path in sorted(MQ_DIR.glob("*.mqxliff")):
        for value, count in sorted(_per_file_counts(path, {"status", "state"}).items()):
            cases.append(pytest.param(
                path, value, count, id=f"mq/{path.name}::{value}"))
    for path in sorted(SDL_DIR.glob("*.sdlxliff")):
        for value, count in sorted(_per_file_counts(path, {"conf"}).items()):
            cases.append(pytest.param(
                path, value, count, id=f"sdl/{path.name}::{value}"))
    return cases


CORPUS_CASES = _corpus_cases()


def test_real_corpus_fixtures_are_present():
    """语料缺失时显式失败，绝不静默跳过这条回归防线。"""
    assert list(MQ_DIR.glob("*.mqxliff")), f"缺少真实 memoQ 样本：{MQ_DIR}"
    assert list(SDL_DIR.glob("*.sdlxliff")), f"缺少真实 Trados 样本：{SDL_DIR}"
    assert CORPUS_CASES, "真实语料里没有扫到任何状态词"


@pytest.mark.parametrize("path,status,count", CORPUS_CASES)
def test_every_real_corpus_status_word_maps(path, status, count):
    assert count >= 1
    fields = map_cat_status(status)  # 表外值在这里抛 StatusMappingError
    # 映射结果必须落在 canonical 词表内（不是任意 dict）。
    assert fields in STATUS_MAP.values(), (path.name, status, fields)
    expected = EXPECTED_CANONICAL.get(status)
    assert expected is not None, (
        f"{path.name} 出现未登记的真实状态词 {status!r}："
        f"先补契约词表与 docs/tu-contract-v1.md §3，再登记进本测试")
    assert fields == STATUS_MAP[expected], (path.name, status, expected)


def test_real_corpus_status_totals_match_the_measured_baseline():
    """实测基线：memoQ 363 token / 7 值；Trados 8 token / 4 值。"""
    mq_counts: Counter = Counter()
    for path in sorted(MQ_DIR.glob("*.mqxliff")):
        mq_counts.update(_per_file_counts(path, {"status", "state"}))
    sdl_counts: Counter = Counter()
    for path in sorted(SDL_DIR.glob("*.sdlxliff")):
        sdl_counts.update(_per_file_counts(path, {"conf"}))

    assert sum(mq_counts.values()) == 363
    assert set(mq_counts) == {
        "NotStarted", "ManuallyConfirmed", "MachineTranslated", "PreTranslated",
        "PartiallyEdited", "Proofread", "Reviewer1Confirmed"}
    assert mq_counts.most_common(3) == [
        ("NotStarted", 160), ("ManuallyConfirmed", 91), ("MachineTranslated", 72)]

    assert sum(sdl_counts.values()) == 8
    assert set(sdl_counts) == {
        "Translated", "Draft", "ApprovedSignOff", "ApprovedTranslation"}


def test_memoq_lock_flag_is_carried_separately_from_status():
    """锁定是正交标志（``mq:locked`` / ``translate="no"``），不是状态词。

    语料证据：9 个带 ``mq:locked="locked"`` 的容器（8 个 ``trans-unit`` +
    1 个 ``mq:historical-unit``）**每个都另有独立的 ``mq:status``**，且状态值
    从不是 "locked"。``translation_status.mqxliff`` 的 6 个锁定段覆盖 6 种
    不同状态，说明"锁定"与状态正交。适配器必须把 ``mq:locked`` 当独立标志
    读取，不能当状态词喂给 ``map_cat_status``。
    """
    locked_elements = []
    status_tokens: set[str] = set()
    for path in sorted(MQ_DIR.glob("*.mqxliff")):
        root = ET.parse(path).getroot()
        for element in root.iter():
            status = element.get(f"{{{MQ_NS}}}status")
            if status is not None:
                status_tokens.add(str(status))
            if element.get(f"{{{MQ_NS}}}locked") == "locked":
                locked_elements.append((path.name, element))

    assert len(locked_elements) == 9
    for name, element in locked_elements:
        # 锁定容器同时带标准 translate="no"，且状态由 mq:status 单独给出。
        assert element.get("translate") == "no", name
        status = element.get(f"{{{MQ_NS}}}status")
        assert status and status != "locked", (name, status)
    # 锁定标志的取值从不占用状态属性；把 "locked" 当状态会与 P3-1 的
    # "锁定是正交标志"裁定冲突（该裁定仍待用户拍板，本测试不修改语义）。
    assert "locked" not in status_tokens


# ---------------------------------------------------------------------------
# P3-2b：真实语料上的标签校验（0 误判）与单元切割（直接子节点）
# ---------------------------------------------------------------------------
# 下面两个投影函数是"适配器的最小替身"：只把真实 XML 投影成契约层要的
# ``(text, [InlineTag])``，不写回。配对键取每侧自己的 ``rid``（无则 ``id``），
# 与真实 memoQ 语义一致；不跨侧对齐编号。

PLACEHOLDER = "\ue000{}\ue001"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class _TagProjector:
    def __init__(self) -> None:
        self.tags: list = []
        self.parts: list[str] = []
        self._next = 1

    def _add(self, kind: str, payload: str, code_name: str = "") -> None:
        from folith.interop.tu_contract import InlineTag

        tag_id = str(self._next)
        self._next += 1
        self.tags.append(InlineTag(tag_id, kind, payload, code_name))
        self.parts.append(PLACEHOLDER.format(tag_id))

    def project(self, element):
        self._walk(element)
        return "".join(self.parts), self.tags

    def _walk(self, element) -> None:
        self.parts.append(element.text or "")
        for child in element:
            name = _local(child.tag)
            payload = ET.tostring(child, encoding="unicode")
            if name == "bpt":
                key = child.get("rid") or child.get("id") or "?"
                self._add("open", payload, f"mq:rid={key}")
            elif name == "ept":
                key = child.get("rid") or child.get("id") or "?"
                self._add("close", payload, f"mq:rid={key}")
            elif name in ("ph", "x"):
                self._add("self_closing", payload)
            elif name == "g":
                key = child.get("id") or "?"
                self._add("open", f"<g id={key}>", f"sdl:g={key}")
                self._walk(child)
                self._add("close", "</g>", f"sdl:g={key}")
            else:
                self._walk(child)
            self.parts.append(child.tail or "")


def _project(element):
    return _TagProjector().project(element)


def _direct_child(element, name: str):
    for child in element:
        if _local(child.tag) == name:
            return child
    return None


def _units(root):
    return [e for e in root.iter() if _local(e.tag) == "trans-unit"]


def _corpus_files() -> list[Path]:
    return sorted(MQ_DIR.glob("*.mqxliff")) + sorted(SDL_DIR.glob("*.sdlxliff"))


def _tag_cases() -> list:
    cases = []
    for path in _corpus_files():
        root = ET.parse(path).getroot()
        sources = sum(1 for tu in _units(root) if _direct_child(tu, "source") is not None)
        targets = sum(1 for tu in _units(root)
                      if _direct_child(tu, "source") is not None
                      and _direct_child(tu, "target") is not None)
        cases.append(pytest.param(path, sources, targets,
                                  id=f"{path.parent.name}/{path.name}"))
    return cases


TAG_CASES = _tag_cases()


@pytest.mark.parametrize("path,sources,targets", TAG_CASES)
def test_real_corpus_source_tag_sequences_validate_clean(path, sources, targets):
    """P3-2b 误判 2：memoQ 交叠格式跨度不是"嵌套错乱"。"""
    from folith.interop.tu_contract import validate_tag_sequence

    root = ET.parse(path).getroot()
    checked = 0
    for tu in _units(root):
        source = _direct_child(tu, "source")
        if source is None:
            continue
        text, tags = _project(source)
        assert validate_tag_sequence(text, tags) == [], (path.name, tu.get("id"))
        checked += 1
    assert checked == sources, (path.name, checked, sources)


@pytest.mark.parametrize("path,sources,targets", TAG_CASES)
def test_real_corpus_target_tags_validate_clean(path, sources, targets):
    """P3-2b 误判 1：每侧独立 ``id``/``rid`` 不是"伪造负载/改写配对"。"""
    from folith.interop.tu_contract import validate_target_tags

    root = ET.parse(path).getroot()
    checked = 0
    for tu in _units(root):
        source = _direct_child(tu, "source")
        target = _direct_child(tu, "target")
        if source is None or target is None:
            continue
        source_text, source_tags = _project(source)
        target_text, target_tags = _project(target)
        assert validate_target_tags(
            source_text, source_tags, target_text, target_tags) == [], (
                path.name, tu.get("id"))
        checked += 1
    assert checked == targets, (path.name, checked, targets)


def test_real_corpus_tag_validation_has_zero_false_positives():
    """汇总基线：9 个文件、193 个 trans-unit（167 个带译文）全部 0 错误。"""
    from folith.interop.tu_contract import (
        validate_tag_sequence,
        validate_target_tags,
    )

    units = 0
    sources = 0
    targets = 0
    for path in _corpus_files():
        root = ET.parse(path).getroot()
        for tu in _units(root):
            units += 1
            source = _direct_child(tu, "source")
            if source is None:
                continue
            sources += 1
            source_text, source_tags = _project(source)
            assert validate_tag_sequence(source_text, source_tags) == [], (
                path.name, tu.get("id"))
            target = _direct_child(tu, "target")
            if target is None:
                continue
            targets += 1
            target_text, target_tags = _project(target)
            assert validate_target_tags(
                source_text, source_tags, target_text, target_tags) == [], (
                    path.name, tu.get("id"))
    assert (units, sources, targets) == (193, 193, 167)


def test_trans_unit_scan_counts_only_direct_source_children():
    """P3-2b 误判 3：只取 trans-unit 直接子节点；嵌套源文单独计数。"""
    from folith.interop.tu_contract import scan_trans_units

    expected = {
        "document.mqxliff": (16, 16, {"historical-unit": 19}),
        "locked.mqxliff": (3, 3, {}),
        "mt.mqxliff": (51, 51, {"historical-unit": 65, "insertedmatch": 51}),
        "pe.mqxliff": (51, 51, {"historical-unit": 135, "insertedmatch": 120}),
        "translation_status.mqxliff": (12, 12,
                                       {"historical-unit": 11, "insertedmatch": 2}),
        "QuotesSample.docx.sdlxliff": (5, 5, {}),
        "SamplePhotoPrinter.doc.sdlxliff": (46, 46, {}),
        "TTSExample.docx.sdlxliff": (4, 4, {}),
        "quickplace.docx.sdlxliff": (5, 5, {}),
    }
    for path in _corpus_files():
        scan = scan_trans_units(ET.parse(path).getroot())
        unit_count, source_count, nested = expected[path.name]
        assert scan.unit_count == unit_count, path.name
        assert len([u for u in scan.units if u.source is not None]) == source_count
        assert dict(scan.nested_sources) == nested, (path.name, scan.nested_sources)
        assert scan.units_without_source == (), path.name
        # 嵌套源文必须出现在报告文本里，不能静默丢弃。
        if nested:
            summary = scan.nested_summary()
            assert f"跳过 {scan.nested_source_total} 个嵌套 <source>" in summary
            for name in nested:
                assert name in summary


def test_flat_source_scan_would_overcount_current_units():
    """反证：平铺 ``.//source`` 的 4 个文件会多算。"""
    from folith.interop.tu_contract import scan_trans_units

    expected_flat = {
        "document.mqxliff": 35,
        "locked.mqxliff": 3,
        "mt.mqxliff": 167,
        "pe.mqxliff": 306,
        "translation_status.mqxliff": 25,
        "QuotesSample.docx.sdlxliff": 5,
        "SamplePhotoPrinter.doc.sdlxliff": 46,
        "TTSExample.docx.sdlxliff": 4,
        "quickplace.docx.sdlxliff": 5,
    }
    mismatched = 0
    for path in _corpus_files():
        root = ET.parse(path).getroot()
        flat = sum(1 for e in root.iter() if _local(e.tag) == "source")
        scan = scan_trans_units(root)
        assert flat == expected_flat[path.name], path.name
        if flat != scan.unit_count:
            mismatched += 1
        assert flat == scan.unit_count + scan.nested_source_total, path.name
    assert mismatched == 4


def test_scan_keeps_historical_and_insertedmatch_separate_in_synthetic_tree():
    """负向：嵌套容器绝不会被当成当前段落，但必须被计数并报告。"""
    from folith.interop.tu_contract import scan_trans_units

    root = ET.fromstring(
        "<xliff><file><body>"
        "<trans-unit id='7'><source>current</source><target>t</target>"
        "<minorversions><historical-unit><source>old</source></historical-unit>"
        "</minorversions>"
        "<insertedmatch><source>tm</source><target>tt</target></insertedmatch>"
        "</trans-unit>"
        "<trans-unit id='8'><source>second</source></trans-unit>"
        "</body></file></xliff>")
    scan = scan_trans_units(root)
    assert scan.unit_count == 2
    assert [u.unit_id for u in scan.units] == ["7", "8"]
    assert scan.units[0].source.text == "current"
    assert scan.units[1].source.text == "second"
    assert dict(scan.units[0].nested_sources) == {
        "historical-unit": 1, "insertedmatch": 1}
    assert dict(scan.nested_sources) == {"historical-unit": 1, "insertedmatch": 1}
    assert scan.nested_source_total == 2
    assert "跳过 2 个嵌套 <source>" in scan.nested_summary()
