"""Native helpers must not open console windows in a frozen GUI build."""
import subprocess
from types import SimpleNamespace

from folith import ocr_tesseract
from folith.desktop import process as desktop_process

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def test_hidden_console_kwargs_on_windows(monkeypatch):
    monkeypatch.setattr(desktop_process.os, "name", "nt")
    assert desktop_process.hidden_console_kwargs() == {
        "creationflags": CREATE_NO_WINDOW}


def test_hidden_console_kwargs_on_posix(monkeypatch):
    monkeypatch.setattr(desktop_process.os, "name", "posix")
    assert desktop_process.hidden_console_kwargs() == {}


def test_tesseract_page_passes_hidden_console_flag(monkeypatch):
    monkeypatch.setattr(desktop_process.os, "name", "nt")
    captured = {}

    def fake_run(args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=1, stdout="", stderr="forced failure")

    monkeypatch.setattr(ocr_tesseract.subprocess, "run", fake_run)
    blocks, error = ocr_tesseract._ocr_page_with_tesseract(
        "tesseract.exe", b"not-a-real-png", "eng")
    assert blocks is None
    assert error
    assert captured.get("creationflags") == CREATE_NO_WINDOW
