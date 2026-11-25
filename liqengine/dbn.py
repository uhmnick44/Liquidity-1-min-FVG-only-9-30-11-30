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
    # Contract resolution. Exchange symbols can repeat across decades (one-digit year, e.g. MGCG7 = 2017 and 2027),
    # so a record is attributed by its instrument_id when the configuration lists ids. The file symbol is used only
    # for configurations without ids.
    by_id = {c.instrument_id: c for c in instrument.contracts if c.instrument_id is not None}
    if not by_id:
        try:
            fixed = instrument.contract(sym)
        except KeyError:
            rep.excluded_reason = "SYMBOL_NOT_IN_INSTRUMENT_CONFIG"
            rep.rows = len(recs)
            return store, rep
        if not fixed.is_outright:
            rep.excluded_reason = "NOT_OUTRIGHT"
            rep.rows = len(recs)
            return store, rep
        if allowed_symbols is not None and sym not in allowed_symbols:
            rep.excluded_reason = "NOT_REQUESTED"
            return store, rep
    prev = {}
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
        if by_id:
            contract = by_id.get(r.instrument_id)
            if contract is None or not contract.is_outright:
                rep.unknown_instrument_ids[r.instrument_id] = rep.unknown_instrument_ids.get(r.instrument_id, 0) + 1
                continue
            if allowed_symbols is not None and contract.symbol not in allowed_symbols:
                continue
        else:
            contract = fixed
        key = contract.symbol
        try:
            o, h, lo, c = (contract.grid.to_ticks(fixed_point_to_decimal(x)) for x in (r.open, r.high, r.low, r.close))
        except OffGridError as exc:
            raise DataIntegrityError(f"{path} minute {m}: {exc}") from exc
        if not (lo <= o <= h and lo <= c <= h):
            raise DataIntegrityError(f"{path} minute {m}: inconsistent OHLC")
        if r.volume <= 0:
            raise DataIntegrityError(f"{path} minute {m}: non-positive volume on printed bar")
        pm = prev.get(key)
        if pm is not None and m <= pm:
            if m == pm:
                raise DataIntegrityError(f"{path}: duplicate minute {m} for {key}")
            rep.out_of_order += 1
        prev[key] = m if pm is None else max(pm, m)
        pending.append(Bar(m, o, h, lo, c, int(r.volume), key))
    for b in sorted(pending, key=lambda b: (b.contract, b.minute)):
        existing = store.get(b.contract, b.minute)
        if existing is not None:
            if existing != b:
                raise DataIntegrityError(f"{path}: conflicting duplicate {b.contract} minute {b.minute} across files")
            rep.identical_cross_file_duplicates += 1
            continue
        store.add(b)
    for b in pending:
        rep.per_contract_rows[b.contract] = rep.per_contract_rows.get(b.contract, 0) + 1
    return store, rep
