"""Build a CME exchange calendar for an instrument from Databento `status` records.

Usage:
  python tools/build_calendar_from_status.py STATUS_DIR OUT_CALENDAR.json OUT_REPORT.json [CALENDAR_ID]

Source: CME's own matching-engine status messages (GLBX.MDP3 `status` schema).
- Scheduled segments: from a TRADING(7) transition with reason SCHEDULED(1) to
  the next PRE_OPEN(1) or CLOSE(12) with reason SCHEDULED. Every active
  outright must carry the identical transition; any disagreement aborts.
- Trade date (session_id): the America/New_York date of the scheduled CLOSE
  (or scheduled NOT_AVAILABLE, trader decision 2026-11-25) that ends the
  session (I9 / N7 / N27). A session can therefore contain
  several segments, e.g. a holiday halt.
- Unscheduled halts: inside a scheduled segment, an instrument leaving the
  trading state with a non-scheduled reason, until it trades again. Reported per
  contract. Only WHOLE minutes inside such an interval are listed as halt
  minutes. Partially halted minutes keep their normal classification.

The output calendar is written with verified=false. It becomes verified only
after the trader signs off the checklist (spec I9).
"""
from __future__ import annotations

import glob
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
TRADING, PRE_OPEN, CLOSE, NOT_AVAILABLE = 7, 1, 12, 15
ENDS = (PRE_OPEN, CLOSE, NOT_AVAILABLE)  # scheduled transitions that end trading
HALTING = (PRE_OPEN, 8, 9, 10, NOT_AVAILABLE)  # pre-open, halt, pause, suspend, not available
SCHEDULED = 1
NS = 10**9


