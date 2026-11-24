"""Market-data ingestion and validation (A2, A3, I8).

Verified from official Databento documentation (docs/VENDOR_VERIFICATION.md):
- OHLCV ts_event is the inclusive START of the aggregation interval.
- If no trade occurs within the interval, no record is printed.
- Prices are int64 fixed-point with 1 unit = 1e-9 (or decimal strings when
  exported with pretty_px=true).

Validation never repairs data. Duplicates and malformed rows are hard
errors. Out-of-order rows are reported and then sorted (their content is
unchanged).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .instrument import InstrumentConfig
from .ticks import OffGridError, fixed_point_to_decimal, to_decimal
from .timeutil import dt_to_minute, ns_to_minute, parse_utc

RTYPE_OHLCV_1M = 33


class DataIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class Bar:
    minute: int       # open time (UTC minute); observable at minute + 1
    open: int         # ticks on the contract's grid
    high: int
    low: int
    close: int
    volume: int
    contract: str


class MinuteStatus(Enum):
    OBSERVED = "OBSERVED"
    VERIFIED_ZERO_TRADE = "VERIFIED_ZERO_TRADE"
    CONFIRMED_MISSING = "CONFIRMED_MISSING"
    UNCERTAIN = "UNCERTAIN"

    @property
    def is_missing_data(self) -> bool:
        return self in (MinuteStatus.CONFIRMED_MISSING, MinuteStatus.UNCERTAIN)


@dataclass
class LoadReport:
    rows: int = 0
    out_of_order: int = 0
    unknown_instrument_ids: dict = field(default_factory=dict)
    rejected_non_outright: dict = field(default_factory=dict)
    per_contract_rows: dict = field(default_factory=dict)


@dataclass
class MinuteEvidence:
    """Evidence used to classify scheduled minutes that have no bar (A3).

    verified_zero_trade / confirmed_missing: contract -> set of UTC minutes.
    Anything not listed and without a bar is UNCERTAIN. Evidence sources
    must be independently verified (I8). This class does not judge them.
    """
    verified_zero_trade: dict[str, set[int]] = field(default_factory=dict)
    confirmed_missing: dict[str, set[int]] = field(default_factory=dict)
    source: str = ""


class BarStore:
    def __init__(self):
        self.bars: dict[str, dict[int, Bar]] = {}

    def add(self, bar: Bar):
        d = self.bars.setdefault(bar.contract, {})
        if bar.minute in d:
            raise DataIntegrityError(f"duplicate bar {bar.contract} @ minute {bar.minute}")
        d[bar.minute] = bar

    def get(self, contract: str, minute: int) -> Bar | None:
        return self.bars.get(contract, {}).get(minute)

    def contracts(self):
        return sorted(self.bars)

    def classify(self, contract: str, minute: int, evidence: MinuteEvidence | None) -> MinuteStatus:
        if minute in self.bars.get(contract, {}):
            return MinuteStatus.OBSERVED
        if evidence is not None:
            if minute in evidence.verified_zero_trade.get(contract, ()):
                return MinuteStatus.VERIFIED_ZERO_TRADE
            if minute in evidence.confirmed_missing.get(contract, ()):
                return MinuteStatus.CONFIRMED_MISSING
        return MinuteStatus.UNCERTAIN


def _parse_ts(v) -> int:
    s = str(v).strip()
    if s.isdigit():
        return ns_to_minute(int(s))
    return dt_to_minute(parse_utc(s))


def _parse_px(v):
    s = str(v).strip()
    if s.lstrip("-").isdigit():
        return fixed_point_to_decimal(int(s))   # raw fixed-point integer
    return to_decimal(s)                        # pretty_px decimal string


def _rows_to_store(rows, instrument: InstrumentConfig, store: BarStore, report: LoadReport):
    prev = {}
    pending: list[Bar] = []
    for r in rows:
        report.rows += 1
        if "rtype" in r and r["rtype"] not in (None, "") and int(r["rtype"]) != RTYPE_OHLCV_1M:
            raise DataIntegrityError(f"row {report.rows}: rtype {r['rtype']} is not ohlcv-1m (33)")
        iid = int(r["instrument_id"])
        c = instrument.contract_by_instrument_id(iid)
        if c is None:
            report.unknown_instrument_ids[iid] = report.unknown_instrument_ids.get(iid, 0) + 1
            continue
        if not c.is_outright:
            report.rejected_non_outright[c.symbol] = report.rejected_non_outright.get(c.symbol, 0) + 1
            continue
        m = _parse_ts(r["ts_event"])
        g = c.grid
        try:
            o, h, lo, cl = (g.to_ticks(_parse_px(r[k])) for k in ("open", "high", "low", "close"))
        except OffGridError as exc:
            raise DataIntegrityError(f"row {report.rows} ({c.symbol}): {exc}") from exc
        if not (lo <= o <= h and lo <= cl <= h):
            raise DataIntegrityError(f"row {report.rows} ({c.symbol}): inconsistent OHLC")
        vol = int(r["volume"])
        if vol <= 0:
            raise DataIntegrityError(f"row {report.rows} ({c.symbol}): non-positive volume on a printed bar")
        if c.symbol in prev and m <= prev[c.symbol]:
            if m == prev[c.symbol]:
                raise DataIntegrityError(f"duplicate timestamp {c.symbol} minute {m}")
            report.out_of_order += 1
        prev[c.symbol] = max(m, prev.get(c.symbol, m))
        pending.append(Bar(m, o, h, lo, cl, vol, c.symbol))
        report.per_contract_rows[c.symbol] = report.per_contract_rows.get(c.symbol, 0) + 1
    for b in sorted(pending, key=lambda b: (b.contract, b.minute)):
        store.add(b)  # raises on duplicates that were not adjacent


def load_databento_csv(path: str | Path, instrument: InstrumentConfig, store: BarStore | None = None):
    store = store or BarStore()
    report = LoadReport()
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        missing = {"ts_event", "instrument_id", "open", "high", "low", "close", "volume"} - set(reader.fieldnames or [])
        if missing:
            raise DataIntegrityError(f"{path}: missing columns {sorted(missing)}")
        _rows_to_store(reader, instrument, store, report)
    return store, report


def load_databento_parquet(path: str | Path, instrument: InstrumentConfig, store: BarStore | None = None):
    import pyarrow.parquet as pq  # optional dependency

    store = store or BarStore()
    report = LoadReport()
    table = pq.read_table(path)
    cols = set(table.column_names)
    tscol = "ts_event" if "ts_event" in cols else None
    if tscol is None:
        raise DataIntegrityError(f"{path}: no ts_event column")
    data = table.to_pydict()
    n = table.num_rows
    rows = []
    for i in range(n):
        r = {k: data[k][i] for k in data}
        ts = r["ts_event"]
        if hasattr(ts, "isoformat"):  # pyarrow timestamp -> datetime
            r["ts_event"] = ts.isoformat()
        for k in ("open", "high", "low", "close"):
            if isinstance(r[k], float):
                raise DataIntegrityError(
                    f"{path}: float prices refused (inexact); export with fixed-point integer prices"
                )
        rows.append(r)
    _rows_to_store(rows, instrument, store, report)
    return store, report
