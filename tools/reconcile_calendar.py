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
    from_session = None
    if "--from-session" in bar_dirs:
        i = bar_dirs.index("--from-session"); from_session = bar_dirs[i + 1]; del bar_dirs[i:i + 2]
    tick = Decimal("0.25")
    if "--tick" in bar_dirs:
        i = bar_dirs.index("--tick"); tick = Decimal(bar_dirs[i + 1]); del bar_dirs[i:i + 2]
    inst_cfg = None
    if "--instrument" in bar_dirs:
        i = bar_dirs.index("--instrument"); inst_cfg = InstrumentConfig.load(bar_dirs[i + 1]); del bar_dirs[i:i + 2]
    cal = ExchangeCalendar.load(cal_path)
    rep = json.loads(Path(rep_path).read_text())
    def key_of(k):
        # status report keys are "SYM#instrument_id" (newer builder) or plain symbols
        if "#" in k and inst_cfg is not None:
            c = inst_cfg.contract_by_instrument_id(int(k.split("#")[1]))
            return None if c is None else c.symbol
        return k.split("#")[0]
    halts = {}
    for s, hs in rep["unscheduled_halts"].items():
        kk = key_of(s)
        if kk is None:
            continue
        for h in hs:
            halts.setdefault(kk, set()).update(range(h["full_minute_start"], h["full_minute_end"]))
    files = [f for d in bar_dirs for f in glob.glob(str(Path(d) / "*.ohlcv-1m.*.dbn.zst")) if "-" not in f.split(".")[-3]]
    syms = sorted({f.split(".")[-3] for f in files})
    inst = inst_cfg or InstrumentConfig("X", True, tick, "X", None,
                                        [Contract(s, None, 10**12, tick, True) for s in syms])
    st = BarStore()
    for f in files:
        load_databento_dbn(f, inst, st)
    syms = sorted(st.bars)
    since = None
    if "--from-session" in bar_dirs:
        pass
    sched = halted = 0
    unexplained, bars_in_halt = [], []
    for s in cal.sessions:
        if from_session and s.session_id < from_session:
            continue
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
    first_m = next((s.start for s in cal.sessions if not from_session or s.session_id >= from_session), None)
    outside = []
    for sym in syms:
        for m in st.bars[sym]:
            if first_m is not None and m < first_m:
                continue
            if cal.covers(m) and cal.session_at(m) is None:
                outside.append({"minute": L(m), "contract": sym})
            if m in halts.get(sym, ()) and (first_m is None or m >= first_m):
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
