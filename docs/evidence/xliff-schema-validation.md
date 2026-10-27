# Independent XLIFF schema validation

On 2026-10-27, the two minimal XLIFF 1.2 and 2.0 samples in
`scripts/validate_xliff_schema.py` and their Folith-edited exports were checked
with `lxml.etree.XMLSchema`. Both source and modified output passed in each
version. This is a schema check of two synthetic files, not a CAT client
round-trip or evidence of coverage for every inline tag or vendor extension.

For reproducibility, download these files into one directory with the names
shown, then run `python scripts/validate_xliff_schema.py <directory>`:

| Local name | Source | SHA-256 |
| --- | --- | --- |
| `xliff-1.2.xsd` | [OASIS XLIFF 1.2 strict schema](https://docs.oasis-open.org/xliff/v1.2/os/xliff-core-1.2-strict.xsd) | `9f37af564ecd27d5f69154d7cfe7ba8deb1f620c81b94cf0eab1f4205c74d5f8` |
| `xliff-2.0.xsd` | [OASIS XLIFF 2.0 core schema](https://docs.oasis-open.org/xliff/xliff-core/v2.0/os/schemas/xliff_core_2.0.xsd) | `fb51193694c534b6e0483fefde138488c1e6069ec569e08284434cbeb175a2e4` |
| `xml.xsd` | [W3C XML namespace schema](https://www.w3.org/2001/xml.xsd) | `61960fb3131e38022caad5360e2f33a3382578ab3c80cd58bd74320ede61b20c` |

Native Windows and macOS responsiveness for 1,000 units and real CAT-client
reopening remain separate acceptance work.
