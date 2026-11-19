"""合成数据目录生成器：可复现、规模可控、不含用户文档。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "make_synthetic_data_dir", REPO / "scripts" / "make_synthetic_data_dir.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generation_is_deterministic(tmp_path) -> None:
    mod = _module()
    a = mod.generate(tmp_path / "a", tasks=4, seed=7, segments=12)
    b = mod.generate(tmp_path / "b", tasks=4, seed=7, segments=12)
    assert a == b
    for name in sorted(p.name for p in (tmp_path / "a").iterdir()):
        assert ((tmp_path / "a" / name / "state.json").read_bytes()
                == (tmp_path / "b" / name / "state.json").read_bytes())
    assert a["tasks"] == 4 and a["delivered"] == 2
    assert a["findings_total"] > 0


def test_different_seeds_produce_different_data(tmp_path) -> None:
    mod = _module()
    mod.generate(tmp_path / "a", tasks=3, seed=7, segments=10)
    mod.generate(tmp_path / "b", tasks=3, seed=8, segments=10)
    a = list((tmp_path / "a").iterdir())[0]
    b = list((tmp_path / "b").iterdir())[0]
    assert (a / "state.json").read_bytes() != (b / "state.json").read_bytes()


def test_shape_and_delivered_snapshots(tmp_path) -> None:
    mod = _module()
    stats = mod.generate(tmp_path / "d", tasks=6, seed=11, segments=20)
    states = sorted((tmp_path / "d").glob("*/state.json"))
    assert len(states) == 6
    delivered = 0
    for state_file in states:
        state = json.loads(state_file.read_text())
        assert len(state["pairs"]) == 20
        assert state["filename"].startswith("synthetic-")
        if state["delivery_status"] == "final":
            delivered += 1
            assert (state_file.parent / "snapshots" / "v1" / "delivery.docx").is_file()
    assert delivered == stats["delivered"] == 3


def test_inspect_is_read_only(tmp_path) -> None:
    mod = _module()
    target = tmp_path / "d"
    stats = mod.generate(target, tasks=2, seed=3, segments=8)
    before = {p: p.read_bytes() for p in target.rglob("*") if p.is_file()}
    result = mod.inspect(target)
    assert result["states"] == stats["tasks"]
    assert result["snapshots"] == stats["delivered"]
    assert {p: p.read_bytes() for p in target.rglob("*") if p.is_file()} == before


def test_zip_is_deterministic(tmp_path) -> None:
    mod = _module()
    a = mod.generate(tmp_path / "a", tasks=2, seed=5, segments=8)
    sha_a = mod.zip_dir(tmp_path / "a", tmp_path / "a.zip")
    b = mod.generate(tmp_path / "b", tasks=2, seed=5, segments=8)
    sha_b = mod.zip_dir(tmp_path / "b", tmp_path / "b.zip")
    assert a == b and sha_a == sha_b == mod.zip_dir(tmp_path / "a", tmp_path / "a2.zip")


def test_generator_refuses_to_fill_the_disk(monkeypatch) -> None:
    """A-22: never repeat the 10:14 Errno 28 that interrupted the user's run."""
    mod = _module()
    import types

    monkeypatch.setattr(mod.shutil, "disk_usage",
                        lambda _p: types.SimpleNamespace(free=10 * 1024**3))
    try:
        mod.check_free_space(Path("/tmp/x"), 21, 210)
    except SystemExit as exc:
        assert "REFUSED" in str(exc)
    else:
        raise AssertionError("a 10 GB free disk must be refused for a 21-task dir")
