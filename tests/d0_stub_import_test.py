"""The D0 subprocess must work without cwd/PYTHONPATH or an editable install."""
import os
from pathlib import Path
import subprocess
import sys


def test_stub_imports_offline_provider_from_outside_repository(tmp_path):
    root = Path(__file__).resolve().parents[1]
    # -S disables .pth files and editable import hooks, which can hide a missing
    # repository path. Retain third-party packages needed by the real core.
    package_paths = [path for path in sys.path if path.endswith("site-packages")]
    script = (
        "import sys, runpy; "
        f"sys.path.extend({package_paths!r}); "
        f"stub = runpy.run_path({str(root / 'scripts/d0_fake_llm_server.py')!r}); "
        "content, kind = stub['build_content']({'messages': "
        "[{'role': 'user', 'content': 'OK'}]}); "
        "assert (content, kind) == ('OK', 'probe')"
    )
    env = dict(os.environ, FOLITH_OUTPUT_DIR=str(tmp_path / "data"),
               FOLITH_DESKTOP_DATA_DIR=str(tmp_path / "data"))
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, "-S", "-c", script], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
