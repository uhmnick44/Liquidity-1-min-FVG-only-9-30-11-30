"""Build the instrument configuration from Databento `definition` records.

Usage:
  python tools/build_instrument_from_definitions.py DEF_DIR OUT.json ROOT EXCHANGE_CAL_ID REFERENCE_CAL_ID

Rules:
- Outrights only: instrument_class 'F' (future) and security_type 'FUT'.
  Spreads are excluded.
- Every field must be constant across all definition records of a contract
  (id, expiration, activation, tick). Any change aborts; nothing is guessed.
- Expiration cycle (I3): the contract months actually listed by the exchange.
  All are recorded as in-cycle. For MNQ these are quarterly H/M/U/Z.
"""
from __future__ import annotations

import glob
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from liqengine.dbn import _read  # noqa: E402


def iso(ns):
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(def_dir, out, root, exch_cal, ref_cal, cycle=None):
    """cycle: optional comma-separated month codes in the approved expiration cycle (I3), e.g. 'G,J,M,Q,Z'."""
    codes = "FGHJKMNQUVXZ"
    cycle_months = None if not cycle else {codes.index(c) + 1 for c in cycle.split(",")}
    contracts, months, uom = [], set(), set()
    by_id = {}
    for f in sorted(glob.glob(str(Path(def_dir) / "*.definition.*.dbn.zst"))):
        meta, recs = _read(f)
        sym = meta.symbols[0]
        if "-" in sym:
            continue
        for r in recs:
            uom.add((r.unit_of_measure_qty, str(r.unit_of_measure), r.currency))
            by_id.setdefault(r.instrument_id, set()).add(
                (sym, r.expiration, r.activation, r.min_price_increment, str(r.instrument_class),
                 str(r.security_type), r.maturity_year, r.maturity_month))
    raw_count = {}
    for iid, keys in by_id.items():
        if len(keys) != 1:
            raise SystemExit(f"instrument_id {iid}: definition fields change over time: {keys}")
        raw_count[next(iter(keys))[0]] = raw_count.get(next(iter(keys))[0], 0) + 1
    for iid, keys in sorted(by_id.items()):
        sym, exp, act, mpi, cls, sec, my, mm = next(iter(keys))
        if (cls, sec) != ("F", "FUT"):
            continue
        months.add(mm)
        # Exchange symbols reuse a one-digit year (e.g. MGCG7 = 2017 and 2027): make them unique.
        key = f"{sym}_{my}" if raw_count[sym] > 1 else sym
        contracts.append({"symbol": key, "raw_symbol": sym, "instrument_id": iid, "expiration": iso(exp),
                          "activation": iso(act), "maturity": f"{my}-{mm:02d}",
                          "tick_size": str(Decimal(mpi) / Decimal(10**9)),
                          "in_expiration_cycle": cycle_months is None or mm in cycle_months,
                          "is_outright": True})
    ticks = {c["tick_size"] for c in contracts}
    if len(ticks) != 1:
        raise SystemExit(f"contracts have different tick sizes: {ticks}")
    if len(uom) != 1:
        raise SystemExit(f"unit of measure differs across contracts: {uom}")
    qty, unit, ccy = uom.pop()
    if ccy != "USD":
        raise SystemExit(f"currency {ccy}")
    cfg = {
        "root": root, "has_expirations": True, "tick_size": ticks.pop(),
        "usd_per_point": str(Decimal(qty) / Decimal(10**9)), "unit_of_measure": unit,
        "approved_cycle": cycle or "all listed months",
        "exchange_calendar_id": exch_cal, "reference_calendar_id": ref_cal,
        "nominal_pre_window_end": None,
        "verified": True,
        "verified_by": "Databento GLBX.MDP3 definition schema (exchange security definitions); "
                       "cross-checked against 1m bars (all prices on grid; see data audit)",
        "sources": {"definitions": str(def_dir), "listed_months": sorted(months),
                    "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "contracts": sorted(contracts, key=lambda c: c["expiration"]),
    }
    Path(out).write_text(json.dumps(cfg, indent=1))
    print(f"{len(contracts)} outright contracts, months {sorted(months)}, tick {cfg['tick_size']}")


if __name__ == "__main__":
    main(*sys.argv[1:])
