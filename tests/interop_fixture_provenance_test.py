"""Keep the documented fixture digests tied to the committed bytes.

The fixture READMEs record two 64-hex digests per file: the committed blob and
the upstream original (they differ for the two samples that were normalised from
CRLF to LF on import). This test only owns the first property -- that whatever
the tables say, the committed bytes are among the recorded digests -- so it stays
correct when a column is added. The precise meaning of each column is asserted by
tests/fixture_provenance_test.py, which also recomputes the upstream digest from
the blob.
"""

import hashlib
import re

import pytest

from scripts.release_preflight import ROOT, _ref_blob, _ref_files

_DIGEST = re.compile(r"`([a-f0-9]{64})`")
_ROW = re.compile(r"^\| `([^`]+\.(?:sdlxliff|mqxliff|xlf))` \|")
#: A row whose digests are abbreviated ("`d9fc637c…`") is a narrative summary, not
#: a record: SOURCES.md keeps one to show what line-ending normalisation did. Only
#: rows that state the full 64 hex characters are treated as records.
_ABBREVIATED = re.compile(r"`[a-f0-9]{4,}\.\.\.?`|`[a-f0-9]{4,}…`")


@pytest.mark.parametrize(
    ("directory", "extension"),
    [
        ("tests/fixtures/sdlxliff", "sdlxliff"),
        ("tests/fixtures/mqxliff", "mqxliff"),
        ("tests/fixtures/xliff12", "xlf"),
    ],
)
def test_documented_fixture_digests_match_committed_blobs(directory, extension):
    paths = [
        path for path in _ref_files("HEAD")
        if path.startswith(f"{directory}/") and path.endswith(f".{extension}")
    ]
    assert paths, f"No committed {extension} fixtures"

    digests = {}
    for path in paths:
        blob = _ref_blob("HEAD", path)
        assert blob is not None, path
        digests[path.rsplit("/", 1)[-1]] = hashlib.sha256(blob).hexdigest()

    for document in (ROOT / "SOURCES.md", ROOT / directory / "README.md"):
        documented: dict[str, list[str]] = {}
        for line in document.read_text(encoding="utf-8").splitlines():
            match = _ROW.match(line)
            if not match or not match.group(1).endswith(f".{extension}"):
                continue
            if match.group(1) not in digests:
                # Documented but deliberately not committed (the GPL-3.0 audit
                # sample): there is no blob to tie it to.
                continue
            if _ABBREVIATED.search(line):
                continue
            recorded = _DIGEST.findall(line)
            assert recorded, f"{document}: no digest recorded for {match.group(1)}"
            documented[match.group(1)] = recorded
        assert set(documented) == set(digests), document
        for name, computed in digests.items():
            assert computed in documented[name], (
                f"{document}: committed blob of {name} is {computed}, "
                f"but the row only records {documented[name]}")
