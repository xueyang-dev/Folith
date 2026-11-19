"""测量脚本的硬断言：数据根必须在 /tmp 或仓库 .loop/ 下，绝不碰用户目录。

用法（每个要入仓的测量脚本开头）：

    from safe_data_dir import assert_safe_data_dir
    assert_safe_data_dir(DATA)

或者复制下面 12 行到脚本里（独立、无依赖）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_FORBIDDEN = (
    Path.home() / "Library" / "Application Support" / "Folith",
    Path.home() / "Library" / "Application Support" / "FolioThread",
)


def assert_safe_data_dir(path: str | os.PathLike[str]) -> Path:
    """Resolve *path*, refuse it unless it is under /tmp or <repo>/.loop.

    Refuses the user's real Folith data directory by both prefix and identity,
    so a symlink or a relative path cannot slip through.
    """
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"data dir does not exist: {root}")
    for forbidden in _FORBIDDEN:
        resolved = forbidden.resolve()
        if root == resolved or resolved in root.parents:
            raise SystemExit(f"REFUSED: data dir is inside the user's Folith directory: {root}")
    repo = Path(__file__).resolve().parents[1]
    allowed = (Path("/tmp").resolve(), repo / ".loop")
    if not any(base == root or base in root.parents for base in allowed):
        raise SystemExit(f"REFUSED: data dir must be under /tmp or <repo>/.loop: {root}")
    # 同时保护 core 的全局数据根，防止 prepare_environment 之后漂移。
    sys.path.insert(0, str(repo))
    try:
        import core

        output = Path(core.OUTPUT_DIR).expanduser().resolve()
        for forbidden in _FORBIDDEN:
            resolved = forbidden.resolve()
            if output == resolved or resolved in output.parents:
                raise SystemExit(
                    f"REFUSED: core.OUTPUT_DIR points at the user's Folith data: {output}")
    except ImportError:
        pass
    return root
