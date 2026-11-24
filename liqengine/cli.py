"""Command line: dataset integrity audit (spec Part VI step 7).

python -m liqengine.cli audit --instrument cfg/instrument.json --calendar cfg/exchange.json \
    [--evidence evidence.json] data1.csv [data2.parquet ...] > audit.json
"""
from __future__ import annotations

import argparse
import json
import sys

from .audit import audit, audit_json
from .calendars import ExchangeCalendar
from .data import BarStore, MinuteEvidence, load_databento_csv, load_databento_parquet
from .instrument import InstrumentConfig
from .timeutil import dt_to_minute, parse_utc


def _load_evidence(path):
    d = json.loads(open(path).read())

    def expand(ranges):
        out = set()
        for a, b in ranges:
            out.update(range(dt_to_minute(parse_utc(a)), dt_to_minute(parse_utc(b))))
        return out
    return MinuteEvidence(
        {c: expand(v.get("verified_zero_trade", [])) for c, v in d["contracts"].items()},
        {c: expand(v.get("confirmed_missing", [])) for c, v in d["contracts"].items()},
        source=d.get("source", path),
    )


def main(argv=None):
    ap = argparse.ArgumentParser(prog="liqengine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit")
    a.add_argument("--instrument", required=True)
    a.add_argument("--calendar", required=True)
    a.add_argument("--evidence")
    a.add_argument("files", nargs="+")
    args = ap.parse_args(argv)
    inst = InstrumentConfig.load(args.instrument)
    cal = ExchangeCalendar.load(args.calendar)
    ev = _load_evidence(args.evidence) if args.evidence else None
    store, reports = BarStore(), []
    for f in args.files:
        loader = load_databento_parquet if f.endswith(".parquet") else load_databento_csv
        _, rep = loader(f, inst, store)
        reports.append(rep)
    sys.stdout.write(audit_json(audit(store, cal, ev, load_reports=reports)) + "\n")


if __name__ == "__main__":
    main()
