"""交付工作区的文件动作：ZIP 打包、打开冻结目录、单个文件另存为、文件信息。

这些都是用户流程的最后一步，所以每条路径都必须"要么完整成功，要么明确失败"：
冻结快照只读；缺文件或被改过的快照不能当成成功；同名文件不能悄悄覆盖。
"""
from __future__ import annotations

import hashlib
import json
import shutil
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

import core
from folith import snapshots
from folith.qt import adapter

SOURCE_NAME = "Model Risk Governance Standard.docx"


@pytest.fixture()
def freeze(tmp_path, monkeypatch):
    """在临时 OUTPUT_DIR 里造真实的冻结交付（与 delivery_snapshot_test 同法）。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "outputs")

    def _freeze(job_id: str = "deliveryactions01", filename: str = SOURCE_NAME):
        state = core.new_job_state(filename)
        state.update(
            p1_done=True, p2_done=True, report_enabled=False,
            paras=["Source text"],
            pairs=[{
                "source": "Source text", "target": "译文",
                "initial_target": "初译", "reviewed": True,
                "target_provenance": "reviewed",
            }],
            findings=[], glossary=[], delivery_status="draft",
            has_blocking=False,
        )
        core.save_source(job_id, b"source document bytes")
        core.save_job_state(job_id, state)
        approved, ok, errors = core.approve_delivery(
            job_id, note="v1", actor="reviewer")
        assert ok, errors
        return job_id, int(approved["latest_delivery_snapshot_version"])

    return _freeze


@pytest.fixture()
def frozen(freeze):
    return freeze()


def _snapshot_hashes(job_id: str, version: int) -> dict[str, str]:
    folder = adapter.delivery_folder_path(job_id, version)
    return {
        path.relative_to(folder).as_posix(): hashlib.sha256(
            path.read_bytes()).hexdigest()
        for path in sorted(folder.rglob("*")) if path.is_file()
    }


def _asset_names(job_id: str, version: int) -> list[str]:
    return [name for name, _path in adapter.snapshot_asset_paths(job_id, version)]


def test_archive_name_uses_the_source_name_and_sanitizes_it(freeze, monkeypatch):
    job_id, version = freeze()
    assert adapter.delivery_archive_name(job_id, version) == \
        "Model Risk Governance Standard_delivery_v1.zip"

    odd_id, odd_version = freeze("deliveryactions02", "报告 v2: 客户*.docx")
    assert adapter.delivery_archive_name(odd_id, odd_version) == \
        "报告 v2_ 客户_delivery_v1.zip"

    # 没有原文件名时退回中性名字，仍然能分辨版本。
    monkeypatch.setattr(adapter, "_source_name", lambda job_id, state=None: "")
    assert adapter.delivery_archive_name(job_id, version) == f"delivery_v{version}.zip"


def test_archive_name_rejects_an_unknown_version(frozen):
    job_id, _version = frozen
    with pytest.raises(adapter.AdapterError) as excinfo:
        adapter.delivery_archive_name(job_id, 99)
    assert str(excinfo.value) == "找不到 v99 的冻结交付"


def test_archive_contains_every_frozen_asset_and_the_manifest(frozen):
    job_id, version = frozen
    manifest = core.list_delivery_snapshots(job_id)[0]
    assets = core.delivery_snapshot_assets(job_id, version)
    assert assets and all(data is not None for data in assets.values())

    with ZipFile(BytesIO(adapter.delivery_archive_bytes(job_id, version))) as bundle:
        assert set(bundle.namelist()) == set(assets) | {snapshots.MANIFEST_FILE}
        for name, data in assets.items():
            assert bundle.read(name) == data, name
        assert json.loads(bundle.read(snapshots.MANIFEST_FILE)) == manifest


def test_archive_layout_matches_the_snapshot_archive(frozen):
    """ZIP 必须与 ``core.delivery_snapshot_archive`` 同布局（同样的条目、同样的字节）。"""
    job_id, version = frozen
    ours = adapter.delivery_archive_bytes(job_id, version)
    reference = core.delivery_snapshot_archive(job_id, version)
    assert reference
    with ZipFile(BytesIO(ours)) as mine, ZipFile(BytesIO(reference)) as theirs:
        assert mine.namelist() == theirs.namelist()
        for name in theirs.namelist():
            assert mine.read(name) == theirs.read(name), name


def test_archive_refuses_a_missing_asset_and_writes_nothing(frozen, tmp_path):
    job_id, version = frozen
    name = _asset_names(job_id, version)[0]
    Path(core.delivery_snapshot_asset_path(job_id, version, name)).unlink()

    with pytest.raises(adapter.AdapterError) as excinfo:
        adapter.delivery_archive_bytes(job_id, version)
    assert str(excinfo.value) == f"交付快照缺少文件：{name}"

    destination = tmp_path / "exports" / "delivery.zip"
    with pytest.raises(adapter.AdapterError):
        adapter.save_delivery_archive(job_id, version, destination)
    assert not destination.exists()
    assert not destination.parent.exists(), "校验失败时不得留下目录或半截文件"


def test_archive_refuses_a_corrupted_asset_and_writes_nothing(frozen, tmp_path):
    job_id, version = frozen
    name = _asset_names(job_id, version)[0]
    path = Path(core.delivery_snapshot_asset_path(job_id, version, name))
    changed = bytearray(path.read_bytes())
    changed[0] ^= 0xFF
    path.write_bytes(changed)

    with pytest.raises(adapter.AdapterError) as excinfo:
        adapter.delivery_archive_bytes(job_id, version)
    assert str(excinfo.value) == f"交付快照中的文件校验失败：{name}"

    destination = tmp_path / "exports" / "delivery.zip"
    with pytest.raises(adapter.AdapterError):
        adapter.save_delivery_archive(job_id, version, destination)
    assert not destination.exists()


def test_save_archive_writes_the_same_bytes_atomically(frozen, tmp_path, monkeypatch):
    """The saved file is exactly the bytes the verified builder produced.

    Both sides of this comparison must come from *one* build.  ``ZipFile``
    stamps every entry with ``time.localtime(time.time())[:6]`` (2 s DOS
    resolution), so two separate builds of the same, unmodified snapshot can
    differ by one byte per entry whenever the clock crosses an even second in
    between -- that is exactly what failed on CI (Release Validation run
    43176620912, Python 3.12 job, ``At index 10 diff``).  Recording the bytes
    the save path itself built keeps the assertion ("no re-encode, no
    truncation, nothing else written") and removes the wall clock from it.
    """
    job_id, version = frozen
    destination = tmp_path / "交付 结果" / "交付包.zip"
    built: list[bytes] = []
    real_build = adapter.delivery_archive_bytes

    def recording_build(*args, **kwargs):
        data = real_build(*args, **kwargs)
        built.append(data)
        return data

    monkeypatch.setattr(adapter, "delivery_archive_bytes", recording_build)
    written = adapter.save_delivery_archive(job_id, version, destination)
    assert written == destination
    # The save path must go through the validated builder (and build once).
    assert len(built) == 1, built
    assert destination.read_bytes() == built[0]
    # ... and that archive carries this snapshot's payloads, timestamps aside.
    assets = core.delivery_snapshot_assets(job_id, version)
    with ZipFile(destination) as saved:
        assert set(saved.namelist()) == set(assets) | {snapshots.MANIFEST_FILE}
        for name, data in assets.items():
            assert saved.read(name) == data, name
    # 原子写出不留下临时文件。
    assert list(destination.parent.iterdir()) == [destination]


def test_save_archive_reports_a_folder_target_and_write_failures(
        frozen, tmp_path, monkeypatch):
    job_id, version = frozen
    with pytest.raises(adapter.AdapterError) as folder:
        adapter.save_delivery_archive(job_id, version, tmp_path)
    assert "目标位置是文件夹" in str(folder.value)

    def full_disk(*args, **kwargs):
        raise OSError("磁盘已满")

    monkeypatch.setattr(adapter.tempfile, "mkstemp", full_disk)
    with pytest.raises(adapter.AdapterError) as failure:
        adapter.save_delivery_archive(job_id, version, tmp_path / "x.zip")
    assert "无法写入 x.zip" in str(failure.value)
    assert "磁盘已满" in str(failure.value)
    assert not (tmp_path / "x.zip").exists()


def test_folder_path_is_the_real_snapshot_directory(frozen):
    job_id, version = frozen
    folder = adapter.delivery_folder_path(job_id, version)
    assert folder.is_dir()
    assert folder.name == f"v{version}"
    assert (folder / snapshots.MANIFEST_FILE).is_file()
    assert folder == Path(core.delivery_snapshot_asset_path(job_id, version, ""))

    with pytest.raises(adapter.AdapterError) as excinfo:
        adapter.delivery_folder_path(job_id, 99)
    assert str(excinfo.value) == "找不到 v99 的冻结交付"


def test_folder_path_reports_a_vanished_snapshot_directory(
        frozen, monkeypatch):
    job_id, version = frozen
    shutil.rmtree(adapter.delivery_folder_path(job_id, version))
    # 清单还在（模拟目录在检查之后被移动或删除），不能把"没有目录"当成成功。
    monkeypatch.setattr(
        adapter.core, "list_delivery_snapshots",
        lambda job_id: [{"snapshot_version": version, "assets": []}])
    with pytest.raises(adapter.AdapterError) as excinfo:
        adapter.delivery_folder_path(job_id, version)
    assert f"交付快照 v{version} 的文件夹不存在" in str(excinfo.value)


def test_open_delivery_folder_uses_the_injected_opener(frozen):
    job_id, version = frozen
    folder = adapter.delivery_folder_path(job_id, version)
    opened: list[Path] = []

    def opener(path):
        opened.append(Path(path))
        return SimpleNamespace(ok=True, detail="")

    result = adapter.open_delivery_folder(job_id, version, opener=opener)
    assert result == {"ok": True, "path": str(folder), "detail": ""}
    assert opened == [folder]


def test_open_delivery_folder_never_launches_the_real_file_manager(
        frozen, monkeypatch):
    job_id, version = frozen
    folder = adapter.delivery_folder_path(job_id, version)
    called: list[Path] = []

    def fake_open_directory(path):
        called.append(Path(path))
        return SimpleNamespace(ok=False, detail="系统拒绝打开")

    monkeypatch.setattr(adapter.file_actions, "open_directory", fake_open_directory)
    result = adapter.open_delivery_folder(job_id, version)
    assert result == {"ok": False, "path": str(folder), "detail": "系统拒绝打开"}
    assert called == [folder]


def test_open_delivery_folder_reports_unknown_version_and_crashes(frozen):
    job_id, version = frozen
    missing = adapter.open_delivery_folder(job_id, 99, opener=lambda path: None)
    assert missing["ok"] is False
    assert missing["detail"] == "找不到 v99 的冻结交付"
    assert missing["path"] == ""

    def explode(path):
        raise RuntimeError("boom")

    broken = adapter.open_delivery_folder(job_id, version, opener=explode)
    assert broken["ok"] is False
    assert "boom" in broken["detail"]
    assert broken["path"] == str(adapter.delivery_folder_path(job_id, version))


def test_asset_copy_uses_the_delivery_name_and_never_overwrites(frozen, tmp_path):
    job_id, version = frozen
    name = _asset_names(job_id, version)[0]
    default_name = adapter.delivery_default_asset_name(job_id, version, name)
    assert default_name == adapter.delivery_file_name(SOURCE_NAME, name)
    assert default_name.startswith("Model Risk Governance Standard_")

    destination = tmp_path / "另存 为" / default_name
    written = adapter.save_delivery_asset_copy(job_id, version, name, destination)
    assert written == destination
    assert destination.read_bytes() == \
        core.delivery_snapshot_assets(job_id, version)[name]

    # 已有同名文件：默认拒绝，且原内容原封不动。
    destination.write_text("用户自己的文件", encoding="utf-8")
    with pytest.raises(adapter.AdapterError) as conflict:
        adapter.save_delivery_asset_copy(job_id, version, name, destination)
    assert "目标文件已存在，未覆盖" in str(conflict.value)
    assert destination.read_text(encoding="utf-8") == "用户自己的文件"

    # 只有明确 overwrite=True 才替换。
    adapter.save_delivery_asset_copy(
        job_id, version, name, destination, overwrite=True)
    assert destination.read_bytes() == \
        core.delivery_snapshot_assets(job_id, version)[name]


def test_asset_copy_rejects_a_name_outside_the_manifest(frozen, tmp_path):
    job_id, version = frozen
    with pytest.raises(adapter.AdapterError) as excinfo:
        adapter.save_delivery_asset_copy(
            job_id, version, "not-in-snapshot.docx", tmp_path / "x.docx")
    assert str(excinfo.value) == "交付快照里没有这个文件：not-in-snapshot.docx"
    assert not (tmp_path / "x.docx").exists()

    with pytest.raises(adapter.AdapterError) as name_error:
        adapter.delivery_default_asset_name(job_id, version, "not-in-snapshot.docx")
    assert str(name_error.value) == "交付快照里没有这个文件：not-in-snapshot.docx"

    with pytest.raises(adapter.AdapterError):
        adapter.delivery_default_asset_name(job_id, 99, "bilingual.jsonl")


def test_asset_copy_refuses_a_corrupted_or_missing_snapshot_file(frozen, tmp_path):
    job_id, version = frozen
    name = _asset_names(job_id, version)[0]
    path = Path(core.delivery_snapshot_asset_path(job_id, version, name))
    changed = bytearray(path.read_bytes())
    changed[0] ^= 0xFF
    path.write_bytes(changed)

    corrupt_target = tmp_path / "corrupt" / "copy.docx"
    with pytest.raises(adapter.AdapterError) as corrupt:
        adapter.save_delivery_asset_copy(job_id, version, name, corrupt_target)
    assert str(corrupt.value) == f"交付快照中的文件校验失败：{name}"
    assert not corrupt_target.exists()

    path.unlink()
    missing_target = tmp_path / "missing" / "copy.docx"
    with pytest.raises(adapter.AdapterError) as missing:
        adapter.save_delivery_asset_copy(job_id, version, name, missing_target)
    assert str(missing.value) == f"交付快照缺少文件：{name}"
    assert not missing_target.exists()


def test_asset_info_reports_manifest_and_disk_state(frozen):
    job_id, version = frozen
    manifest = core.list_delivery_snapshots(job_id)[0]
    name = _asset_names(job_id, version)[0]
    record = next(item for item in manifest["assets"] if item["name"] == name)

    info = adapter.delivery_asset_info(job_id, version, name)
    assert info["name"] == name
    assert info["path"] == str(core.delivery_snapshot_asset_path(job_id, version, name))
    assert info["exists"] is True
    assert info["size"] == record["size"]
    assert info["sha256"] == record["sha256"]
    assert info["actual_size"] == record["size"]
    assert info["created_at"] == manifest["created_at"]
    assert info["version"] == version
    assert info["source_name"] == SOURCE_NAME
    assert info["extension"] == Path(name).suffix
    assert info["modified_at"]

    # 文件被删掉时如实报告缺失，而不是继续显示清单里的记录。
    Path(info["path"]).unlink()
    gone = adapter.delivery_asset_info(job_id, version, name)
    assert gone["exists"] is False
    assert gone["actual_size"] is None
    assert gone["modified_at"] is None
    assert gone["sha256"] == record["sha256"]


def test_file_actions_never_modify_the_frozen_snapshot(frozen, tmp_path):
    job_id, version = frozen
    before = _snapshot_hashes(job_id, version)
    assert snapshots.MANIFEST_FILE in before

    adapter.delivery_archive_bytes(job_id, version)
    adapter.delivery_archive_name(job_id, version)
    adapter.save_delivery_archive(job_id, version, tmp_path / "out" / "delivery.zip")
    folder = adapter.delivery_folder_path(job_id, version)
    adapter.open_delivery_folder(job_id, version, opener=lambda path: SimpleNamespace(
        ok=True, detail=""))
    for name in _asset_names(job_id, version):
        adapter.delivery_default_asset_name(job_id, version, name)
        adapter.delivery_asset_info(job_id, version, name)
        adapter.save_delivery_asset_copy(
            job_id, version, name, tmp_path / "copies" / name)

    assert _snapshot_hashes(job_id, version) == before
    assert adapter.delivery_folder_path(job_id, version) == folder
