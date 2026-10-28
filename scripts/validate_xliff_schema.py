"""Validate two Folith exports against externally supplied OASIS schemas.

Download the 1.2 strict, 2.0 core and W3C XML schemas described in
docs/evidence/xliff-schema-validation.md, then pass their directory here.
This probe is offline and does not change a task or contact a provider.
"""
from pathlib import Path
import sys

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from folith.xliff_roundtrip import extract_xliff_units, write_roundtrip_xliff

schema_dir = Path(sys.argv[1]).resolve()


class Resolver(etree.Resolver):
    def resolve(self, url, public_id, context):
        if url.endswith("xml.xsd"):
            return self.resolve_filename(str(schema_dir / "xml.xsd"), context)
        if Path(url).resolve().parent == schema_dir:
            return None
        raise ValueError(f"Unexpected schema dependency: {url}")


parser = etree.XMLParser(no_network=True)
parser.resolvers.add(Resolver())
sources = {
    "1.2": b'<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2" version="1.2"><file original="a.txt" source-language="en" target-language="zh-CN" datatype="plaintext"><body><trans-unit id="a"><source>Hello world.</source><target>Old.</target></trans-unit></body></file></xliff>',
    "2.0": b'<xliff xmlns="urn:oasis:names:tc:xliff:document:2.0" version="2.0" srcLang="en" trgLang="zh-CN"><file id="f1"><unit id="u1"><segment id="s1"><source>Hello world.</source><target>Old.</target></segment></unit></file></xliff>',
}
for version, source in sources.items():
    schema = etree.XMLSchema(etree.parse(str(schema_dir / f"xliff-{version}.xsd"), parser))
    schema.assertValid(etree.fromstring(source))
    unit = extract_xliff_units(source)[0][0]
    unit.target = "New target."
    output, _report = write_roundtrip_xliff(source, [unit])
    schema.assertValid(etree.fromstring(output))
    print(f"{version}: source and modified output satisfy OASIS schema")
