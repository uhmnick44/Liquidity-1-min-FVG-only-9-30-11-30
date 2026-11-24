"""Databento DBN (.dbn / .dbn.zst) ohlcv-1m loader (A2, I8).

Batch files produced with split_symbols=true hold one raw symbol per file.
The DBN header maps that symbol to instrument_id over date ranges. Exchange
instrument ids can be reused over time, so every record's instrument_id is
checked against the mapping valid on the record's UTC date. An id is never
trusted on its own.

Requires the optional packages `databento-dbn` and `zstandard`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from .data import Bar, BarStore, DataIntegrityError, LoadReport
from .instrument import InstrumentConfig
from .ticks import OffGridError, fixed_point_to_decimal
from .timeutil import ns_to_minute


@dataclass
class DbnFileReport(LoadReport):
    path: str = ""
    symbol: str = ""
    header_start_ns: int = 0
    header_end_ns: int = 0
    mappings: list = field(default_factory=list)
    mapping_end_inclusive_hits: int = 0   # records valid only if end_date is treated as inclusive
    excluded_reason: str | None = None
    identical_cross_file_duplicates: int = 0


def _read(path: str | Path):
    import databento_dbn as dbn
    raw = Path(path).read_bytes()
    if str(path).endswith(".zst"):
        import zstandard
        raw = zstandard.ZstdDecompressor().stream_reader(raw).read()
    dec = dbn.DBNDecoder()
    dec.write(raw)
    recs = dec.decode()
    return recs[0], recs[1:]


def check_mapping(mappings: list[dict], iid: int, d: date) -> str:
    """Return 'ok', 'ok_end_inclusive' or 'mismatch' for instrument id on UTC date d.

    Databento mapping intervals are treated as [start_date, end_date). A
    match that only holds when end_date is inclusive is reported separately,
    because the convention is unverified."""
    for mp in mappings:
        if int(mp["symbol"]) == iid and mp["start_date"] <= d < mp["end_date"]:
            return "ok"
    for mp in mappings:
        if int(mp["symbol"]) == iid and d == mp["end_date"]:
            return "ok_end_inclusive"
    return "mismatch"


def load_databento_dbn(path: str | Path, instrument: InstrumentConfig, store: BarStore | None = None,
                       allowed_symbols: set[str] | None = None):
    store = store if store is not None else BarStore()
    meta, recs = _read(path)
    rep = DbnFileReport(path=str(path), header_start_ns=meta.start, header_end_ns=meta.end)
    if str(meta.schema) != "ohlcv-1m":
        raise DataIntegrityError(f"{path}: schema {meta.schema} is not ohlcv-1m")
    if len(meta.symbols) != 1:
        raise DataIntegrityError(f"{path}: expected one symbol per file (split_symbols), got {meta.symbols}")
    sym = meta.symbols[0]
    rep.symbol = sym
    rep.mappings = [dict(m) for m in meta.mappings.get(sym, [])]
    try:
        contract = instrument.contract(sym)
    except KeyError:
        rep.excluded_reason = "SYMBOL_NOT_IN_INSTRUMENT_CONFIG"
        rep.rows = len(recs)
        return store, rep
    if not contract.is_outright:
        rep.excluded_reason = "NOT_OUTRIGHT"
        rep.rows = len(recs)
        return store, rep
    if allowed_symbols is not None and sym not in allowed_symbols:
        rep.excluded_reason = "NOT_REQUESTED"
        return store, rep
    g = contract.grid
    prev = None
    pending = []
    for r in recs:
        rep.rows += 1
        if str(r.rtype) != "ohlcv-1m":
            raise DataIntegrityError(f"{path}: record rtype {r.rtype} is not ohlcv-1m")
        m = ns_to_minute(r.ts_event)
        d = datetime.fromtimestamp(r.ts_event // 10**9, tz=timezone.utc).date()
        chk = check_mapping(rep.mappings, r.instrument_id, d)
        if chk == "mismatch":
            raise DataIntegrityError(f"{path}: instrument_id {r.instrument_id} not mapped to {sym} on {d}")
        if chk == "ok_end_inclusive":
            rep.mapping_end_inclusive_hits += 1
        try:
            o, h, lo, c = (g.to_ticks(fixed_point_to_decimal(x)) for x in (r.open, r.high, r.low, r.close))
        except OffGridError as exc:
            raise DataIntegrityError(f"{path} minute {m}: {exc}") from exc
        if not (lo <= o <= h and lo <= c <= h):
            raise DataIntegrityError(f"{path} minute {m}: inconsistent OHLC")
        if r.volume <= 0:
            raise DataIntegrityError(f"{path} minute {m}: non-positive volume on printed bar")
        if prev is not None and m <= prev:
            if m == prev:
                raise DataIntegrityError(f"{path}: duplicate minute {m}")
            rep.out_of_order += 1
        prev = m if prev is None else max(prev, m)
        pending.append(Bar(m, o, h, lo, c, int(r.volume), sym))
    for b in sorted(pending, key=lambda b: b.minute):
        existing = store.get(sym, b.minute)
        if existing is not None:
            if existing != b:
                raise DataIntegrityError(f"{path}: conflicting duplicate {sym} minute {b.minute} across files")
            rep.identical_cross_file_duplicates += 1
            continue
        store.add(b)
    rep.per_contract_rows[sym] = len(pending)
    return store, rep
