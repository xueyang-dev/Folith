"""Keep the documented fixture digests tied to the committed bytes."""

import hashlib
import re

import pytest

from scripts.release_preflight import ROOT, _ref_blob, _ref_files


@pytest.mark.parametrize("extension", ["sdlxliff", "mqxliff"])
def test_documented_fixture_digests_match_committed_blobs(extension):
    directory = f"tests/fixtures/{extension}"
    paths = [
        path for path in _ref_files("HEAD")
        if path.startswith(f"{directory}/") and path.endswith(f".{extension}")
    ]
    assert paths, f"No committed {extension} fixtures"

    digests = {}
    for path in paths:
        blob = _ref_blob("HEAD", path)
        assert blob is not None, path
        name = path.rsplit("/", 1)[-1]
        digests[name] = hashlib.sha256(blob).hexdigest()

    for document in (ROOT / "SOURCES.md", ROOT / directory / "README.md"):
        rows = document.read_text(encoding="utf-8")
        documented = dict(re.findall(
            rf"^\| `([^`]+\.{extension})` \|.* \| `([a-f0-9]{{64}})` \|$",
            rows, re.MULTILINE,
        ))
        assert documented == digests, document
