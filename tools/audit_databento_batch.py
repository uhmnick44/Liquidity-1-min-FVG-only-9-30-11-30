"""Dataset audit for Databento ohlcv-1m batch downloads (spec Part VI step 7, A3, I8).

Usage:
  python tools/audit_databento_batch.py OUT.json BATCH_DIR [BATCH_DIR ...]

What it does:
- Verifies every file's SHA-256 and size against the batch manifest.
- Loads outright contracts with full integrity checks (see liqengine/dbn.py):
  instrument-id/date mapping, tick grid, OHLC consistency, volume, duplicates,
  ordering, and cross-file overlap.
- Builds a CANDIDATE exchange calendar from pandas_market_calendars
  "CME_Equity". It is a third-party library, NOT an official source, so it is
  marked verified=false. It is used only to measure where data and schedule
  disagree, which is input for human verification of the real calendar.
- Measures missing scheduled minutes on the highest-volume outright of each
  session. "Highest-volume" here is descriptive only; it is NOT the spec's
  rollover rule.

Tick size 0.25 is taken from spec v1.0 §2 (user-provided) and still needs
checking against CME/Databento definitions. The data either fits that grid or
the load fails.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from liqengine.calendars import ExchangeCalendar, ExchangeSession  # noqa: E402
from liqengine.data import BarStore  # noqa: E402
from liqengine.dbn import load_databento_dbn  # noqa: E402
from liqengine.instrument import Contract, InstrumentConfig  # noqa: E402
from liqengine.timeutil import dt_to_minute, minute_to_iso  # noqa: E402

NY = ZoneInfo("America/New_York")
FAR = 10**12  # AUDIT ONLY: expirations unknown (no definition data); never used here


def manifest_check(d: Path):
    m = json.loads((d / "manifest.json").read_text())
    bad = []
    for f in m["files"]:
        p = d / f["filename"]
        h = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
        if h != f["hash"] or p.stat().st_size != f["size"]:
            bad.append(f["filename"])
    return {"job_id": m["job_id"], "files": len(m["files"]), "hash_or_size_mismatch": bad}


def candidate_calendar(start, end):
    import pandas_market_calendars as pmc
    cal = pmc.get_calendar("CME_Equity")
    sch = cal.schedule(start, end)
    sessions = []
    for idx, row in sch.iterrows():
        o, c = row.market_open.to_pydatetime(), row.market_close.to_pydatetime()
        bs, be = row.break_start, row.break_end
        segs = []
        if bs is not None and be is not None and be.to_pydatetime() > bs.to_pydatetime():
            segs = [(dt_to_minute(o), dt_to_minute(bs.to_pydatetime())), (dt_to_minute(be.to_pydatetime()), dt_to_minute(c))]
        else:
            segs = [(dt_to_minute(o), dt_to_minute(c))]
        sessions.append(ExchangeSession(idx.date().isoformat(), tuple(segs)))
    return ExchangeCalendar("CANDIDATE_pmc_CME_Equity", f"pandas_market_calendars-{pmc.__version__}", False,
                            "third-party library; NOT verified", sessions)


def main(out_path, *dirs):
    dirs = [Path(d) for d in dirs]
    report = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "batches": []}
    files, conditions = [], {}
    for d in dirs:
        meta = json.loads((d / "metadata.json").read_text())
        report["batches"].append({"dir": d.name, "manifest": manifest_check(d), "query": meta["query"],
                                  "customizations": meta["customizations"]})
        for c in json.loads((d / "condition.json").read_text()):
            prev = conditions.get(c["date"])
            if prev is not None and prev != c["condition"]:
                report.setdefault("condition_conflicts", []).append(c["date"])
            conditions[c["date"]] = c["condition"]
        files += sorted(d.glob("*.dbn.zst"))
    symbols = sorted({f.name.split(".")[-3] for f in files})
    outrights = [s for s in symbols if "-" not in s]
    spreads = [s for s in symbols if "-" in s]
    inst = InstrumentConfig(
        root="MNQ", has_expirations=True, tick_size=Decimal("0.25"), exchange_calendar_id="CANDIDATE",
        reference_calendar_id=None, verified=False,
        contracts=[Contract(s, None, FAR, Decimal("0.25"), True, True) for s in outrights]
        + [Contract(s, None, FAR, Decimal("0.25"), False, False) for s in spreads],
    )
    store = BarStore()
    file_reports = []
    for f in sorted(files, key=lambda p: (p.name.split(".")[-3], p.name)):
        _, rep = load_databento_dbn(f, inst, store)
        file_reports.append({
            "file": f"{f.parent.name}/{f.name}", "symbol": rep.symbol, "records": rep.rows,
            "excluded": rep.excluded_reason, "out_of_order": rep.out_of_order,
            "identical_cross_file_duplicates": rep.identical_cross_file_duplicates,
            "mapping_end_inclusive_hits": rep.mapping_end_inclusive_hits,
            "mappings": [{"start": str(m["start_date"]), "end": str(m["end_date"]), "id": m["symbol"]} for m in rep.mappings],
        })
    report["files"] = file_reports
    report["spreads_excluded"] = spreads
    report["contracts"] = {s: {"bars": len(store.bars.get(s, {})),
                               "first": minute_to_iso(min(store.bars[s])) if store.bars.get(s) else None,
                               "last": minute_to_iso(max(store.bars[s])) if store.bars.get(s) else None}
                           for s in outrights}

    all_min = [m for s in outrights for m in store.bars.get(s, {})]
    first, last = min(all_min), max(all_min)
    f_dt = datetime.fromtimestamp(first * 60, tz=timezone.utc)
    l_dt = datetime.fromtimestamp(last * 60, tz=timezone.utc)
    cal = candidate_calendar(f_dt.date().isoformat(), l_dt.date().isoformat())
    report["candidate_calendar"] = {"id": cal.calendar_id, "version": cal.version, "verified": False,
                                    "sessions": len(cal.sessions)}

    # ---- highest-volume outright per candidate session (descriptive)
    sess_rows = []
    miss_hour = Counter()
    miss_window = 0
    window_sched = 0
    miss_total = sched_total = 0
    run_hist = Counter()
    miss_on_degraded = miss_on_available = 0
    other_live = 0
    vol_verifiable = Counter()
    sessions_with_any_miss = 0
    for s in cal.sessions:
        vols = {}
        for sym in outrights:
            b = store.bars.get(sym, {})
            v = sum(b[m].volume for m in s.minutes() if m in b)
            if v:
                vols[sym] = v
        if not vols:
            sess_rows.append({"session": s.session_id, "front": None})
            continue
        ranked = sorted(vols, key=lambda k: -vols[k])
        front = ranked[0]
        fb = store.bars[front]
        missing = [m for m in s.minutes() if m not in fb]
        sched = sum(b - a for a, b in s.segments)
        sched_total += sched
        miss_total += len(missing)
        if missing:
            sessions_with_any_miss += 1
        run = 0
        prev = None
        for m in missing:
            run = run + 1 if prev is not None and m == prev + 1 else 1
            if prev is not None and m != prev + 1:
                run_hist[cur] += 1
            cur = run
            prev = m
        if missing:
            run_hist[cur] += 1
        for m in missing:
            lt = datetime.fromtimestamp(m * 60, tz=timezone.utc).astimezone(NY)
            miss_hour[lt.hour] += 1
            day = datetime.fromtimestamp(m * 60, tz=timezone.utc).date().isoformat()
            if conditions.get(day) == "degraded":
                miss_on_degraded += 1
            else:
                miss_on_available += 1
            if any(m in store.bars.get(o, {}) for o in ranked[1:]):
                other_live += 1
            if (lt.hour, lt.minute) >= (9, 30) and (lt.hour, lt.minute) < (11, 30):
                miss_window += 1
        for m in s.minutes():
            lt = datetime.fromtimestamp(m * 60, tz=timezone.utc).astimezone(NY)
            if (lt.hour, lt.minute) >= (9, 30) and (lt.hour, lt.minute) < (11, 30):
                window_sched += 1
        for i, sym in enumerate(ranked[:2]):
            b = store.bars[sym]
            if all(m in b for m in s.minutes()):
                vol_verifiable["front" if i == 0 else "second"] += 1
        sess_rows.append({"session": s.session_id, "front": front, "front_volume": vols[front],
                          "second": ranked[1] if len(ranked) > 1 else None,
                          "second_volume": vols[ranked[1]] if len(ranked) > 1 else None,
                          "front_missing_minutes": len(missing), "scheduled_minutes": sched})
    report["front_month_gap_analysis"] = {
        "note": "front = highest observed volume in the candidate session (descriptive, not the rollover rule)",
        "scheduled_minutes": sched_total, "missing_minutes": miss_total,
        "missing_fraction": miss_total / sched_total if sched_total else None,
        "sessions_with_any_missing": sessions_with_any_miss,
        "missing_in_0930_1130_ET": miss_window, "scheduled_in_0930_1130_ET": window_sched,
        "missing_by_ET_hour": {str(k): v for k, v in sorted(miss_hour.items())},
        "missing_run_lengths": {str(k): v for k, v in sorted(run_hist.items())},
        "missing_on_degraded_utc_days": miss_on_degraded, "missing_on_available_utc_days": miss_on_available,
        "missing_while_another_MNQ_outright_printed": other_live,
        "sessions_where_all_minutes_observed": dict(vol_verifiable),
    }
    report["sessions"] = sess_rows

    # front changes (descriptive)
    changes = []
    prev = None
    for r in sess_rows:
        if r.get("front") and r["front"] != prev:
            changes.append({"session": r["session"], "front": r["front"]})
            prev = r["front"]
    report["highest_volume_contract_changes"] = changes

    # ---- bars outside candidate schedule, per front contract
    outside = defaultdict(Counter)
    for sym in outrights:
        for m in store.bars.get(sym, {}):
            if cal.covers(m) and cal.session_at(m) is None:
                lt = datetime.fromtimestamp(m * 60, tz=timezone.utc).astimezone(NY)
                outside[sym][lt.date().isoformat()] += 1
    report["bars_outside_candidate_schedule"] = {
        s: {"total": sum(c.values()), "by_local_date": dict(sorted(c.items()))} for s, c in outside.items()}
    degraded = sorted(d for d, c in conditions.items() if c != "available")
    # N28 (approved): flag every session that touches a degraded UTC date (audit metadata only)
    touch = {}
    for dd in degraded:
        day0 = dt_to_minute(datetime.fromisoformat(dd).replace(tzinfo=timezone.utc))
        touch[dd] = [s.session_id for s in cal.sessions if s.start < day0 + 1440 and s.end > day0]
    report["condition"] = {"degraded_days": degraded, "days": len(conditions),
                           "sessions_touching_degraded_days": touch}
    Path(out_path).write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
    print(json.dumps({k: report[k] for k in ("candidate_calendar", "front_month_gap_analysis", "condition")}, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