def iso(ns):
    return datetime.fromtimestamp(ns / NS, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local(ns):
    return datetime.fromtimestamp(ns / NS, tz=timezone.utc).astimezone(NY)


def load(status_dir):
    import databento_dbn as dbn
    import zstandard
    per = {}
    for f in sorted(glob.glob(str(Path(status_dir) / "*.status.*.dbn.zst"))):
        sym = f.split(".")[-3]
        if "-" in sym:
            continue  # outrights only
        raw = zstandard.ZstdDecompressor().stream_reader(open(f, "rb")).read()
        d = dbn.DBNDecoder()
        d.write(raw)
        recs = d.decode()
        # key by instrument_id: symbols can repeat across decades
        for r in recs[1:]:
            per.setdefault(f"{sym}#{r.instrument_id}", []).append((r.ts_event, int(r.action), int(r.reason), r.is_trading))
    return per


def main(status_dir, out_cal, out_rep, cal_id="CME_GLBX_MNQ_from_status"):
    per = load(status_dir)
    # ---- scheduled transitions: consensus across active instruments
    sched = defaultdict(set)
    span = {s: (rs[0][0], rs[-1][0]) for s, rs in per.items() if rs}
    for sym, rs in per.items():
        for ts, a, rsn, _ in rs:
            if rsn == SCHEDULED and a in (TRADING,) + ENDS:
                sched[(ts, a)].add(sym)
    for (ts, a), syms in sched.items():
        active = {s for s, (a0, a1) in span.items() if a0 <= ts <= a1}
        if active - syms:
            raise SystemExit(f"scheduled transition {iso(ts)} action {a} missing for {sorted(active - syms)}")
    # Exchange-substituted opens: while the schedule is closed, an unscheduled
    # PRE_OPEN (reason MARKET_EVENT) followed by an unscheduled TRADING means the
    # exchange replaced the scheduled open. The segment opens at that PRE_OPEN; the
    # time until trading is then detected below as an unscheduled halt.
    mkt = set()
    for sym, rs in per.items():
        for ts, a, rsn, _ in rs:
            if rsn == 3 and a in (PRE_OPEN, TRADING):
                mkt.add((ts, -a))   # marker: negative action = market-event transition
    events = sorted(set(sched) | mkt)  # (ts, action)
    sessions, segs, open_ts, problems = [], [], None, []
    substituted_opens = []
    cand = None
    for ts, a in events:
        if a < 0:
            if open_ts is None:
                if -a == PRE_OPEN and cand is None:
                    cand = ts
                elif -a == TRADING and cand is not None:
                    open_ts = cand
                    substituted_opens.append({"open": iso(cand), "first_trading": iso(ts)})
                    if cand % (60 * NS):
                        problems.append({"ts": iso(cand), "issue": "substituted open not minute-aligned"})
                    cand = None
            continue
        if a != TRADING:
            cand = None
        changes_state = (a == TRADING and open_ts is None) or (a != TRADING and open_ts is not None)
        if changes_state and ts % (60 * NS):
            problems.append({"ts": iso(ts), "issue": "state-changing scheduled transition not minute-aligned"})
        if a == TRADING:
            if open_ts is None:
                open_ts = ts
        else:
            if open_ts is not None:
                segs.append((open_ts, ts))
                open_ts = None
            # A scheduled NOT_AVAILABLE also ends the trade date (trader decision 2026-11-25:
            # Friday holidays 2026-06-19 / 2026-07-03 are their own trade dates, like 2025-07-04).
            if a in (CLOSE, NOT_AVAILABLE) and segs:
                sessions.append({"session_id": local(ts).date().isoformat(),
                                 "segments": [[iso(x), iso(y)] for x, y in segs]})
                segs = []
    if segs or open_ts is not None:
        problems.append({"issue": "trailing open segments without a scheduled CLOSE (dataset end)",
                         "segments": [[iso(x), iso(y)] for x, y in segs], "open_since": iso(open_ts) if open_ts else None})
    seg_list = [(datetime.fromisoformat(a.replace("Z", "+00:00")).timestamp() * NS,
                 datetime.fromisoformat(b.replace("Z", "+00:00")).timestamp() * NS)
                for s in sessions for a, b in s["segments"]]

    def in_segment(ts):
        return any(a <= ts < b for a, b in seg_list)

    # ---- unscheduled halts per contract
    halts = defaultdict(list)

    def record(sym, start, end_ts, ended_by):
        h0 = start[0]
        first_full = -(-h0 // (60 * NS))          # ceil to minute
        last_excl = end_ts // (60 * NS)            # floor
        halts[sym].append({"start": iso(h0), "end": iso(end_ts), "seconds": round((end_ts - h0) / NS, 3),
                           "action": start[1], "reason": start[2], "ended_by": ended_by,
                           "full_minutes": max(0, last_excl - first_full),
                           "full_minute_start": first_full, "full_minute_end": last_excl})

    for sym, rs in per.items():
        halt_start = None
        for ts, a, rsn, is_tr in rs:
            # A halt starts only on an explicit halting action with is_trading == False.
            # (action NONE / is_trading unknown records are "no change" and never start a halt;
            #  reason 5 = instrument expiration is not a halt.)
            if rsn not in (SCHEDULED, 0, 5) and is_tr is False and a in HALTING and in_segment(ts):
                if halt_start is None:
                    halt_start = (ts, a, rsn)
            elif a == TRADING and halt_start is not None:
                record(sym, halt_start, ts, "TRADING_RESUMED")
                halt_start = None
            elif rsn == SCHEDULED and a in ENDS and halt_start is not None:
                # halt runs into a scheduled non-trading phase: the halt ends where the schedule takes over
                record(sym, halt_start, ts, "SCHEDULED_TRANSITION")
                halt_start = None
    cal = {"calendar_id": cal_id, "version": f"status-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
           "verified": False, "verified_by": None,
           "source": "CME matching-engine status messages via Databento GLBX.MDP3 status schema; "
                     "scheduled transitions identical across all active outrights",
           "sessions": sessions}
    Path(out_cal).write_text(json.dumps(cal, indent=1))
    rep = {"sessions": len(sessions), "problems": problems, "substituted_opens": substituted_opens,
           "multi_segment_sessions": [s for s in sessions if len(s["segments"]) > 1],
           "early_or_irregular": [s for s in sessions if any(not b.endswith(("21:00:00Z", "22:00:00Z")) for _, b in s["segments"][-1:])],
           "unscheduled_halts": {s: v for s, v in halts.items()},
           "unscheduled_halts_with_full_minutes": {s: [h for h in v if h["full_minutes"] > 0] for s, v in halts.items()}}
    Path(out_rep).write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({"sessions": len(sessions), "problems": len(problems),
                      "multi_segment_sessions": len(rep["multi_segment_sessions"]),
                      "irregular_close_sessions": len(rep["early_or_irregular"]),
                      "halts_total": sum(len(v) for v in halts.values()),
                      "halts_with_full_minutes": {s: len(v) for s, v in rep["unscheduled_halts_with_full_minutes"].items() if v}},
                     indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
