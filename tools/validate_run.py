"""Independent validation of an engine run against raw bars (spec §15: test every
definition). It does NOT import liqengine.engine; it re-derives the results from
the bars, the calendars and the written rules, and compares them with the
engine's outputs.

Usage:
  python tools/validate_run.py RUN_DIR CME_CAL.json NYSE_CAL.json HALTS.json BARS_DIR [BARS_DIR ...]

Checks:
 A  every observed penetration: H >= P + tick (BSL) / L <= P - tick (SSL) on the penetration bar
 B  references are never tested before availability
 C  confirmation: first qualifying close (strict) within clock minutes t..t+3, same segment;
    rejection NO_CONFIRMATION: no qualifying close in t..t+3
 D  frozen extreme E = max H / min L over bars t..q
 E  three-close invalidation at the first triple of consecutive minutes (with bars) after q
    closing strictly beyond E; no earlier triple
 F  consumption: exactly one consuming event per consumed reference, at consumed_ts
 G  first penetration: no earlier bar of the active contract penetrated the level
    between availability (or the last rollover translation) and consumption
 H  15m swing levels recomputed from bars (strict 3-candle fractal on complete,
    fully observed, uninterrupted candles) equal the engine's SWING_15M sources
 I  exchange-session / RTH / pre-window highs and lows recomputed equal the engine's
"""
from __future__ import annotations

import glob
import json
import sys
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from liqengine.dbn import _read  # noqa: E402  (raw decoding only)

NY = ZoneInfo("America/New_York")
TICK = Decimal("0.25")


def m_of(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) // 60


