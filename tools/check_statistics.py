"""Convert Databento `statistics` for MNQ outrights and check them against bars and the calendar.

Usage:
  python tools/check_statistics.py STATS_DIR CALENDAR.json OUT_REPORT.json BARS_DIR [BARS_DIR ...]

Checks (no repairs):
- Cleared volume: session mapping (statistics.convert), ts_ref agreement,
  revisions, coverage, and cleared volume >= 1m-bar volume sum (cleared volume
  includes block trades, so it can only be equal or larger).
- Session high/low: every venue figure must bound the observed 1m bars over the
  span it claims (inconsistencies are counted).
- Rollover dates implied by the N13(b) rule (descriptive replay of A12 with
  cleared volume), compared with the highest-volume contract changes.
"""
from __future__ import annotations

import glob
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from liqengine.calendars import ExchangeCalendar  # noqa: E402
from liqengine.data import BarStore  # noqa: E402
from liqengine.dbn import _read, check_mapping, load_databento_dbn  # noqa: E402
from liqengine.instrument import Contract, InstrumentConfig  # noqa: E402
from liqengine.statistics import convert  # noqa: E402

NS = 10**9


def replay_rollover(instrument, cal, vols, st):
    """Descriptive replay of A12/I3/I10/N8/N13(b) contract selection on cleared volume.
    Decision at each session end uses only figures available by then; effective next session."""
    from liqengine.rollover import ContractSelector
    sessions = cal.sessions
    current, k, log = None, 0, []
    while current is None and k < len(sessions):
        s = sessions[k]
        cands = [c for c in instrument.contracts if c.expiration > s.end and (c.activation is None or c.activation <= s.start)]
        figs = {c.symbol: vols.get((c.symbol, s.session_id)) for c in cands}
        # N8 decision for session k is taken when its figures are available
        if cands and all(v is not None and v.volume is not None for v in figs.values()):
            best = max(v.volume for v in figs.values())
            current = min((c for c in cands if figs[c.symbol].volume == best), key=lambda c: c.expiration).symbol
            log.append({"session": s.session_id, "initial": current})
        k += 1
    if current is None:
        return log
    sel = ContractSelector(instrument, current)
    pending, queue = None, []
    for s in sessions[k:]:
        if pending:
            log.append({"effective_session": s.session_id, "from": pending.from_contract, "to": pending.to_contract,
                        "crossover_session": pending.crossover_session_id,
                        "delta": None if pending.delta is None else str(pending.delta), "flags": pending.flags})
            sel.apply(pending)
            pending = None
        if not sel.current_eligible_at(s.start):
            log.append({"session": s.session_id, "SUSPENDED": sel.current})
            break
        queue.append(s)
        while queue and pending is None:
            cs = queue[0]
            syms = [sel.current] + [c.symbol for c in sel._eligible_successors(s.end)]
            figs = {x: vols[(x, cs.session_id)] for x in syms
                    if (x, cs.session_id) in vols and vols[(x, cs.session_id)].available_minute <= s.end}
            if len(figs) < len(syms):
                # not yet available, or never published/verified: wait while it may still arrive
                if cs is not s:   # N33: not published by the end of the next session -> unverified
                    log.append({"crossover_session_unverified": cs.session_id, "missing": sorted(set(syms) - set(figs))})
                    queue.pop(0)
                    continue
                break
            queue.pop(0)
            d = sel.evaluate(cs, figs, st, None, s.end)
            if d is not None:
                pending, queue = d, []
    return log


