#!/usr/bin/env python3
"""Merge arm payloads from several dynamic-context benchmark output dirs.

The A/B harness writes one ``arms.json`` per ``--phase translate`` invocation
containing only the arms that invocation ran, so a multi-phase experiment needs
an explicit merge before ``--phase compare``.

Run
---
    python3 scripts/dynamic_context_merge.py \
        --out tmp/dynamic-context-ab \
        --from tmp/dynamic-context-ab/arms-phase1.json \
        --from tmp/dynamic-context-ab/arms.json \
        --rename D_budget_small=D_budget_tiny_600 \
        --from-dir tmp/dynamic-context-ab-tiny
"""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
from typing import Any, Dict, List


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True,
                        help="directory whose arms.json becomes the merged set")
    parser.add_argument("--from", dest="sources", action="append", default=[],
                        help="arms.json path to merge (repeatable)")
    parser.add_argument("--from-dir", dest="source_dirs", action="append",
                        default=[], help="directory containing arms.json")
    parser.add_argument("--rename", action="append", default=[],
                        help="OLD=NEW label rename, applied to the next source only")
    args = parser.parse_args()

    out_dir = Path(args.out)
    merged: Dict[str, Any] = {}
    base: Dict[str, Any] = {}
    sources: List[Path] = [Path(p) for p in args.sources]
    sources += [Path(d) / "arms.json" for d in args.source_dirs]
    renames = [item.split("=", 1) for item in args.rename if "=" in item]

    for path in sources:
        if not path.exists():
            print(f"skip missing {path}")
            continue
        blob = _load(path)
        base = base or blob
        for label, payload in (blob.get("arms") or {}).items():
            new_label = label
            for old, new in renames:
                if label == old:
                    new_label = new
            payload = dict(payload)
            payload["label"] = new_label
            merged[new_label] = payload
        print(f"merged {len(blob.get('arms') or {})} arm(s) from {path}")

    if not merged:
        raise SystemExit("nothing merged")

    out_path = out_dir / "arms.json"
    out_path.write_text(json.dumps({
        "schema": "dynamic-context-ab/arms/1",
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "fixture": base.get("fixture"),
        "limit": base.get("limit"),
        "concurrency": base.get("concurrency"),
        "enable_review": bool(base.get("enable_review")),
        "dry_run": bool(base.get("dry_run")),
        "wiring": base.get("wiring"),
        "default_context_budget_tokens": base.get("default_context_budget_tokens"),
        "arms": merged,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out_path} with arms: {', '.join(merged)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
