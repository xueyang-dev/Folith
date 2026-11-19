#!/usr/bin/env python3
"""生成一个可复现的合成数据目录，用来测 #514/#515 的启动与项目页卡顿。

**文本全部是程序生成的，不含任何用户文档。** 同一 `--seed` 与参数产生同样的任务、
同样的文本、同样的 sha256。生成物是一个普通的 Folith 数据目录（任务目录 + state.json +
交付快照），可以直接拷到 `/tmp` 下给 `--smoke` / runner 用。

为什么需要它：#514（启动 16 s）与 #515（项目页 16–33 s）的原始证据来自一份 21 任务、
609 MB 的用户数据目录，不能外发也不能给并行 worker 用。这个脚本用同样的形状
（一半任务已交付、带快照，合计约 3,000 待审项）重建一份，使「修前/修后」有稳定的基面。

用法：

```
python scripts/make_synthetic_data_dir.py --out /tmp/synth-7 --tasks 21 --seed 7
python scripts/make_synthetic_data_dir.py --out /tmp/synth-7 --tasks 21 --seed 7 --zip /tmp/synth-7.zip
```

`--inspect` 只打印现有目录的统计，不改动任何文件。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import tempfile
import zipfile
from pathlib import Path

#: 每个任务的段落数（pairs）。21 个任务 × 约 143 段 ≈ 3,000 待审项。
DEFAULT_SEGMENTS = 210
#: 一段"版面 IR"的近似大小（字节）。真实任务中每份 state 约 30 MB，来自整本书的
#: 段落版面数据；#514 的慢路径（json.dumps / 深拷贝）只有在 state 达到这个量级才触发。
IR_PER_SEGMENT = 145_000
#: 每个已交付任务的 findings 密度（约 1/3 段有检查项）
FINDINGS_PER_DELIVERED_DIVISOR = 2
#: 未交付任务的 findings 密度（约 1/2 段有检查项）
FINDINGS_PER_DRAFT_DIVISOR = 1
WORDS = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
         "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi", "rho",
         "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega")


def _text(rng: random.Random, words: int) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(words)).capitalize() + "。"


def _state(filename: str, index: int, segments: int, findings: int, rng: random.Random) -> dict:
    """一个任务状态：结构照 `core.new_job_state()`，内容全生成。"""
    pairs = [
        {"source": f"段 {n + 1}：" + _text(rng, 24),
         "target": f"译 {n + 1}：" + _text(rng, 22)}
        for n in range(segments)
    ]
    items = []
    for n in range(findings):
        pair = pairs[n % segments]
        items.append({
            "finding_id": f"synthetic-{index:03d}-{n:05d}",
            "kind": "terminology" if n % 3 == 0 else "number",
            "severity": ("blocking" if n % 17 == 0 else
                         "actionable" if n % 3 else "informational"),
            "segment": n % segments,
            "source": pair["source"][:60],
            "target": pair["target"][:60],
            "message": "生成的检查项：" + _text(rng, 12),
            "resolved": False,
        })
    return {
        "filename": filename,
        "p1_done": True,
        "p2_done": True,
        "p3_done": True,
        "report_enabled": True,
        "paras": [p["source"] for p in pairs],
        "pairs": pairs,
        # 大段版面 IR：占 state 体积的绝大部分，文本仍是程序生成的。
        "page_layout_ir": [
            "IR:" + _text(rng, IR_PER_SEGMENT // 8) for _ in range(segments)
        ],
        "auto_terms": {f"term-{n}": f"译法-{n}" for n in range(6)},
        "findings": items,
        "review_stats": {
            "reviewed_segments": 0, "batches_reviewed": 0,
            "batches_skipped_low_risk": 0, "skipped_segments": 0,
            "blocking": sum(1 for f in items if f["severity"] == "blocking"),
            "actionable": sum(1 for f in items if f["severity"] == "actionable"),
            "informational": sum(1 for f in items if f["severity"] == "informational"),
            "review_failed": 0,
        },
        "tm_used_count": segments,
        "has_blocking": any(f["severity"] == "blocking" for f in items),
        "p3_md": "# " + _text(rng, 8),
        "p3_sections": [{"title": _text(rng, 4), "body": _text(rng, 30)} for _ in range(3)],
        "theory": _text(rng, 20),
        "warnings": [],
        "annotations": {},
        "annotations_done": False,
        "annotations_done_offset": 0,
        "delivery_status": "final",
        "snapshot_version": 1,
    }


def _job_id(seed: int, index: int) -> str:
    """稳定、无用户信息的 id。"""
    return hashlib.sha256(f"synthetic:{seed}:{index}".encode()).hexdigest()[:16]


#: A-22: refuse to generate when the target disk is too full.  Each task costs
#: roughly ``segments * 3 MB`` at the current shape; require 3x that plus 10 GB
#: so a copy or a zip cannot fill the disk mid-translation again.
MIN_FREE_BYTES = 10 * 1024**3
EST_BYTES_PER_SEGMENT = 3 * 1024**2


def check_free_space(out: Path, tasks: int, segments: int) -> None:
    target = out if out.exists() else out.parent
    if not target.exists():
        target = Path("/tmp")
    try:
        free = shutil.disk_usage(target).free
    except OSError as exc:
        raise SystemExit(f"cannot check free space for {target}: {exc}") from exc
    needed = tasks * segments * EST_BYTES_PER_SEGMENT * 3 + MIN_FREE_BYTES
    if free < needed:
        raise SystemExit(
            f"REFUSED: only {free / 1024**3:.1f} GB free on {target}, "
            f"need at least {needed / 1024**3:.1f} GB for {tasks} tasks x {segments} segments")


def generate(out: Path, tasks: int, seed: int, segments: int) -> dict:
    """写数据目录，返回统计（不含本机家目录）。"""
    check_free_space(out, tasks, segments)
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    delivered = 0
    findings_total = 0
    for index in range(tasks):
        job = out / _job_id(seed, index)
        job.mkdir(parents=True, exist_ok=True)
        # 一半任务已交付：state 里标 final，并写一个最小的交付快照目录。
        is_delivered = index % 2 == 0
        findings = (segments // FINDINGS_PER_DELIVERED_DIVISOR if is_delivered
                    else segments // FINDINGS_PER_DRAFT_DIVISOR)
        state = _state(f"synthetic-{index:03d}.docx", index, segments, findings, rng)
        if not is_delivered:
            state["delivery_status"] = "draft"
            state.pop("snapshot_version", None)
        else:
            delivered += 1
        findings_total += findings
        (job / "state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        # 真实任务在 save_job_state 时会同步写一份轻量 summary.json；启动路径优先读它。
        # 合成目录也写一份同形状的，否则会把「没有 summary」的回退路径当成基线。
        (job / "summary.json").write_text(json.dumps({
            "job_id": job.name,
            "filename": state["filename"],
            "attention_count": sum(1 for f in state["findings"] if not f.get("resolved")),
            "attention_tone": "warning" if state.get("has_blocking") else "info",
            "segment_count": segments,
            "target_lang": "zh-CN",
            "runtime_status": "idle",
            "updated_at": f"synthetic-{seed}-{index:03d}",
            "project_id": None,
            "progress_label": "已完成" if is_delivered else "翻译中",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        (job / "source.docx").write_bytes(
            f"synthetic source {seed} {index}\n".encode())
        if is_delivered:
            snap = job / "snapshots" / "v1"
            snap.mkdir(parents=True, exist_ok=True)
            (snap / "delivery.docx").write_bytes(
                f"synthetic delivery {seed} {index}\n".encode())
            (snap / "manifest.json").write_text(json.dumps({
                "snapshot_version": 1,
                "job_id": job.name,
                "assets": [{"name": "delivery.docx"}],
                "artifact_hashes": {"delivery.docx": hashlib.sha256(
                    f"synthetic delivery {seed} {index}\n".encode()).hexdigest()},
                "translation_state_identity": "synthetic",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"tasks": tasks, "delivered": delivered,
            "segments_per_task": segments, "findings_total": findings_total,
            "seed": seed}


def inspect(out: Path) -> dict:
    """只读统计：任务数、state 数、快照数、总 findings。"""
    jobs = [p for p in out.iterdir() if p.is_dir()] if out.is_dir() else []
    states = snapshots = findings = 0
    for job in jobs:
        f = job / "state.json"
        if f.is_file():
            states += 1
            try:
                findings += len(json.loads(f.read_text()).get("findings") or [])
            except Exception:  # noqa: BLE001 - 只读统计，坏文件不算
                pass
        snapshots += len(list((job / "snapshots").glob("v*"))) if (job / "snapshots").is_dir() else 0
    return {"dirs": len(jobs), "states": states, "snapshots": snapshots,
            "findings": findings}


#: zip 条目的固定时间戳（zip 纪元，1980-01-01）。生成目录的**文件 mtime** 是运行时
#: 的墙钟，`ZipFile.write` 会把它写进条目头；两次生成只要跨过 zip 的 2 秒粒度，
#: 同样的内容就会得到不同字节，sha256 也就不再是"同一份数据"的指纹。
#: #541 的 RV 里正是这样红的（Python 3.11 / 3.12 各一次）。
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
#: 常规文件、0644：权限位也不能随 umask 漂移。
_ZIP_FILE_MODE = 0o644 << 16


def zip_dir(out: Path, archive: Path) -> str:
    """打包成 zip，返回 sha256。**同一份内容 → 同一串字节**，与生成时间无关。"""
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(out.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(path.relative_to(out).as_posix(),
                                   date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = _ZIP_FILE_MODE
            zf.writestr(info, path.read_bytes())
    return hashlib.sha256(archive.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="数据目录（生成模式）")
    parser.add_argument("--tasks", type=int, default=21)
    parser.add_argument("--segments", type=int, default=DEFAULT_SEGMENTS)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--zip", type=Path, help="生成后打包到此 zip")
    parser.add_argument("--inspect", type=Path, help="只统计现有目录")
    args = parser.parse_args()

    if args.inspect is not None:
        print(json.dumps(inspect(args.inspect), ensure_ascii=False, indent=2))
        return
    if args.out is None:
        parser.error("--out 或 --inspect 必须给一个")
    if args.tasks < 1 or args.segments < 1:
        parser.error("--tasks 与 --segments 必须为正数")
    stats = generate(args.out, args.tasks, args.seed, args.segments)
    if args.zip is not None:
        stats["zip"] = str(args.zip.name)
        stats["sha256"] = zip_dir(args.out, args.zip)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
