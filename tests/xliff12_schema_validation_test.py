"""P3-6：用 OASIS XLIFF 1.2 的 strict / transitional XSD 校验内核的原样导出。

通过条件不是「原文件必须合法」。原文件本来就可能过不了 strict。通过条件是：
原样导出没有带来新的校验错误。方言 no-op 由内核返回原字节，所以两边的错误
清单必须相同。

schema 来源和许可见 ``tests/fixtures/xliff12/schema/README.md``。
"""
from __future__ import annotations

import os
from io import BytesIO

import pytest
from lxml import etree

from folith.interop.xliff12 import parse_xliff12
from folith.interop.xliff12_dialect import MQXLIFF, SDLXLIFF, export_unchanged

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures")
SCHEMA_DIR = os.path.join(FIXTURE, "xliff12", "schema")
XML_XSD = "http://www.w3.org/2001/xml.xsd"

# 从 validators.en.xlf 里读出来、再对照导出的 5 个单元。
_FIVE_UNITS = (
    ("1", "This value should be false.", "This value should be false."),
    ("2", "This value should be true.", "This value should be true."),
    ("3", "This value should be of type {{ type }}.",
     "This value should be of type {{ type }}."),
    ("4", "This value should be blank.", "This value should be blank."),
    ("5", "The value you selected is not a valid choice.",
     "The value you selected is not a valid choice."),
)


class _LocalXmlNamespace(etree.Resolver):
    def resolve(self, url, pubid, context):
        if url == XML_XSD:
            return self.resolve_filename(os.path.join(SCHEMA_DIR, "xml.xsd"), context)
        if url.startswith(("http://", "https://")):
            raise AssertionError(f"schema 校验试图访问网络：{url}")
        return None


def _load_schema(filename: str) -> etree.XMLSchema:
    parser = etree.XMLParser(no_network=True)
    parser.resolvers.add(_LocalXmlNamespace())
    document = etree.parse(os.path.join(SCHEMA_DIR, filename), parser)
    return etree.XMLSchema(document)


STRICT = _load_schema("xliff-core-1.2-strict.xsd")
TRANSITIONAL = _load_schema("xliff-core-1.2-transitional.xsd")
SCHEMAS = (("strict", STRICT), ("transitional", TRANSITIONAL))


def _issues(schema: etree.XMLSchema, data: bytes) -> list[tuple[int, int, str]]:
    schema.validate(etree.parse(BytesIO(data)))
    return [(item.line, item.column, item.message) for item in schema.error_log]


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _generic_paths() -> list[str]:
    directory = os.path.join(FIXTURE, "xliff12")
    return [
        os.path.join(directory, name)
        for name in (
            "validators.en.xlf",
            "messages.fr.xlf",
            "test1_es.xlf",
            "manual-alttrans.xlf",
        )
    ]


def _dialect_cases() -> list[tuple[object, str, str]]:
    found = []
    for config, folder, extension in (
        (SDLXLIFF, "sdlxliff", ".sdlxliff"),
        (MQXLIFF, "mqxliff", ".mqxliff"),
    ):
        directory = os.path.join(FIXTURE, folder)
        names = sorted(name for name in os.listdir(directory) if name.endswith(extension))
        assert names
        found.extend(
            (config, os.path.join(directory, name), name) for name in names
        )
    return found


@pytest.mark.parametrize("path", _generic_paths(), ids=lambda path: os.path.basename(path))
@pytest.mark.parametrize("schema_name,schema", SCHEMAS, ids=["strict", "transitional"])
def test_generic_noop_export_adds_no_schema_errors(path, schema_name, schema):
    original = _read(path)
    exported = parse_xliff12(original).render()
    assert exported == original
    before = _issues(schema, original)
    after = _issues(schema, exported)
    assert after == before, schema_name


@pytest.mark.parametrize(
    "config,path,label",
    _dialect_cases(),
    ids=[item[2] for item in _dialect_cases()],
)
@pytest.mark.parametrize("schema_name,schema", SCHEMAS, ids=["strict", "transitional"])
def test_dialect_noop_export_adds_no_schema_errors(config, path, label, schema_name, schema):
    original = _read(path)
    exported = export_unchanged(config, original)
    assert exported == original
    before = _issues(schema, original)
    after = _issues(schema, exported)
    assert after == before, (label, schema_name)


def test_strict_schema_is_not_vacuous_and_transitional_accepts_the_clean_file():
    """校验器要真的在判。干净样本两边都过；带外来标记的样本 strict 不过。"""
    clean = _read(os.path.join(FIXTURE, "xliff12", "messages.fr.xlf"))
    extended = _read(os.path.join(FIXTURE, "xliff12", "test1_es.xlf"))
    assert _issues(STRICT, clean) == []
    assert _issues(TRANSITIONAL, clean) == []
    assert _issues(STRICT, extended)
    assert _issues(TRANSITIONAL, extended) == []


def test_five_units_match_the_source_file_after_noop_export():
    path = os.path.join(FIXTURE, "xliff12", "validators.en.xlf")
    original = parse_xliff12(_read(path))
    exported = parse_xliff12(original.render())
    for unit_id, source_text, target_text in _FIVE_UNITS:
        before = next(unit for unit in original.units if unit.unit_id == unit_id)
        after = next(unit for unit in exported.units if unit.unit_id == unit_id)
        assert before.source.text == source_text
        assert before.target.text == target_text
        assert after.source.text == source_text
        assert after.target.text == target_text
        assert after.external_id == before.external_id
