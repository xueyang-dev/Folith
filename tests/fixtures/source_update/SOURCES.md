# Source Update Fixtures Provenance and Specification

## Provenance
All fixtures in this directory are synthetic and created specifically for Folith Issue #152:
`[Project] Source update and incremental re-translation via TM alignment`.

No third-party copyrighted texts, confidential documents, or external proprietary materials are used.

## Classification Categories
1. `unchanged`: Paragraph text is identical or typographically equivalent under `tm_normalize`.
2. `changed`: Paragraph is modified in place (minor edit, similarity >= threshold) without ambiguity.
3. `added`: Paragraph is newly introduced in v2.
4. `deleted`: Paragraph existed in v1 but is absent in v2.
5. `suspected_changed`: Paragraph alignment lacks certainty (e.g. moved segment, relocated structural position, or ambiguous similarity). Must be confirmed by human before execution.
