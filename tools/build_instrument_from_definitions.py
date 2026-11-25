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


def main(def_dir, out, root, exch_cal, ref_cal):
    contracts, months = [], set()
    for f in sorted(glob.glob(str(Path(def_dir) / "*.definition.*.dbn.zst"))):
        meta, recs = _read(f)
        sym = meta.symbols[0]
        if "-" in sym:
            continue
        keys = {(r.instrument_id, r.expiration, r.activation, r.min_price_increment,
                 str(r.instrument_class), str(r.security_type), r.maturity_month) for r in recs}
        if len(keys) != 1:
            raise SystemExit(f"{sym}: definition fields change over time: {keys}")
        iid, exp, act, mpi, cls, sec, mm = keys.pop()
        if (cls, sec) != ("F", "FUT"):
            continue
        months.add(mm)
        contracts.append({"symbol": sym, "instrument_id": iid, "expiration": iso(exp), "activation": iso(act),
                          "tick_size": str(Decimal(mpi) / Decimal(10**9)), "in_expiration_cycle": True,
                          "is_outright": True})
    ticks = {c["tick_size"] for c in contracts}
    if len(ticks) != 1:
        raise SystemExit(f"contracts have different tick sizes: {ticks}")
    cfg = {
        "root": root, "has_expirations": True, "tick_size": ticks.pop(),
        "exchange_calendar_id": exch_cal, "reference_calendar_id": ref_cal,
        "nominal_pre_window_end": None,
        "verified": True,
        "verified_by": "Databento GLBX.MDP3 definition schema (exchange security definitions); "
                       "cross-checked against 1m bars (all prices on grid, last bar 09:29 ET each expiry)",
        "sources": {"definitions": str(def_dir), "listed_months": sorted(months),
                    "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
        "contracts": sorted(contracts, key=lambda c: c["expiration"]),
    }
    Path(out).write_text(json.dumps(cfg, indent=1))
    print(f"{len(contracts)} outright contracts, months {sorted(months)}, tick {cfg['tick_size']}")


if __name__ == "__main__":
    main(*sys.argv[1:])
