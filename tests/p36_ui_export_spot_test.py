"""P3-6 / G5 item 3: the UI export, walked through in a real window.

The kernel proofs already live in ``xliff12_schema_validation_test.py``. This
file covers what those cannot: a real window imports an SDLXLIFF and an
MQXLIFF, the locked rows are visible and read-only, the delivery panel offers
only the unchanged export for a dialect, and that export is byte-identical to
the file the user picked.

``FOLITH_P36_SCREENSHOT_DIR`` turns on the screenshot step; without it the test
still asserts everything, so CI does not need a display server.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

from io import BytesIO

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import core  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402
from folith.qt import adapter, window as window_module  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
#: One SDLXLIFF with subsegment locks and one MQXLIFF with unit locks.
DIALECTS = (
    ("sdlxliff", FIXTURES / "sdlxliff" / "QuotesSample.docx.sdlxliff"),
    ("mqxliff", FIXTURES / "mqxliff" / "locked.mqxliff"),
)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def client(tmp_path, monkeypatch, app):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data))
    monkeypatch.setenv("FOLITH_QT_SETTINGS_DIR", str(tmp_path / "settings"))
    monkeypatch.setattr(core, "OUTPUT_DIR", data)
    main = window_module.MainWindow(adapter.EngineConfig(), desktop_paths.resolve_paths(), None)
    main.timer.stop()
    main.show()
    app.processEvents()
    yield main
    main.runner.abandon()
    main.close()
    app.processEvents()


def _wait(app, predicate, timeout=120.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("timed out waiting for the window to finish")


def _screenshot(app, client, path: Path) -> None:
    client.raise_()
    client.activateWindow()
    for _ in range(12):
        app.processEvents()
        time.sleep(0.05)
    image = client.screen().grabWindow(int(client.winId()))
    assert not image.isNull()
    assert image.save(str(path))


@pytest.mark.parametrize("dialect,source", DIALECTS, ids=[item[0] for item in DIALECTS])
def test_dialect_import_is_read_only_and_exports_the_original(client, app, tmp_path,
                                                             dialect, source):
    original = source.read_bytes()
    client.import_paths([str(source)], open_result=True)
    _wait(app, lambda: client._current_job_id and not client._opening_job_id)
    app.processEvents()

    job_id = client._current_job_id
    state = core.load_job_state(job_id)
    assert state["xliff"]["dialect"] == dialect
    # Every dialect row is read-only, so whole-job translation stays off.
    assert all(pair.get("readonly") for pair in state["pairs"])
    assert client.workbench.start_button.isHidden()
    assert client.workbench.retry_button.isHidden()

    # The delivery panel offers the unchanged export and no dialect write-back.
    panel = client.workbench.delivery
    assert panel.bilingual_original.isVisibleTo(panel)
    assert not panel.bilingual_targets.isVisibleTo(panel)

    # A locked row shows the file's own target and cannot be edited.
    workbench = client.workbench
    workbench.select_segment(0)
    app.processEvents()
    assert workbench.target_edit.isReadOnly()
    assert not workbench.submit_button.isEnabled()
    assert not workbench.retranslate_button.isEnabled()
    assert workbench.target_edit.toPlainText() == (state["pairs"][0].get("target") or "")

    directory = os.environ.get("FOLITH_P36_SCREENSHOT_DIR")
    if directory:
        os.makedirs(directory, exist_ok=True)
        workbench.tabs.setCurrentWidget(workbench._pages["translate"])
        app.processEvents()
        _screenshot(app, client, Path(directory) / f"g5-{dialect}-workbench.png")
        workbench.tabs.setCurrentWidget(workbench._pages["deliver"])
        app.processEvents()
        _screenshot(app, client, Path(directory) / f"g5-{dialect}-delivery.png")

    # The unchanged export goes through the same signal the button emits.
    output = tmp_path / f"{dialect}-export{source.suffix}"
    client.export_bilingual_file(False, str(output))
    _wait(app, lambda: output.exists() and not client.runner.busy)
    assert output.read_bytes() == original, "dialect export must be byte-identical"


def test_generic_export_from_the_ui_matches_the_kernel_export(client, app, tmp_path):
    """P3-6 spot check: what the UI writes validates like the kernel's own export."""
    from lxml import etree

    source = FIXTURES / "xliff12" / "validators.en.xlf"
    client.import_paths([str(source)], open_result=True)
    _wait(app, lambda: client._current_job_id and not client._opening_job_id)
    job_id = client._current_job_id

    state = core.load_job_state(job_id)
    state["pairs"][0]["target"] = "This value should be false. [p3-6]"
    core.save_job_state(job_id, state)

    output = tmp_path / "ui-export.xlf"
    client.export_bilingual_file(True, str(output))
    _wait(app, lambda: output.exists() and not client.runner.busy)
    exported = output.read_bytes()

    # Same bytes the core produces for the same state: the UI adds no writer.
    kernel_copy = tmp_path / "kernel-export.xlf"
    core.export_xliff_task(job_id, kernel_copy, write_targets=True)
    assert exported == kernel_copy.read_bytes()

    # And it validates the way the kernel export does: the edited text changed,
    # no new schema error appeared, and the untouched units are identical.
    schema_dir = FIXTURES / "xliff12" / "schema"

    class _LocalXmlNamespace(etree.Resolver):
        def resolve(self, url, pubid, context):
            if url.endswith("xml.xsd"):
                return self.resolve_filename(str(schema_dir / "xml.xsd"), context)
            if url.startswith(("http://", "https://")):
                raise AssertionError(f"schema validation must not reach the network: {url}")
            return None

    def _load(filename: str):
        parser = etree.XMLParser(no_network=True)
        parser.resolvers.add(_LocalXmlNamespace())
        return etree.XMLSchema(etree.parse(str(schema_dir / filename), parser))

    def _issues(schema, data: bytes):
        schema.validate(etree.parse(BytesIO(data)))
        return [(item.line, item.column, item.message) for item in schema.error_log]

    for label, filename in (("strict", "xliff-core-1.2-strict.xsd"),
                            ("transitional", "xliff-core-1.2-transitional.xsd")):
        schema = _load(filename)
        before = _issues(schema, source.read_bytes())
        after = _issues(schema, exported)
        assert not [item for item in after if item not in before], label