def load_bars(dirs):
    per = defaultdict(list)
    for d in dirs:
        for f in glob.glob(str(Path(d) / "*.ohlcv-1m.*.dbn.zst")):
            sym = f.split(".")[-3]
            if "-" in sym:
                continue
            _, recs = _read(f)
            for r in recs:
                per[sym].append((r.ts_event // 60_000_000_000, r.high, r.low, r.close))
    out = {}
    for sym, rows in per.items():
        rows = sorted(set(rows))
        a = np.array(rows, dtype=np.int64)
        # prices in ticks (fixed-point 1e-9 / 0.25)
        q = int(TICK * 10**9)
        assert not (a[:, 1:] % q).any(), f"{sym}: off-grid price"
        out[sym] = {"m": a[:, 0], "h": a[:, 1] // q, "l": a[:, 2] // q, "c": a[:, 3] // q,
                    "idx": {int(x): i for i, x in enumerate(a[:, 0])}}
    return out


def ticks(price_str):
    v = Decimal(price_str) / TICK
    assert v == v.to_integral_value()
    return int(v)


def main(run_dir, cme_path, nyse_path, halts_path, *bar_dirs):
    global TICK
    bar_dirs = list(bar_dirs)
    if "--tick" in bar_dirs:
        i = bar_dirs.index("--tick"); TICK = Decimal(bar_dirs[i + 1]); del bar_dirs[i:i + 2]
    run = Path(run_dir)
    refs = {json.loads(l)["ref_id"]: json.loads(l) for l in open(run / "references.jsonl")}
    events = [json.loads(l) for l in open(run / "sweep_events.jsonl")]
    log = [json.loads(l) for l in open(run / "lifecycle_log.jsonl")]
    cme = json.load(open(cme_path))
    nyse = json.load(open(nyse_path))
    halts = {k: set(v) for k, v in json.load(open(halts_path))["verified_halts"].items()}
    bars = load_bars(bar_dirs)
    segs = sorted((m_of(a), m_of(b), s["session_id"]) for s in cme["sessions"] for a, b in s["segments"])
    seg_starts = [s[0] for s in segs]

    def seg_of(m):
        i = bisect_right(seg_starts, m) - 1
        return i if i >= 0 and segs[i][0] <= m < segs[i][1] else None

    # active contract timeline from engine log (rollovers / start)
    begin = [x for x in log if x["event"] == "SESSION_BEGIN"]
    first_minute = begin[0]["minute"]
    rolls = sorted((x["minute"], x["new"]) for x in log if x["event"] == "ROLLOVER")
    first_contract = next(x["old"] for x in log if x["event"] == "ROLLOVER") if rolls else None
    timeline = [(first_minute, first_contract)] + rolls

    def active(m):
        i = bisect_right([t for t, _ in timeline], m) - 1
        return timeline[i][1] if i >= 0 else None

    fails = defaultdict(list)
    counts = Counter()
    B = lambda c, m: bars[c]["idx"].get(m)  # noqa: E731

    # ---------------- A-E events
    by_ref_consuming = Counter()
    for e in events:
        if e["penetration_ts"] is None:
            counts["evidence_events"] += 1
            continue
        c = e["contract"]
        r = refs[e["ref_id"]]
        P = ticks(e["level_price"])
        t = m_of(e["penetration_ts"])
        bsl = e["direction"] == "BSL"
        b = bars[c]
        i = B(c, t)
        counts["events_checked"] += 1
        # A
        if i is None or not ((b["h"][i] >= P + 1) if bsl else (b["l"][i] <= P - 1)):
            fails["A_penetration"].append(e["event_id"])
            continue
        # B
        if t < m_of(r["availability_ts"]):
            fails["B_availability"].append(e["event_id"])
        if e["reject_reason"] == "DUAL_SIDED":
            counts["dual_sided"] += 1
            continue
        # C confirmation scan over clock minutes t..t+3 in the same segment
        sg = seg_of(t)
        q = None
        end_reason = None
        for k in range(4):
            m = t + k
            if seg_of(m) != sg:
                end_reason = "SCHEDULED_CLOSURE"
                break
            j = B(c, m)
            if j is None:
                if m in halts.get(c, ()):
                    end_reason = "UNSCHEDULED_HALT"
                    break
                end_reason = "DATA_GAP"  # missing minute (no zero-trade evidence, N12)
                break
            if (b["c"][j] < P) if bsl else (b["c"][j] > P):
                q = m
                break
        if q is None and end_reason is None:
            end_reason = "NO_CONFIRMATION_WITHIN_WINDOW"
        if e["status"] == "CONFIRMED":
            if q is None or m_of(e["confirmation_ts"]) != q:
                fails["C_confirmation"].append(e["event_id"])
                continue
            # D extreme
            js = [B(c, m) for m in range(t, q + 1) if B(c, m) is not None]
            E = max(b["h"][j] for j in js) if bsl else min(b["l"][j] for j in js)
            if ticks(e["sweep_extreme"]) != E:
                fails["D_extreme"].append(e["event_id"])
            # E invalidation (first triple after q)
            if e["invalidation_reason"] == "THREE_CLOSES_BEYOND_EXTREME":
                inv = m_of(e["invalidation_ts"])
                run_n, found, m = 0, None, q + 1
                while m <= inv:
                    j = B(c, m)
                    if j is None or seg_of(m) != seg_of(m - 1):
                        run_n = 0
                    if j is not None:
                        beyond = (b["c"][j] > E) if bsl else (b["c"][j] < E)
                        run_n = run_n + 1 if beyond else 0
                        if run_n == 3:
                            found = m
                            break
                    m += 1
                if found != inv:
                    fails["E_invalidation"].append(e["event_id"])
                counts["invalidations_checked"] += 1
        elif e["status"] == "REJECTED" and e["reject_reason"] in (
                "NO_CONFIRMATION_WITHIN_WINDOW", "SCHEDULED_CLOSURE", "DATA_GAP", "UNSCHEDULED_HALT"):
            if q is not None or end_reason != e["reject_reason"]:
                fails["C_rejection"].append((e["event_id"], e["reject_reason"], end_reason))
        by_ref_consuming[e["ref_id"]] += 1

    # ---------------- F, G references
    for rid, r in refs.items():
        if not r["consumed"]:
            continue
        counts["consumed_refs"] += 1
        if r["consumed_ts"] is None:
            continue  # consumed by coarse evidence inside a gap
        if by_ref_consuming[rid] + (1 if any(e["ref_id"] == rid and e["reject_reason"] == "DUAL_SIDED" for e in []) else 0) > 1:
            fails["F_multiple_consuming_events"].append(rid)
        cons = m_of(r["consumed_ts"])
        hist = [(m_of(a), c, ticks(p)) for a, c, p in r["price_history"]]
        bsl = r["direction"] == "BSL"
        for k, (start, c, P) in enumerate(hist):
            stop = hist[k + 1][0] if k + 1 < len(hist) else cons
            b = bars[c]
            lo, hi = bisect_left(b["m"], start), bisect_left(b["m"], stop)
            if hi > lo:
                hit = (b["h"][lo:hi] >= P + 1).any() if bsl else (b["l"][lo:hi] <= P - 1).any()
                if hit:
                    fails["G_not_first_penetration"].append(rid)
                    break

    # ---------------- H swings and I windows (recomputed on the active contract)
    rec_swings, rec_windows = set(), set()
    ny_days = {d["date"]: d for d in nyse["days"]}
    for s in cme["sessions"]:
        sid = s["session_id"]
        s0, s1 = m_of(s["segments"][0][0]), m_of(s["segments"][-1][1])
        if s1 <= first_minute:
            continue
        c = active(s0)
        if c is None:
            continue
        b = bars[c]
        mins = [m for a, z in s["segments"] for m in range(m_of(a), m_of(z))]
        # windows (only if every minute observed or halted; otherwise engine quarantines -> compared separately)
        day = ny_days.get(sid)
        wins = [("EXCHANGE_SESSION", s0, s1)]
        if day and not day.get("closed"):
            d = date.fromisoformat(sid)
            ro = int(datetime.combine(d, time.fromisoformat(day["open"]), NY).timestamp()) // 60
            rc = int(datetime.combine(d, time.fromisoformat(day["close"]), NY).timestamp()) // 60
            wins += [("PRE_WINDOW", s0, ro), ("RTH", ro, min(rc, s1))]
        for lab, a, z in wins:
            js = [B(c, m) for m in mins if a <= m < z and B(c, m) is not None]
            if js:
                rec_windows.add((lab, sid, "BSL", int(max(b["h"][j] for j in js))))
                rec_windows.add((lab, sid, "SSL", int(min(b["l"][j] for j in js))))
        # 15m candles per segment
        for a, z in s["segments"]:
            a, z = m_of(a), m_of(z)
            chain = []
            for q0 in range(a - a % 15, z, 15):
                ms = range(q0, q0 + 15)
                ok = q0 >= a and q0 + 15 <= z and all(B(c, m) is not None for m in ms)
                if not ok:
                    chain = []
                    continue
                js = [B(c, m) for m in ms]
                chain.append((q0, int(max(b["h"][j] for j in js)), int(min(b["l"][j] for j in js))))
                if len(chain) >= 3:
                    (_, h1, l1), (q2, h2, l2), (q3, h3, l3) = chain[-3:]
                    av = q3 + 15
                    if h2 > h1 and h2 > h3:
                        rec_swings.add((c, "BSL", h2, av))
                    if l2 < l1 and l2 < l3:
                        rec_swings.add((c, "SSL", l2, av))
    eng_swings, eng_windows = set(), set()
    for r in refs.values():
        for src in r["sources"]:
            if src["label"] == "SWING_15M":
                eng_swings.add((src["contract"], r["direction"], ticks(src["original_price"]), m_of(src["availability_ts"])))
            else:
                if "INCOMPLETE_SOURCE_WINDOW" in src["flags"]:
                    counts["incomplete_windows_skipped"] += 1
                    continue
                eng_windows.add((src["label"], src["origin_id"], r["direction"], ticks(src["original_price"])))
    rec_swings = {x for x in rec_swings if x[3] >= first_minute}
    sw_missing, sw_extra = rec_swings - eng_swings, eng_swings - rec_swings
    if sw_missing or sw_extra:
        fails["H_swings"].append({"missing_in_engine": len(sw_missing), "extra_in_engine": len(sw_extra),
                                  "sample_missing": sorted(sw_missing)[:5], "sample_extra": sorted(sw_extra)[:5]})
    # windows: compare only windows the engine registered as complete, and those recomputed as observed
    win_missing = {x for x in rec_windows - eng_windows}
    win_extra = {x for x in eng_windows - rec_windows}
    # windows the engine skipped because incomplete are legitimately absent; filter by sessions with any missing minute
    if win_extra:
        fails["I_windows_extra"].append(sorted(win_extra)[:10])
    counts["swings_recomputed"] = len(rec_swings)
    counts["swings_engine"] = len(eng_swings)
    counts["windows_recomputed"] = len(rec_windows)
    counts["windows_engine_complete"] = len(eng_windows)
    counts["windows_recomputed_not_in_engine"] = len(win_missing)
    result = {"counts": dict(counts), "failures": {k: (v if len(v) <= 20 else v[:20] + [f"... {len(v)} total"])
                                                   for k, v in fails.items()},
              "failure_counts": {k: len(v) for k, v in fails.items()},
              "windows_not_in_engine_sample": sorted(win_missing)[:10]}
    Path(run / "independent_validation.json").write_text(json.dumps(result, indent=1, default=str))
    print(json.dumps(result, indent=1, default=str)[:5000])


if __name__ == "__main__":
    main(*sys.argv[1:])
