"""Run the strategy backtest on a validated engine run.

Usage:
  python tools/run_backtest.py ENGINE_RUN_DIR OUT_DIR BARS_DIR [BARS_DIR ...] [--s1 SKIP] [--s2 no|yes]

Inputs are read only: engine outputs (sweep_events.jsonl), DBN bars, verified calendars.
"""
from __future__ import annotations

import glob
import json
import sys
from collections import Counter
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from liqengine.dbn import _read  # noqa: E402
from strategy.backtest import NY, Bar, Market, Params, build_setups, local, m_of, simulate  # noqa: E402
import strategy.backtest as bt  # noqa: E402
from strategy.stats import breakdown, breakeven_commission, half_hour, risk_bucket, summarize  # noqa: E402

Q = 250_000_000  # tick in 1e-9 units (set from the instrument config in main)


def load_bars(dirs, q=None, id_map=None):
    """id_map: instrument_id -> contract key (symbols can repeat across decades)."""
    q = q or Q
    out = {}
    for d in dirs:
        for f in glob.glob(str(Path(d) / "*.ohlcv-1m.*.dbn.zst")):
            sym = f.split(".")[-3]
            if "-" in sym:
                continue
            _, recs = _read(f)
            for r in recs:
                key = id_map.get(r.instrument_id) if id_map else sym
                if key is None:
                    continue
                rows = out.setdefault(key, {})
                assert not (r.open % q or r.high % q or r.low % q or r.close % q), f"{sym}: off-grid price"
                rows[r.ts_event // 60_000_000_000] = Bar(r.ts_event // 60_000_000_000, r.open // q, r.high // q, r.low // q, r.close // q)
    return {s: list(v.values()) for s, v in out.items()}


def market(bars, cal_path=None):
    cme = json.load(open(cal_path or ROOT / "config/calendars/CME_GLBX_MNQ.json"))
    ny = {d["date"]: d for d in json.load(open(ROOT / "config/calendars/NYSE.json"))["days"]}
    assert cme["verified"], "CME calendar not verified"
    segs, rth = [], {}
    for s in cme["sessions"]:
        for a, b in s["segments"]:
            segs.append((m_of(a), m_of(b), s["session_id"]))
        d = ny.get(s["session_id"])
        if d is None or d.get("closed"):
            rth[s["session_id"]] = None
        else:
            dd = date.fromisoformat(s["session_id"])
            rc = int(datetime.combine(dd, time.fromisoformat(d["close"]), NY).timestamp()) // 60
            rth[s["session_id"]] = min(rc, m_of(s["segments"][-1][1]))
    return Market(bars, segs, rth)


def main(run_dir, out_dir, *rest):
    args = list(rest)
    s1 = s2 = None
    if "--s1" in args:
        i = args.index("--s1"); s1 = args[i + 1]; del args[i:i + 2]
    inst_path, cal_path = ROOT / "config/instruments/MNQ.json", None
    if "--instrument" in args:
        i = args.index("--instrument"); inst_path = Path(args[i + 1]); del args[i:i + 2]
    if "--calendar" in args:
        i = args.index("--calendar"); cal_path = Path(args[i + 1]); del args[i:i + 2]
    since = until = None
    if "--from" in args:
        i = args.index("--from"); since = date.fromisoformat(args[i + 1]); del args[i:i + 2]
    if "--to" in args:
        i = args.index("--to"); until = date.fromisoformat(args[i + 1]); del args[i:i + 2]
    icfg = json.load(open(inst_path))
    assert icfg["verified"], "instrument config not verified"
    bt.configure(Decimal(icfg["tick_size"]), Decimal(icfg["usd_per_point"]))
    q = int(Decimal(icfg["tick_size"]) * 10**9)
    variant = {}
    if "--stop-mult" in args:
        i = args.index("--stop-mult"); variant["stop_multiple"] = int(args[i + 1]); del args[i:i + 2]
    if "--target-r" in args:
        i = args.index("--target-r"); variant["target_r"] = Decimal(args[i + 1]); del args[i:i + 2]
    split = None
    if "--split" in args:
        i = args.index("--split"); split = date.fromisoformat(args[i + 1]); del args[i:i + 2]
    if "--s2" in args:
        i = args.index("--s2"); s2 = args[i + 1] == "yes"; del args[i:i + 2]
    events = [json.loads(l) for l in open(Path(run_dir) / "sweep_events.jsonl")]
    id_map = {c["instrument_id"]: c["symbol"] for c in icfg["contracts"] if c.get("instrument_id") is not None}
    mkt = market(load_bars(args, q, id_map), cal_path)
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    results = {}
    kw = dict(trade_reference_closed_days=s2, opposite_same_candle=s1, **variant)
    scenarios = {"base": Params(**kw)}
    for slip in (0, 2):
        scenarios[f"slippage_{slip}"] = Params(**kw, entry_slippage=slip, stop_slippage=slip, exit_slippage=slip)
    scenarios["target_touch_fill"] = Params(**kw, target_through_ticks=0)
    log = []
    for name, p in scenarios.items():
        setups = build_setups(events, mkt, p, log if name == "base" else [])
        trades, skipped = simulate(setups, mkt, p)
        if since or until:
            trades = [t for t in trades if (not since or local(t.entry_minute).date() >= since)
                      and (not until or local(t.entry_minute).date() <= until)]
        res = {"setups": len(setups), "skipped": dict(Counter(r for _, r in skipped)),
               "gross": summarize(trades),
               "breakeven_commission_rt_usd": breakeven_commission(trades)}
        if split is not None:
            ins = [t for t in trades if local(t.entry_minute).date() < split]
            oos = [t for t in trades if local(t.entry_minute).date() >= split]
            res["in_sample"] = {c: summarize(ins, Decimal(c)) for c in ("0", "0.50", "1.00", "1.50")}
            res["out_of_sample"] = {c: summarize(oos, Decimal(c)) for c in ("0", "0.50", "1.00", "1.50")}
            for part, ts in (("in_sample", ins), ("out_of_sample", oos)):
                rs = [float(t.pnl_ticks * bt.USD_PER_TICK - Decimal("1.00")) / float(t.risk * bt.USD_PER_TICK) for t in ts]
                if len(rs) > 1:
                    mu = sum(rs) / len(rs)
                    se = (sum((x - mu) ** 2 for x in rs) / (len(rs) - 1)) ** 0.5 / len(rs) ** 0.5
                    res[part]["r_at_1usd_mean_se"] = [round(mu, 4), round(se, 4)]
        if name == "base":
            for c in ("0.50", "1.00", "1.50"):
                res[f"net_commission_rt_{c}"] = summarize(trades, Decimal(c))
            res["exit_reasons"] = dict(Counter(t.reason for t in trades))
            res["ambiguous_same_bar_stop_assumed"] = sum(t.ambiguous_bar for t in trades)
            res["by_side"] = breakdown(trades, lambda t: t.side)
            res["by_k"] = breakdown(trades, lambda t: t.setup.k)
            res["by_risk_ticks"] = breakdown(trades, risk_bucket)
            res["by_entry_half_hour"] = breakdown(trades, half_hour)
            res["by_year"] = breakdown(trades, lambda t: local(t.entry_minute).year)
            res["by_label_set"] = breakdown(trades, lambda t: "+".join(t.setup.labels))
            with open(out / "trades.jsonl", "w") as fh:
                for t in trades:
                    fh.write(json.dumps(t.to_dict()) + "\n")
            res["setup_build_issues"] = log
        results[name] = res
    (out / "backtest_results.json").write_text(json.dumps(results, indent=1, default=str))
    b = results["base"]
    print(json.dumps({"setups": b["setups"], "skipped": b["skipped"], "gross": b["gross"],
                      "breakeven_commission_rt_usd": b["breakeven_commission_rt_usd"]}, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
