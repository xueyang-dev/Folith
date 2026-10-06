"""D1 build/runtime diagnostics and redacted export (roadmap §3.5)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folith.desktop import diagnostics  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402


@pytest.fixture()
def tmp_paths(tmp_path, monkeypatch):
    data = tmp_path / "data"
    logs = tmp_path / "logs"
    monkeypatch.setenv(desktop_paths.DATA_DIR_ENV, str(data))
    monkeypatch.setenv(desktop_paths.LOG_DIR_ENV, str(logs))
    return desktop_paths.resolve_paths()


def _job(data_dir: Path, job: str, *, runtime: dict | None = None,
         drafts: dict | None = None, journal: list[dict] | None = None) -> Path:
    path = data_dir / job
    path.mkdir(parents=True, exist_ok=True)
    if runtime is not None:
        (path / "runtime_state.json").write_text(json.dumps(runtime), encoding="utf-8")
    if drafts is not None:
        (path / "translation_drafts.json").write_text(json.dumps(drafts), encoding="utf-8")
    if journal is not None:
        (path / "state_journal.jsonl").write_text(
            "\n".join(json.dumps(item) for item in journal) + "\n", encoding="utf-8")
    return path


def test_collect_reports_the_roadmap_facts(tmp_paths):
    facts = diagnostics.collect(paths=tmp_paths)
    assert set(facts) == {"app", "runtime", "system", "locations", "data_schema"}
    app = facts["app"]
    assert app["version"] and app["build_id"]
    assert app["packaging_backend"]
    assert isinstance(app["frozen"], bool)
    runtime = facts["runtime"]
    for key in ("python_version", "streamlit_version", "pywebview_version"):
        assert runtime[key], key
    system = facts["system"]
    assert system["os"] and system["os_version"] and system["architecture"]
    locations = facts["locations"]
    # Under the home directory paths collapse to `~`; elsewhere the user name is
    # removed instead of leaking an account/project name.
    assert locations["data_dir"].startswith(("~", "/"))
    assert Path.home().name not in json.dumps(facts)
    assert str(Path.home()) not in json.dumps(facts)


def test_job_state_is_reported_as_unversioned_with_observed_schemas(tmp_paths):
    _job(tmp_paths.data_dir, "job-a",
         runtime={"schema_version": "2", "status": "completed"},
         drafts={"version": 1, "drafts": []},
         journal=[{"schema": 1, "pairs": []}])
    facts = diagnostics.collect(paths=tmp_paths)
    schema = facts["data_schema"]
    assert schema["job_state"] == "unversioned"
    assert schema["runtime"]["observed"] == "2"
    assert schema["translation_drafts"]["observed"] == 1
    assert schema["journal"]["observed"] == 1
    assert facts["locations"]["job_count"] == 1


def test_observed_schemas_match_the_business_core_constants(tmp_paths):
    """The observed values must agree with the real constants (no drift)."""
    import core

    _job(tmp_paths.data_dir, "job-b",
         runtime={"schema_version": core.RUNTIME_SCHEMA_VERSION},
         drafts={"version": core.TRANSLATION_DRAFTS_VERSION},
         journal=[{"schema": diagnostics._journal_schema_declared()}])
    schema = diagnostics.collect(paths=tmp_paths)["data_schema"]
    assert str(schema["runtime"]["observed"]) == str(core.RUNTIME_SCHEMA_VERSION)
    assert schema["translation_drafts"]["observed"] == core.TRANSLATION_DRAFTS_VERSION
    assert schema["journal"]["observed"] == diagnostics._journal_schema_declared()


def test_error_summary_is_filtered_and_redacted(tmp_paths):
    tmp_paths.log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_paths.launcher_log).write_text(
        f"{Path.home()}/secret-project/launcher started fine\n"
        f"{Path.home()}/secret-project/ERROR boom api_key=sk-live-123\n",
        encoding="utf-8")
    (tmp_paths.service_log).write_text(
        "Traceback (most recent call last):\nnormal line\n", encoding="utf-8")
    lines = diagnostics.collect_error_summary(tmp_paths)
    joined = "\n".join(lines)
    assert "boom" in joined and "Traceback" in joined
    assert "started fine" not in joined
    assert str(Path.home()) not in joined
    assert "~/" in joined, "the home directory must collapse to ~ inside log lines"
    assert "sk-live-123" not in joined


def test_export_bundle_never_includes_secrets_or_document_content(tmp_paths):
    """Plant a credential and document text, then prove neither is exported."""
    tmp_paths.data_dir.mkdir(parents=True, exist_ok=True)
    secret = "sk-D0-NOT-EXPORTED-1234567890"
    (tmp_paths.data_dir / "provider_config.json").write_text(json.dumps({
        "provider": "自定义中转站",
        "api_key": secret,
        "base_url": "http://127.0.0.1:9/v1",
    }), encoding="utf-8")
    job = _job(tmp_paths.data_dir, "job-c", runtime={"schema_version": "2"})
    (job / "source.bin").write_bytes(b"CONFIDENTIAL SOURCE DOCUMENT TEXT")
    (job / "state.json").write_text(json.dumps({
        "pairs": [{"source": "CONFIDENTIAL SOURCE DOCUMENT TEXT", "target": "秘密译文"}],
    }), encoding="utf-8")
    tmp_paths.log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_paths.launcher_log).write_text("exit flow step cancel-task\n", encoding="utf-8")

    target = tmp_paths.log_dir.parent / "diagnostics-export.json"
    diagnostics.export_bundle(target, paths=tmp_paths)
    text = target.read_text(encoding="utf-8")
    payload = json.loads(text)

    assert secret not in text
    assert "CONFIDENTIAL SOURCE DOCUMENT TEXT" not in text
    assert "秘密译文" not in text
    assert "provider_config" not in text
    assert str(Path.home()) not in text
    assert payload["diagnostics"]["data_schema"]["job_state"] == "unversioned"
    assert payload["export_version"] == diagnostics.EXPORT_VERSION


def test_write_diagnostics_creates_the_file(tmp_paths):
    target = diagnostics.write_diagnostics(tmp_paths, pywebview_version="9.9.9")
    assert target == tmp_paths.log_dir / diagnostics.DIAGNOSTICS_FILE
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["diagnostics"]["runtime"]["pywebview_version"] == "9.9.9"


def test_diagnostics_reads_no_forbidden_sources(monkeypatch, tmp_paths):
    """Guard: the diagnostics path must not open provider config or .onboarded."""
    opened: list[str] = []
    original = Path.read_text

    def spy(self, *args, **kwargs):
        opened.append(str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    diagnostics.collect(paths=tmp_paths)
    assert not any("provider_config.json" in item for item in opened)
    assert not any(".onboarded" in item for item in opened)


def test_declared_schema_versions_come_from_the_core_constants(tmp_path, monkeypatch):
    """Roadmap §3.5: declared versions come from the constants, never a copy."""
    import core
    from folith.desktop import diagnostics, paths as desktop_paths
    from folith.storage import state_journal

    monkeypatch.setenv(desktop_paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.setenv(desktop_paths.LOG_DIR_ENV, str(tmp_path / "logs"))
    facts = diagnostics.collect(paths=desktop_paths.resolve_paths())
    schema = facts["data_schema"]
    assert schema["job_state"] == "unversioned"
    assert schema["runtime"]["declared"] == core.RUNTIME_SCHEMA_VERSION
    assert schema["translation_drafts"]["declared"] == core.TRANSLATION_DRAFTS_VERSION
    assert schema["journal"]["declared"] == state_journal.JOURNAL_SCHEMA_VERSION
    # The constants are the real ones the product writes.
    assert core.RUNTIME_SCHEMA_VERSION == "2"
    assert core.TRANSLATION_DRAFTS_VERSION == 1


def test_observed_schema_is_read_from_the_real_files(tmp_path, monkeypatch):
    import json
    from folith.desktop import diagnostics, paths as desktop_paths

    data = tmp_path / "data"
    job = data / "job-abc"
    job.mkdir(parents=True)
    (job / "runtime_state.json").write_text(
        json.dumps({"schema_version": "2", "status": "completed"}), encoding="utf-8")
    (job / "translation_drafts.json").write_text(
        json.dumps({"version": 1, "drafts": []}), encoding="utf-8")
    (job / "state_journal.jsonl").write_text(
        json.dumps({"schema": 1, "pairs": []}) + "\n", encoding="utf-8")
    monkeypatch.setenv(desktop_paths.DATA_DIR_ENV, str(data))
    monkeypatch.setenv(desktop_paths.LOG_DIR_ENV, str(tmp_path / "logs"))

    schema = diagnostics.collect(paths=desktop_paths.resolve_paths())["data_schema"]
    assert schema["runtime"]["observed"] == "2"
    assert schema["translation_drafts"]["observed"] == 1
    assert schema["journal"]["observed"] == 1
    assert "1 job(s)" in schema["source"]


def test_fresh_install_diagnostics_is_honest(tmp_path, monkeypatch):
    """A brand-new install has no data dir: diagnostics must not invent values."""
    from folith.desktop import diagnostics, paths as desktop_paths

    data = tmp_path / "missing" / "data"      # deliberately absent
    monkeypatch.setenv(desktop_paths.DATA_DIR_ENV, str(data))
    monkeypatch.setenv(desktop_paths.LOG_DIR_ENV, str(tmp_path / "logs"))

    facts = diagnostics.collect(paths=desktop_paths.resolve_paths())
    schema = facts["data_schema"]
    assert schema["job_state"] == "unversioned"
    # Declared versions are still known (they come from the constants) ...
    assert schema["runtime"]["declared"] == "2"
    assert schema["journal"]["declared"] == 1
    assert schema["translation_drafts"]["declared"] == 1
    # ... while nothing is claimed about data that does not exist yet.
    assert schema["runtime"]["observed"] is None
    assert schema["journal"]["observed"] is None
    assert schema["translation_drafts"]["observed"] is None
    assert "0 job(s)" in schema["source"]
    assert facts["locations"]["job_count"] == 0


def test_bundled_streamlit_version_matches_the_bundle_metadata():
    """§3.5: the reported version must be the one actually shipped."""
    import re
    from pathlib import Path

    app = Path(__file__).resolve().parents[1] / "dist" / "desktop" / "Folith.app"
    if not app.is_dir():
        import pytest

        pytest.skip("no built bundle to inspect")
    metadata = next(app.rglob("streamlit-*.dist-info/METADATA"), None)
    if metadata is None:
        import pytest

        pytest.skip("streamlit metadata not found in the bundle")
    version = None
    for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("Version: "):
            version = line.split(": ", 1)[1].strip()
            break
    assert version, "bundled metadata must declare a version"
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
    # The distribution folder name carries the same version.
    assert f"streamlit-{version}.dist-info" == metadata.parent.name


def test_bare_credentials_in_log_lines_are_redacted():
    """§3.5: a key can appear without an `api_key=` prefix and must still go."""
    from folith.desktop import diagnostics

    cases = {
        "ERROR upstream returned 401 for key sk-BARE-BBB2222222222": "sk-BARE-BBB2222222222",
        "ERROR google said AIzaSyA1234567890abcdefghijklmnopqrstuv": "AIzaSyA1234567890abcdefghijklmnopqrstuv",
        "ERROR Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "WARNING startup_token=abc123def456": "abc123def456",
        "ERROR api_key: sk-PREFIXED-AAA111": "sk-PREFIXED-AAA111",
    }
    for line, secret in cases.items():
        redacted = diagnostics._redact_text(line)
        assert secret not in redacted, f"leaked {secret!r} in {redacted!r}"
        assert "<redacted>" in redacted

    # Plain hashes must survive: diagnostics stay useful.
    digest = "3ddb8e87b05f072e0d427e68ae383555b0dcdc8ae85a574c4ebcab81be69daea"
    assert digest in diagnostics._redact_text(f"ERROR hash mismatch: {digest}")
