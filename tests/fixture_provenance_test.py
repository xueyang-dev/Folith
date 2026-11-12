"""样本溯源看门测试：README 记录的 SHA-256 必须等于入库 blob 的实际哈希。

背景（Issue #145/#146 Gate A）：`tests/fixtures/sdlxliff/` 与
`tests/fixtures/mqxliff/` 下的真实样本是将来 round-trip 验收的证据基线。
README 表格里的哈希一旦与提交内的字节脱节，"字节级 round trip / 结构 diff
为空"的结论就失去意义，而 `docs/*-gate-a-compatibility-matrix.md` 也会带着
错误的证据发布。

因此这里做两件事：

1. **blob 看门**：逐文件解析 README 表格里记录的"入库 blob SHA-256"，与
   `hashlib.sha256(blob)` 比对。改一个样本字节、替换样本或改记录，都会红。
2. **上游口径**：README 同时记录"上游原件 SHA-256"。两个上游原件是 CRLF、
   入库 blob 是 LF 的样本（`SamplePhotoPrinter.doc.sdlxliff`、
   `document.mqxliff`），把 blob 的 LF 程序化还原成 CRLF 后必须复算出上游
   哈希——即"差异只有换行"是**可复算的事实**，不是一句声明。

不做任何导入/写回，不联网（哈希来自 README 记录，上游比对在评审时人工完成，
证据写在 README 的"换行口径"段落与本文件常量里）。

运行：python -m pytest tests/fixture_provenance_test.py -q
或直接跑两个方言审计文件（它们也接入了同一套看门）。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SDL_DIR = ROOT / "tests" / "fixtures" / "sdlxliff"
MQ_DIR = ROOT / "tests" / "fixtures" / "mqxliff"

# 上游原件是 CRLF、入库 blob 是 LF 的两个样本。
# 值 = (上游原件字节数, 入库 blob 字节数)，来自 2026-11-12 对固定 commit 的核对。
CRLF_NORMALIZED: dict[str, tuple[int, int]] = {
    "SamplePhotoPrinter.doc.sdlxliff": (42996, 42698),
    "document.mqxliff": (71684, 71199),
}

_HEX64 = re.compile(r"[0-9a-f]{64}")
_SEPARATOR = re.compile(r":?-{2,}:?")


def _split_row(line: str) -> list[str] | None:
    """把一行 Markdown 表格拆成单元格；不是表格行则返回 None。"""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    if not stripped.endswith("|"):
        stripped += "|"
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def readme_records(directory: Path) -> dict[str, dict[str, str]]:
    """解析 README 表格，返回 {文件名: {"blob": ..., "upstream": ...}}。

    只接受列名同时含 "SHA-256" 与 "入库"/"上游" 的列；这样"仅审计未入库"
    参考样本表不会被误当成入库记录。记录缺"上游原件"列时，按"上游与入库
    逐字节相同"的默认语义取 blob 值（其余 7 个样本正是如此）。
    """
    readme = directory / "README.md"
    records: dict[str, dict[str, str]] = {}
    header: list[str] | None = None
    for line in readme.read_text(encoding="utf-8").splitlines():
        cells = _split_row(line)
        if cells is None:
            header = None
            continue
        if header is None:
            header = cells
            continue
        if all(_SEPARATOR.fullmatch(cell) for cell in cells if cell):
            continue
        if len(cells) != len(header):
            continue
        name = cells[0].strip("`").strip()
        if not name.endswith((".sdlxliff", ".mqxliff")):
            continue
        blob = upstream = None
        for key, value in zip(header, cells):
            if "SHA-256" not in key:
                continue
            digest = value.strip("`").strip()
            if not _HEX64.fullmatch(digest):
                continue
            if "入库" in key:
                blob = digest
            elif "上游" in key:
                upstream = digest
        if blob is None:
            continue
        records[name] = {"blob": blob, "upstream": upstream or blob}
    return records


def assert_blob_digest(path: Path, recorded: str) -> None:
    """README 记录值必须等于 blob 实际哈希；失败信息直接给出三方信息。"""
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != recorded:
        raise AssertionError(
            f"{path.name}: README 记录的入库 SHA-256 与 blob 不符\n"
            f"  README 记录: {recorded}\n"
            f"  blob 实际:   {actual}\n"
            f"  文件:        {path}\n"
            f"  字节数:      {path.stat().st_size}"
        )


def assert_upstream_digest(path: Path, blob_recorded: str, upstream_recorded: str) -> None:
    """校验上游口径：只有换行差异的样本必须能由 blob 复算出上游哈希。"""
    data = path.read_bytes()
    actual_blob = hashlib.sha256(data).hexdigest()
    if actual_blob != blob_recorded:
        raise AssertionError(
            f"{path.name}: 入库 blob 哈希与 README 记录不符（上游校验前先失败）\n"
            f"  README 记录: {blob_recorded}\n"
            f"  blob 实际:   {actual_blob}\n"
            f"  文件:        {path}"
        )
    if upstream_recorded == blob_recorded:
        # 未做换行规范化的样本：上游原件与入库 blob 应逐字节相同。
        if b"\r" in data:
            raise AssertionError(
                f"{path.name}: README 声称上游与入库相同，但 blob 含 CR 字节"
            )
        return

    expected_sizes = CRLF_NORMALIZED.get(path.name)
    if expected_sizes is None:
        raise AssertionError(
            f"{path.name}: README 记录了不同的上游哈希，但未在 CRLF_NORMALIZED 中登记"
        )
    upstream_size, blob_size = expected_sizes
    if len(data) != blob_size:
        raise AssertionError(
            f"{path.name}: 入库 blob 字节数变化：预期 {blob_size}，实际 {len(data)}"
        )
    if b"\r" in data:
        raise AssertionError(f"{path.name}: 入库 blob 应已规范化为 LF，但含 CR 字节")

    restored = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if len(restored) != upstream_size:
        raise AssertionError(
            f"{path.name}: LF→CRLF 还原后字节数变化：预期 {upstream_size}，"
            f"实际 {len(restored)}"
        )
    newline_count = data.count(b"\n")
    if len(restored) - len(data) != newline_count:
        raise AssertionError(
            f"{path.name}: 还原增量应等于换行数 {newline_count}，"
            f"实际 {len(restored) - len(data)}"
        )
    actual_upstream = hashlib.sha256(restored).hexdigest()
    if actual_upstream != upstream_recorded:
        raise AssertionError(
            f"{path.name}: 由入库 blob 还原的上游 SHA-256 与 README 记录不符\n"
            f"  README 记录: {upstream_recorded}\n"
            f"  还原后实际:  {actual_upstream}\n"
            f"  还原方式:    把 {newline_count} 个 LF 换回 CRLF"
        )
    if restored.replace(b"\r\n", b"\n") != data:
        raise AssertionError(
            f"{path.name}: 还原只应改变换行，解码文本出现了其他差异"
        )


def audit_readme_digests(directory: Path) -> None:
    """供两个方言审计文件复用：README 覆盖、blob、上游口径三项一次跑完。"""
    records = readme_records(directory)
    fixtures = {path.name: path for path in directory.glob("*.*xliff")}
    if set(records) != set(fixtures):
        raise AssertionError(
            f"{directory.name}: README 表格与目录内样本不一致\n"
            f"  仅在 README: {sorted(set(records) - set(fixtures))}\n"
            f"  仅在目录:    {sorted(set(fixtures) - set(records))}"
        )
    for name in sorted(fixtures):
        record = records[name]
        assert_blob_digest(fixtures[name], record["blob"])
        assert_upstream_digest(fixtures[name], record["blob"], record["upstream"])


def _cases(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.glob("*.*xliff"))


@pytest.mark.parametrize("name", _cases(SDL_DIR), ids=_cases(SDL_DIR))
def test_sdlxliff_blob_digest_matches_readme(name: str) -> None:
    record = readme_records(SDL_DIR)[name]
    assert_blob_digest(SDL_DIR / name, record["blob"])


@pytest.mark.parametrize("name", _cases(MQ_DIR), ids=_cases(MQ_DIR))
def test_mqxliff_blob_digest_matches_readme(name: str) -> None:
    record = readme_records(MQ_DIR)[name]
    assert_blob_digest(MQ_DIR / name, record["blob"])


@pytest.mark.parametrize("name", _cases(SDL_DIR), ids=_cases(SDL_DIR))
def test_sdlxliff_upstream_digest_recomputable(name: str) -> None:
    record = readme_records(SDL_DIR)[name]
    assert_upstream_digest(SDL_DIR / name, record["blob"], record["upstream"])


@pytest.mark.parametrize("name", _cases(MQ_DIR), ids=_cases(MQ_DIR))
def test_mqxliff_upstream_digest_recomputable(name: str) -> None:
    record = readme_records(MQ_DIR)[name]
    assert_upstream_digest(MQ_DIR / name, record["blob"], record["upstream"])


@pytest.mark.parametrize(
    "directory", [SDL_DIR, MQ_DIR], ids=["sdlxliff", "mqxliff"]
)
def test_readme_covers_exactly_the_committed_fixtures(directory: Path) -> None:
    records = set(readme_records(directory))
    fixtures = {path.name for path in directory.glob("*.*xliff")}
    assert records == fixtures, (
        f"{directory.name}: README 与目录样本集合不一致；"
        f"仅在 README {sorted(records - fixtures)}，仅在目录 {sorted(fixtures - records)}"
    )


@pytest.mark.parametrize(
    "directory", [SDL_DIR, MQ_DIR], ids=["sdlxliff", "mqxliff"]
)
def test_readme_records_hex_digests(directory: Path) -> None:
    for name, record in readme_records(directory).items():
        for key in ("blob", "upstream"):
            assert _HEX64.fullmatch(record[key]), f"{name}: {key} 不是 64 位十六进制"


@pytest.mark.parametrize(
    "source",
    [SDL_DIR / "SamplePhotoPrinter.doc.sdlxliff", MQ_DIR / "document.mqxliff"],
    ids=["sdlxliff", "mqxliff"],
)
def test_single_byte_mutation_is_detected(tmp_path: Path, source: Path) -> None:
    """变异自检：改 1 个字节后看门必须红（防止比对逻辑退化成恒真）。"""
    mutated = tmp_path / source.name
    data = bytearray(source.read_bytes())
    data[len(data) // 2] ^= 0x01
    mutated.write_bytes(bytes(data))
    recorded = readme_records(source.parent)[source.name]["blob"]
    with pytest.raises(AssertionError, match="SHA-256"):
        assert_blob_digest(mutated, recorded)