def main(stats_dir, cal_path, out, *bar_dirs):
    bar_dirs = list(bar_dirs)
    inst_path = Path(__file__).resolve().parents[1] / "config/instruments/MNQ.json"
    if "--instrument" in bar_dirs:
        i = bar_dirs.index("--instrument"); inst_path = Path(bar_dirs[i + 1]); del bar_dirs[i:i + 2]
    cal = ExchangeCalendar.load(cal_path)
    files = [f for f in glob.glob(str(Path(stats_dir) / "*.statistics.*.dbn.zst")) if "-" not in f.split(".")[-3]]
    syms = sorted({f.split(".")[-3] for f in files})
    inst = InstrumentConfig.load(inst_path)
    recs, maps = [], {}
    for f in files:
        meta, rs = _read(f)
        sym = meta.symbols[0]
        maps[sym] = [dict(m) for m in meta.mappings.get(sym, [])]
        for r in rs:
            recs.append((sym, r))

    def symbol_of(iid, d):
        c = inst.contract_by_instrument_id(iid)
        if c is not None:
            return c.symbol if c.is_outright else None
        for sym, mp in maps.items():
            if check_mapping(mp, iid, d) in ("ok", "ok_end_inclusive"):
                return sym
        return None

    # bars
    bfiles = [f for d in bar_dirs for f in glob.glob(str(Path(d) / "*.ohlcv-1m.*.dbn.zst")) if "-" not in f.split(".")[-3]]
    st = BarStore()
    binst = inst
    for f in bfiles:
        load_databento_dbn(f, binst, st)
    def bar_volume(sym, session):
        b = st.bars.get(sym)
        return None if b is None else sum(b[m].volume for m in session.minutes() if m in b)

    vols, coarse, rep = convert([r for _, r in recs], symbol_of, cal, inst, bar_volume=bar_volume)
    sess = {s.session_id: s for s in cal.sessions}
    # cleared volume vs bar sum
    below, cmp_n = [], 0
    for (sym, sid), v in vols.items():
        s = sess.get(sid)
        if s is None or sym not in st.bars:
            continue
        bsum = sum(st.bars[sym][m].volume for m in s.minutes() if m in st.bars[sym])
        cmp_n += 1
        if v.volume < bsum:
            below.append({"symbol": sym, "session": sid, "cleared": v.volume, "bar_sum": bsum})
    lag = Counter()
    for (sym, sid), v in vols.items():
        s = sess.get(sid)
        if s:
            lag[round((v.available_minute - s.end) / 60)] += 1
    # coarse consistency
    bad = 0
    for c in coarse:
        b = st.bars.get(c.contract, {})
        obs = [b[m] for m in range(c.start_minute, c.end_minute) if m in b]
        if not obs:
            continue
        g = inst.tick_size
        if c.high is not None and c.high < max(x.high for x in obs) * g:
            bad += 1
        if c.low is not None and c.low > min(x.low for x in obs) * g:
            bad += 1
    # coverage: sessions where a listed contract has bars but no cleared volume
    no_vol = Counter()
    for sym in st.bars:
        for s in cal.sessions:
            if (sym, s.session_id) not in vols and any(m in st.bars[sym] for m in s.minutes()):
                no_vol[sym] += 1
    from liqengine.instrument import InstrumentConfig as IC
    roll_log = replay_rollover(IC.load(str(inst_path)), cal, vols, st)
    report = {
        "records": len(recs), "contracts": syms, "rollover_replay": roll_log,
        "convert": {"used_volume": rep.used_volume, "used_extremes": rep.used_extremes,
                    "deletes_ignored": rep.deletes_ignored, "revised_volume": len(rep.revised_volume),
                    "revised_sample": rep.revised_volume[:10],
                    "rejected_below_bar_volume": len(rep.rejected_below_bar_volume),
                    "rejected_sample": rep.rejected_below_bar_volume[:15], "ts_ref_mismatch": len(rep.ts_ref_mismatch),
                    "ts_ref_mismatch_sample": rep.ts_ref_mismatch[:10], "unmapped": rep.unmapped,
                    "outside_calendar": rep.outside_calendar},
        "cleared_vs_bar_sum": {"compared": cmp_n, "cleared_below_bar_sum": len(below), "sample": below[:10]},
        "cleared_volume_publication_lag_hours_after_session_end": dict(sorted(lag.items())),
        "coarse_inconsistent_with_bars": bad, "coarse_records": len(coarse),
        "sessions_with_bars_but_no_cleared_volume": dict(no_vol),
    }
    Path(out).write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: report[k] for k in report if k not in ("contracts", "rollover_replay")}, indent=1, default=str)[:2500])
    print("ROLLOVER REPLAY:")
    for x in roll_log:
        print(" ", x)


if __name__ == "__main__":
    main(*sys.argv[1:])
