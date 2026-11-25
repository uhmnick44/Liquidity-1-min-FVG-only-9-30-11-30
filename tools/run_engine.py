"""VALIDATION engine run on real data (verified calendars and instrument; no overrides).

Full pipeline with spec v1.2 policies (APPROVED_V12 + N22/N32/N33). The driver
refuses unverified configuration, so this run only succeeds on signed-off
calendars and instrument files. Liquidity-engine output only: no strategy logic.

Usage:
  python tools/run_engine.py OUT_DIR BARS_DIR[,BARS_DIR...] STATS_DIR [--status-halts HALTS.json]
"""
from __future__ import annotations

import glob
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from liqengine.calendars import ExchangeCalendar, ReferenceCalendar  # noqa: E402
from liqengine.data import BarStore, MinuteEvidence  # noqa: E402
from liqengine.dbn import _read, check_mapping, load_databento_dbn  # noqa: E402
from liqengine.driver import run  # noqa: E402
from liqengine.export import write_outputs  # noqa: E402
from liqengine.instrument import InstrumentConfig  # noqa: E402
from liqengine.policies import APPROVED_V12  # noqa: E402
from liqengine.statistics import convert  # noqa: E402


def main(out_dir, bar_dirs, stats_dir, *opts):
    t0 = time.time()
    opts = list(opts)
    ip = opts[opts.index("--instrument") + 1] if "--instrument" in opts else ROOT / "config/instruments/MNQ.json"
    cp = opts[opts.index("--calendar") + 1] if "--calendar" in opts else ROOT / "config/calendars/CME_GLBX_MNQ.json"
    inst = InstrumentConfig.load(ip)
    cme = ExchangeCalendar.load(cp)
    ny = ReferenceCalendar.load(ROOT / "config/calendars/NYSE.json")
    store = BarStore()
    for d in bar_dirs.split(","):
        for f in sorted(glob.glob(str(Path(d) / "*.ohlcv-1m.*.dbn.zst"))):
            if "-" not in f.split(".")[-3]:
                load_databento_dbn(f, inst, store)
    # statistics -> cleared volume (N13b, N32, N33) + venue session high/low (N18b)
    recs, maps = [], {}
    for f in glob.glob(str(Path(stats_dir) / "*.statistics.*.dbn.zst")):
        if "-" in f.split(".")[-3]:
            continue
        meta, rs = _read(f)
        maps[meta.symbols[0]] = [dict(m) for m in meta.mappings.get(meta.symbols[0], [])]
        recs += rs

    def symbol_of(iid, d):
        for sym, mp in maps.items():
            if check_mapping(mp, iid, d) in ("ok", "ok_end_inclusive"):
                return sym
        return None

    def bar_volume(sym, session):
        b = store.bars.get(sym)
        return None if b is None else sum(b[m].volume for m in session.minutes() if m in b)

    vols, coarse, srep = convert(recs, symbol_of, cme, inst, bar_volume=bar_volume)
    evidence = MinuteEvidence(source="none (N12: no zero-trade evidence)")
    if "--status-halts" in opts:
        h = json.loads(Path(opts[opts.index("--status-halts") + 1]).read_text())
        evidence.verified_halts = {k: set(v) for k, v in h["verified_halts"].items()}
        evidence.halt_sources = [h["source"]]
        evidence.source += " + status-derived halts (N22, pending approval)"
    t1 = time.time()
    s_from = opts[opts.index("--from-session") + 1] if "--from-session" in opts else cme.sessions[0].session_id
    res = run(inst, cme, ny, store, APPROVED_V12, s_from, cme.sessions[-1].session_id,
              evidence=evidence, coarse=coarse, external_volumes=vols)
    t2 = time.time()
    eng = res.engine
    res.provenance["RUN"] = "VALIDATION"
    write_outputs(eng, out_dir, res.provenance)
    ev = list(eng.events.values())
    summary = {
        "RUN": "VALIDATION", "load_s": round(t1 - t0, 1), "run_s": round(t2 - t1, 1),
        "sessions": len(cme.sessions), "final_contract": eng.contract, "suspended": eng.suspended,
        "rollovers": [{"minute": x["minute"], "old": x["old"], "new": x["new"], "delta": x["delta"]}
                      for x in eng.log if x["event"] == "ROLLOVER"],
        "selection_log_tail": res.selection_log[-5:],
        "references": len(eng.refs),
        "references_by_label": Counter(s.label.value for r in eng.refs.values() for s in r.sources),
        "references_consumed": sum(r.consumed for r in eng.refs.values()),
        "references_quarantined_now": sum(r.quarantined and not r.consumed for r in eng.refs.values()),
        "references_reinstated": sum(bool(r.reinstatements) for r in eng.refs.values()),
        "events": len(ev),
        "events_by_status": Counter(e.status.value for e in ev),
        "reject_reasons": Counter(e.reject_reason.value for e in ev if e.reject_reason),
        "invalidation_reasons": Counter(e.invalidation_reason.value for e in ev if e.invalidation_reason),
        "expiry_reasons": Counter(e.expiry_reason.value for e in ev if e.expiry_reason),
        "dual_sided": sum(e.dual_sided for e in ev),
        "data_quality": Counter(d["kind"] for d in eng.data_quality),
        "stats_convert": {"rejected_below_bar_volume": len(srep.rejected_below_bar_volume),
                          "ts_ref_mismatch": len(srep.ts_ref_mismatch), "revised": len(srep.revised_volume)},
    }
    Path(out_dir, "preflight_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main(*sys.argv[1:])
