"""P11/A-81: credential writes are safe across fresh/stale temp files and failures.

Named ``persistence_*`` so the Windows persistence/recovery suite selects it.
POSIX-only mode assertions are guarded; save/replace/cleanup contract holds on
both platforms.
"""
from __future__ import annotations

import os
import pathlib
import stat

import pytest

from folith.engine import credential_file

NEW_VALUE = 'new-synthetic-key'


def test_fresh_file_owner_only(tmp_path):
    """POSIX: a brand-new temp file is 0600 at creation (no umask window)."""
    if os.name != 'posix':
        pytest.skip('POSIX mode assertion')
    path = tmp_path / 'provider_config.json'
    credential_file.write_credential_json(path, {'api_key': NEW_VALUE})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert NEW_VALUE in path.read_text(encoding='utf-8')


def test_stale_loose_tmp_never_receives_new_value(tmp_path):
    """A leftover 0644 .tmp from an old build must not be reused."""
    path = tmp_path / 'provider_config.json'
    stale = pathlib.Path(str(path) + '.tmp')
    stale.write_text('old', encoding='utf-8')
    stale.chmod(0o644)
    credential_file.write_credential_json(path, {'api_key': NEW_VALUE})
    # New value went to the product file; the stale temp was left untouched.
    assert NEW_VALUE in path.read_text(encoding='utf-8')
    assert stale.read_text(encoding='utf-8') == 'old'


def test_replace_failure_keeps_original_and_cleans_new_temp(tmp_path, monkeypatch):
    """A failed replace must not damage the original or leave this call's temp."""
    path = tmp_path / 'provider_config.json'
    path.write_text('{"api_key": "original"}', encoding='utf-8')
    real_replace = pathlib.Path.replace

    def failing_replace(self, target):
        if self.name != path.name and str(self).endswith('.tmp'):
            raise OSError('simulated replace failure')
        return real_replace(self, target)

    monkeypatch.setattr(pathlib.Path, 'replace', failing_replace)
    with pytest.raises(OSError, match='simulated'):
        credential_file.write_credential_json(path, {'api_key': NEW_VALUE})
    assert path.read_text(encoding='utf-8') == '{"api_key": "original"}'
    assert not list(tmp_path.glob('*.tmp'))


def test_both_save_entries_use_the_helper(monkeypatch):
    """Guard that both product save paths go through the safe writer."""
    import core
    from folith.engine import ocr_config

    called = []
    monkeypatch.setattr(credential_file, 'write_credential_json',
                        lambda p, payload: called.append(p.name))
    monkeypatch.setattr(core, 'load_provider_config', lambda: {})
    monkeypatch.setattr(core, '_ensure_output_dir', lambda: None)
    core.save_provider_config('p', 'm', 'k')
    ocr_config.save_ocr_online_config(provider='p', model='m', api_key='k')
    assert called == ['provider_config.json', 'ocr_online_config.json']
