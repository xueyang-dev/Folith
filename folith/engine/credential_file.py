"""P11: create credential temp files owner-only from the moment of creation.

write_text() + chmod() leaves a plaintext window under a permissive umask.
Here each write uses a fresh, exclusively-created temp file with mode 0600,
then atomically replaces the target. A stale .tmp from an earlier version is
never reused, so it cannot keep its old permissive mode. On Windows the mode
is advisory and the platform account ACL applies (verified separately).
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile


def write_credential_json(path: pathlib.Path, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp',
                                dir=str(path.parent))
    temp = pathlib.Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(data)
        temp.replace(path)
    except BaseException:
        # Clean up only the file this call created; never touch the original.
        try:
            temp.unlink()
        except OSError:
            pass
        raise
