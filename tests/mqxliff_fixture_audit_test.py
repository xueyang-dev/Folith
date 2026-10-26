"""Issue #146 Gate A 看门测试:入库的真实 MQXLIFF 样本结构必须稳定。

这些样本来自 MIT 许可的公开仓库,是真实 memoQ(7.8–11.6)导出,是将来
Gate B(受限 MQXLIFF round trip,依赖 #144)要依据的证据基线。本测试只读
校验样本的关键结构事实与 docs/mqxliff-gate-a-compatibility-matrix.md 的记载
一致:样本被意外替换或损坏时先在这里失败。不做任何导入/写回,不承诺格式支持。

运行:python tests/mqxliff_fixture_audit_test.py
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "mqxliff"
XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
# memoQ 用字面量 "MQXliff" 作命名空间 URI(不是 URL,大小写混合,矩阵 §2.1)。
MQ_NS = "MQXliff"


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _mq(tag: str) -> str:
    return f"{{{MQ_NS}}}{tag}"


def audit_common(root: ET.Element, name: str) -> None:
    _check(root.tag == f"{{{XLIFF_NS}}}xliff", f"{name}: 根元素必须是 XLIFF 1.2 xliff")
    _check(root.get("version") == "1.2", f"{name}: xliff version 必须是 1.2")
    _check(
        root.find(f".//{{{XLIFF_NS}}}tool") is not None
        and root.find(f".//{{{XLIFF_NS}}}tool").get("tool-id") == "MQ",
        f"{name}: header/tool 必须 tool-id=MQ(memoQ 导出判据)",
    )
    # 方言核心事实:每 trans-unit 即一段,无 seg-source/mrk 拆分(矩阵 §2.2)
    _check(
        not root.findall(f".//{{{XLIFF_NS}}}seg-source")
        and not root.findall(f".//{{{XLIFF_NS}}}mrk"),
        f"{name}: 不应出现 seg-source/mrk(memoQ 段模型)",
    )
    # 命名空间陷阱:容器元素上重复声明默认命名空间 xmlns="MQXliff"(矩阵 §2.5)
    raw = (FIXTURES / name).read_text(encoding="utf-8-sig")
    _check(
        'xmlns:mq="MQXliff"' in raw,
        f"{name}: 根部应声明 xmlns:mq=\"MQXliff\"",
    )
    _check(
        re.search(r'xmlns="MQXliff"', raw) is not None,
        f"{name}: 应存在默认命名空间 xmlns=\"MQXliff\" 重声明",
    )


def audit_document() -> None:
    name = "document.mqxliff"
    root = ET.parse(FIXTURES / name).getroot()
    audit_common(root, name)

    status = {
        tu.get(_mq("status"))
        for tu in root.iter(f"{{{XLIFF_NS}}}trans-unit")
        if tu.get(_mq("status"))
    }
    _check(status == {"ManuallyConfirmed"}, f"{name}: 状态值域变化:{sorted(status)}")

    # TM 溯源 mq:insertedmatch(矩阵 P14)
    matches = list(root.iter(_mq("insertedmatch")))
    _check(len(matches) >= 13, f"{name}: insertedmatch 数应 ≥13,实际 {len(matches)}")
    _check(
        any(m.get("matchrate") == "101" and m.get("source") for m in matches),
        f"{name}: insertedmatch 应含 CM 匹配(matchrate=101)与 source 溯源",
    )

    # QA 警告载体:7.8 时代的带版本元素名 mq:warnings40(矩阵 P13)
    warnings = list(root.iter(_mq("warnings40")))
    _check(len(warnings) == 2, f"{name}: warnings40 数应为 2,实际 {len(warnings)}")
    errs = [e for w in warnings for e in w.findall(_mq("errorwarning"))]
    _check(len(errs) == 3, f"{name}: errorwarning 数应为 3,实际 {len(errs)}")

    # mq:details 内嵌 CDATA 包裹的不透明 XML(矩阵 P12)
    details = [d for d in root.iter(_mq("details")) if d.text]
    _check(details, f"{name}: 应存在带文本的 mq:details")
    _check(
        all(d.text.strip().startswith("<?xml") for d in details),
        f"{name}: mq:details 文本应为内嵌 XML(CDATA)",
    )


def audit_locked() -> None:
    name = "locked.mqxliff"
    root = ET.parse(FIXTURES / name).getroot()
    audit_common(root, name)

    tus = list(root.iter(f"{{{XLIFF_NS}}}trans-unit"))
    _check(len(tus) == 3, f"{name}: trans-unit 数应为 3,实际 {len(tus)}")

    # 锁定标记是 presence-as-value:值 = 本地名 "locked",不是 "true"(矩阵 M6)
    locked = [tu for tu in tus if tu.get(_mq("locked")) == "locked"]
    _check(len(locked) == 1, f"{name}: locked 段数应为 1,实际 {len(locked)}")
    # 锁定段同时写标准 translate="no"(矩阵 M6)
    _check(
        all(tu.get("translate") == "no" for tu in locked),
        f"{name}: 锁定段应同时带 translate=\"no\"",
    )
    # 审读态存在(Proofread),与 mq:segmentproofread 同点标记(矩阵 M3)
    proofread = [tu for tu in tus if tu.get(_mq("segmentproofread")) == "segmentproofread"]
    _check(len(proofread) == 1, f"{name}: segmentproofread 段数应为 1,实际 {len(proofread)}")


def audit_status_domain() -> None:
    """translation_status.mqxliff:一次导出覆盖全部六种观测状态(矩阵 §2.4)。"""
    name = "translation_status.mqxliff"
    root = ET.parse(FIXTURES / name).getroot()
    audit_common(root, name)

    tus = list(root.iter(f"{{{XLIFF_NS}}}trans-unit"))
    _check(len(tus) == 12, f"{name}: trans-unit 数应为 12,实际 {len(tus)}")
    expected = {
        "NotStarted", "PartiallyEdited", "PreTranslated",
        "ManuallyConfirmed", "Reviewer1Confirmed", "Proofread",
    }
    status: dict[str, int] = {}
    for tu in tus:
        v = tu.get(_mq("status"))
        status[v] = status.get(v, 0) + 1
    _check(
        set(status) == expected,
        f"{name}: 状态值域变化:观测 {sorted(status)} 预期 {sorted(expected)}",
    )
    _check(
        all(count == 2 for count in status.values()),
        f"{name}: 各状态应各 2 段,实际 {status}",
    )

    # 注释双表示:标准 <note> 与 mq:comment 同时存在(矩阵 §2.3)
    notes = list(root.iter(f"{{{XLIFF_NS}}}note"))
    comments = list(root.iter(_mq("comment")))
    _check(len(notes) == 23 and len(comments) == 23, f"{name}: note/comment 计数变化:notes={len(notes)} comments={len(comments)}")

    # 历史版本 mq:minorversions/mq:historical-unit(矩阵 P10)
    _check(
        len(list(root.iter(_mq("historical-unit")))) == 11,
        f"{name}: historical-unit 数应为 11",
    )


def audit_mt_pe_pair() -> None:
    """mt/pe 是同一文档的两次导出:段身份与标签结构一致,状态不同(矩阵 §2.4)。"""
    mt = ET.parse(FIXTURES / "mt.mqxliff").getroot()
    pe = ET.parse(FIXTURES / "pe.mqxliff").getroot()
    for name, root, expected in (
        ("mt.mqxliff", mt, {"MachineTranslated": 36, "PreTranslated": 15}),
        ("pe.mqxliff", pe, {"ManuallyConfirmed": 51}),
    ):
        audit_common(root, name)
        tus = list(root.iter(f"{{{XLIFF_NS}}}trans-unit"))
        _check(len(tus) == 51, f"{name}: trans-unit 数应为 51,实际 {len(tus)}")
        status: dict[str, int] = {}
        for tu in tus:
            v = tu.get(_mq("status"))
            status[v] = status.get(v, 0) + 1
        _check(status == expected, f"{name}: 状态分布变化:{status} 预期 {expected}")
        # 内联标签用标准 XLIFF bpt/ept/ph,非 mq: 扩展(矩阵 §2.2)
        inline = sum(
            len(list(root.iter(f"{{{XLIFF_NS}}}{tag}")))
            for tag in ("bpt", "ept", "ph")
        )
        _check(inline > 0, f"{name}: 应含标准内联标签")
    mt_guids = {tu.get(_mq("segmentguid")) for tu in mt.iter(f"{{{XLIFF_NS}}}trans-unit")}
    pe_guids = {tu.get(_mq("segmentguid")) for tu in pe.iter(f"{{{XLIFF_NS}}}trans-unit")}
    # TU 14 在 pe 中被改写后 segmentguid 变了(源文本相同)——segmentguid 不是
    # 跨导出稳定身份,round-trip 不得以它作为唯一主键(矩阵 §2.2)。
    _check(
        len(mt_guids & pe_guids) == 50 and len(mt_guids) == 51 and len(pe_guids) == 51,
        f"mt/pe 段身份应 51 段对应、1 段 guid 变更,实际交集 {len(mt_guids & pe_guids)}",
    )


def main() -> int:
    _check(FIXTURES.is_dir(), "缺少 MQXLIFF fixtures 目录")
    audit_document()
    audit_locked()
    audit_status_domain()
    audit_mt_pe_pair()
    print("mqxliff fixture audit: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
