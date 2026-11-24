"""Venue-published statistics -> rollover volume (N13b) and coarse evidence (N18b).

Source: Databento `statistics` schema (docs/VENDOR_VERIFICATION.md):
- stat_type 6 CLEARED_VOLUME: "The number of contracts cleared for an
  instrument on the previous trading date."
- stat_type 5 / 4 TRADING_SESSION_HIGH / LOW_PRICE: "The highest / lowest
  trade price of an instrument during the trading session."

Mapping rules. Each is causal and follows from the documented wording. Each
must be checked against real statistics data before use (see OPEN_ITEMS C3).
- Cleared volume is assigned to the most recent exchange session that had
  ENDED when the record was received (ts_recv). It becomes usable at the minute
  after ts_recv. If ts_ref is set and its UTC date differs from that session
  id, the record is NOT used and is reported.
- A session high/low record stamped at ts_event inside session S bounds all
  minutes of S before minute(ts_event), and printed no later than that minute.
  A record stamped after S ended (and before the next session) bounds all of S.
  It becomes usable at the minute after ts_recv.
- DELETE actions are not applied; they are reported. For cleared volume only
  the first-received figure is used; later differing figures are reported.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .calendars import ExchangeCalendar
from .models import CoarseBar
from .rollover import SessionVolume
from .ticks import fixed_point_to_decimal

CLEARED_VOLUME, SESSION_HIGH, SESSION_LOW = 6, 5, 4
NEW, DELETE = 1, 2
UNDEF_TS = 2**64 - 1
NS_MIN = 60 * 10**9


@dataclass
class StatsReport:
    used_volume: int = 0
    used_extremes: int = 0
    deletes_ignored: int = 0
    revised_volume: list = field(default_factory=list)
    ts_ref_mismatch: list = field(default_factory=list)
    unmapped: int = 0
    outside_calendar: int = 0


def _val(x):
    return int(getattr(x, "value", x))


def _get(r, k):
    return r[k] if isinstance(r, dict) else getattr(r, k)


def _last_ended_session(cal: ExchangeCalendar, minute: int):
    best = None
    for s in cal.sessions:
        if s.end <= minute:
            best = s
        else:
            break
    return best


def convert(records, symbol_of, exchange: ExchangeCalendar, instrument):
    """records: iterable of dicts or databento_dbn.StatMsg.
    symbol_of(instrument_id, utc_date) -> contract symbol or None."""
    rep = StatsReport()
    volumes: dict[tuple[str, str], SessionVolume] = {}
    coarse: list[CoarseBar] = []
    for r in sorted(records, key=lambda r: (_get(r, "ts_recv"), _get(r, "instrument_id"))):
        st = _val(_get(r, "stat_type"))
        if st not in (CLEARED_VOLUME, SESSION_HIGH, SESSION_LOW):
            continue
        if _val(_get(r, "update_action")) == DELETE:
            rep.deletes_ignored += 1
            continue
        recv_ns = _get(r, "ts_recv")
        avail = recv_ns // NS_MIN + 1
        d = datetime.fromtimestamp(recv_ns // 10**9, tz=timezone.utc).date()
        sym = symbol_of(_get(r, "instrument_id"), d)
        if sym is None:
            rep.unmapped += 1
            continue
        if st == CLEARED_VOLUME:
            s = _last_ended_session(exchange, recv_ns // NS_MIN)
            if s is None:
                rep.outside_calendar += 1
                continue
            ts_ref = _get(r, "ts_ref")
            if ts_ref not in (None, 0, UNDEF_TS):
                ref_date = datetime.fromtimestamp(ts_ref // 10**9, tz=timezone.utc).date().isoformat()
                if ref_date != s.session_id:
                    rep.ts_ref_mismatch.append({"symbol": sym, "session": s.session_id, "ts_ref_date": ref_date})
                    continue
            qty = int(_get(r, "quantity"))
            key = (sym, s.session_id)
            if key in volumes:
                if volumes[key].volume != qty:
                    rep.revised_volume.append({"symbol": sym, "session": s.session_id,
                                               "first": volumes[key].volume, "later": qty})
                continue
            volumes[key] = SessionVolume(sym, s.session_id, qty, avail, "databento-statistics-cleared-volume")
            rep.used_volume += 1
        else:
            ev_min = _get(r, "ts_event") // NS_MIN
            s = exchange.session_at(ev_min)
            if s is not None:
                end, attr = ev_min, ev_min + 1
            else:
                s = _last_ended_session(exchange, ev_min)
                if s is None:
                    rep.outside_calendar += 1
                    continue
                end = attr = s.end
            if end <= s.start:
                continue
            px = fixed_point_to_decimal(_get(r, "price"))
            instrument.contract(sym).grid.to_ticks(px)  # raises if off-grid: never rounded
            coarse.append(CoarseBar(sym, s.start, end, px if st == SESSION_HIGH else None,
                                    px if st == SESSION_LOW else None, max(avail, attr),
                                    f"databento-statistics-{st}", attribution_end_minute=attr))
            rep.used_extremes += 1
    return volumes, coarse, rep


def load_statistics_dbn(path):
    from .dbn import _read
    meta, recs = _read(path)
    if str(meta.schema) != "statistics":
        raise ValueError(f"{path}: schema {meta.schema} is not statistics")
    return meta, recs
