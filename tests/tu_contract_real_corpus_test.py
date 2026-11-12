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
