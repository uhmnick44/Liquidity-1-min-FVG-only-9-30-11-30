"""Write engine outputs to disk (JSON Lines / CSV). Read-only w.r.t. engine state."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .engine import LiquidityEngine


def write_outputs(engine: LiquidityEngine, out_dir: str | Path, provenance: dict | None = None):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    refs, evts = engine.reference_snapshots(), engine.event_snapshots()
    for name, rows in (("references", refs), ("sweep_events", evts), ("lifecycle_log", engine.log),
                       ("data_quality", engine.data_quality)):
        with open(out / f"{name}.jsonl", "w") as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True, default=str) + "\n")
    for name, rows in (("references", refs), ("sweep_events", evts)):
        if not rows:
            continue
        with open(out / f"{name}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=sorted(rows[0]))
            w.writeheader()
            for r in rows:
                w.writerow({k: json.dumps(v, default=str) if isinstance(v, (list, dict)) else v for k, v in r.items()})
    if provenance is not None:
        (out / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True, default=str))
