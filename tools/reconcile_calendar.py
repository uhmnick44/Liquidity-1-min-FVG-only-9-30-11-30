"""Reconcile 1m bars against a status-derived calendar and halt report.

Usage:
  python tools/reconcile_calendar.py CALENDAR.json STATUS_REPORT.json OUT_HALTS.json BARS_DIR [BARS_DIR ...]

Outputs:
- Counts of front-contract scheduled minutes, missing minutes explained by
  exchange-reported halts, and unexplained missing minutes. "Front" is the
  highest observed session volume; this is descriptive, not the rollover rule.
- Bars printed outside the calendar (each one means the calendar is wrong).
- OUT_HALTS.json: per-contract whole-minute halt sets, in MinuteEvidence format.
  Using it as N22 "notice-verified" evidence requires the trader's approval.
"""
from __future__ import annotations

import glob
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from liqengine.calendars import ExchangeCalendar  # noqa: E402
from liqengine.data import BarStore  # noqa: E402
from liqengine.dbn import load_databento_dbn  # noqa: E402
from liqengine.instrument import Contract, InstrumentConfig  # noqa: E402

NY = ZoneInfo("America/New_York")


def L(m):
    return datetime.fromtimestamp(m * 60, tz=timezone.utc).astimezone(NY).strftime("%Y-%m-%d %a %H:%M ET")


def main(cal_path, rep_path, out_halts, *bar_dirs):
    bar_dirs = list(bar_dirs)
    tick = Decimal("0.25")
    if "--tick" in bar_dirs:
        i = bar_dirs.index("--tick"); tick = Decimal(bar_dirs[i + 1]); del bar_dirs[i:i + 2]
    cal = ExchangeCalendar.load(cal_path)
    rep = json.loads(Path(rep_path).read_text())
    halts = {s: set() for s in rep["unscheduled_halts"]}
    for s, hs in rep["unscheduled_halts"].items():
        for h in hs:
            halts[s].update(range(h["full_minute_start"], h["full_minute_end"]))
    files = [f for d in bar_dirs for f in glob.glob(str(Path(d) / "*.ohlcv-1m.*.dbn.zst")) if "-" not in f.split(".")[-3]]
    syms = sorted({f.split(".")[-3] for f in files})
    inst = InstrumentConfig("X", True, tick, "X", None,
                            [Contract(s, None, 10**12, tick, True) for s in syms])
    st = BarStore()
    for f in files:
        load_databento_dbn(f, inst, st)
    sched = halted = 0
    unexplained, bars_in_halt = [], []
    for s in cal.sessions:
        vols = {sym: sum(st.bars[sym][m].volume for m in s.minutes() if m in st.bars.get(sym, {})) for sym in syms}
        f = max(vols, key=vols.get)
        if not vols[f]:
            continue
        for m in s.minutes():
            sched += 1
            if m not in st.bars[f]:
                if m in halts.get(f, ()):
                    halted += 1
                else:
                    unexplained.append({"minute": L(m), "contract": f})
    outside = []
    for sym in syms:
        for m in st.bars[sym]:
            if cal.covers(m) and cal.session_at(m) is None:
                outside.append({"minute": L(m), "contract": sym})
            if m in halts.get(sym, ()):
                bars_in_halt.append({"minute": L(m), "contract": sym})
    out = {"source": f"status-derived halts ({rep_path})",
           "verified_halts": {s: sorted(v) for s, v in halts.items() if v}}
    Path(out_halts).write_text(json.dumps(out))
    summary = {"front_scheduled_minutes": sched, "front_missing_explained_by_halts": halted,
               "front_missing_unexplained": unexplained, "bars_outside_calendar": len(outside),
               "bars_outside_calendar_sample": outside[:20], "bars_inside_reported_halts": bars_in_halt[:20],
               "halt_minutes_per_contract": {s: len(v) for s, v in halts.items() if v}}
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
