"""Sweep-reversal strategy backtest (docs/STRATEGY_SPEC.md v0.1).

Strategy layer only. It reads the liquidity engine's outputs (sweep events and
the lifecycle log) plus 1-minute bars, and never changes liquidity state
(spec A16).

All prices are integer ticks (0.25). Bars are labelled by open minute and
observable at minute + 1.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
TICK = Decimal("0.25")
USD_PER_POINT = Decimal("2")          # spec 6.1 (definitions: unit_of_measure_qty = 2, IPNT, USD)
USD_PER_TICK = USD_PER_POINT * TICK   # $0.50


def m_of(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) // 60


def to_ticks(price: str) -> int:
    v = Decimal(price) / TICK
    if v != v.to_integral_value():
        raise ValueError(f"off-grid price {price}")
    return int(v)


def local(m: int) -> datetime:
    return datetime.fromtimestamp(m * 60, tz=timezone.utc).astimezone(NY)


@dataclass(frozen=True)
class Bar:
    m: int
    o: int
    h: int
    l: int
    c: int


@dataclass
class Params:
    window_start: time = time(9, 30)
    window_end: time = time(11, 30)
    entry_slippage: int = 1          # ticks against the trade (3.2)
    stop_slippage: int = 1           # ticks against the trade (4.4)
    exit_slippage: int = 1           # ticks, time exit (5.4)
    target_through_ticks: int = 1    # 5.2: price must trade this far through the target (0 = touch)
    stop_multiple: int = 2           # 4.1
    # Questions not settled by the approved spec (reported, see S-items):
    trade_reference_closed_days: bool | None = None       # S2
    opposite_same_candle: str | None = None               # S1: "SKIP"


@dataclass
class Setup:
    confirmation_minute: int
    direction: str                  # BSL -> short, SSL -> long
    extreme: int                    # E, ticks
    contract: str
    event_ids: list
    levels: list
    labels: list
    k: int                          # q - t (confirmation candle index)
    penetration_minute: int


@dataclass
class Trade:
    setup: Setup
    side: str                       # LONG / SHORT
    entry_minute: int
    entry: int
    stop: int
    target: int
    dist_e: int                     # |fill - E| in ticks
    exit_minute: int | None = None
    exit: int | None = None
    reason: str | None = None
    ambiguous_bar: bool = False
    flags: list = field(default_factory=list)

    @property
    def risk(self) -> int:
        return abs(self.entry - self.stop)

    @property
    def pnl_ticks(self) -> int:
        return (self.exit - self.entry) if self.side == "LONG" else (self.entry - self.exit)

    @property
    def r(self) -> float:
        return self.pnl_ticks / self.risk

    def to_dict(self):
        iso = lambda m: None if m is None else local(m).strftime("%Y-%m-%d %H:%M ET")  # noqa: E731
        return {
            "side": self.side, "contract": self.setup.contract, "event_ids": self.setup.event_ids,
            "levels": [str(x * TICK) for x in self.setup.levels], "labels": self.setup.labels, "k": self.setup.k,
            "penetration": iso(self.setup.penetration_minute), "confirmation": iso(self.setup.confirmation_minute),
            "sweep_extreme": str(self.setup.extreme * TICK),
            "entry_time": iso(self.entry_minute), "entry": str(self.entry * TICK),
            "stop": str(self.stop * TICK), "target": str(self.target * TICK),
            "dist_e_ticks": self.dist_e, "risk_ticks": self.risk,
            "exit_time": iso(self.exit_minute), "exit": None if self.exit is None else str(self.exit * TICK),
            "reason": self.reason, "pnl_ticks": self.pnl_ticks, "r": round(self.r, 4),
            "pnl_usd_gross": str(self.pnl_ticks * USD_PER_TICK),
            "ambiguous_bar": self.ambiguous_bar, "flags": self.flags,
        }


class Market:
    """Bars per contract + calendar segments + RTH close per trade date."""

    def __init__(self, bars: dict[str, list[Bar]], segments: list[tuple[int, int, str]],
                 rth_close: dict[str, int | None]):
        self.bars = {c: sorted(v, key=lambda b: b.m) for c, v in bars.items()}
        self.ms = {c: [b.m for b in v] for c, v in self.bars.items()}
        self.segments = sorted(segments)
        self.seg_starts = [s[0] for s in self.segments]
        self.rth_close = rth_close

    def segment(self, m):
        i = bisect_right(self.seg_starts, m) - 1
        if i >= 0 and self.segments[i][0] <= m < self.segments[i][1]:
            return self.segments[i]
        return None

    def bars_from(self, contract, m):
        i = bisect_right(self.ms[contract], m - 1)
        return self.bars[contract][i:]


def window_bounds(m: int, p: Params):
    d = local(m).date()
    a = int(datetime.combine(d, p.window_start, NY).timestamp()) // 60
    b = int(datetime.combine(d, p.window_end, NY).timestamp()) // 60
    return a, b


def eligible(e: dict, mkt: Market, p: Params) -> str | None:
    """Trading-window eligibility (A6/A7, mirrors liqengine.eligibility). Returns a reject reason or None."""
    if e["status"] != "CONFIRMED":
        return "NOT_CONFIRMED"
    if e["dual_sided"]:
        return "DUAL_SIDED"
    if e["reference_quarantined_at_penetration"]:
        return "REFERENCE_QUARANTINED"
    t, q = m_of(e["penetration_ts"]), m_of(e["confirmation_ts"])
    w0, w1 = window_bounds(t, p)
    if not (w0 <= t < w1):
        return "PENETRATION_OUTSIDE_WINDOW"
    if not q + 1 < w1:
        return "CONFIRMATION_OBSERVABLE_AT_OR_AFTER_WINDOW_END"
    if mkt.segment(q + 1) is None:
        return "EXCHANGE_CLOSED_AT_CONFIRMATION"
    return None


def build_setups(events: list[dict], mkt: Market, p: Params, log: list):
    """Spec 1.1, 1.6: eligible confirmed sweeps; same candle + same side -> one setup."""
    groups: dict[tuple, list] = {}
    for e in events:
        if e.get("penetration_ts") is None:
            continue
        why = eligible(e, mkt, p)
        if why:
            continue
        key = (m_of(e["confirmation_ts"]), e["direction"])
        groups.setdefault(key, []).append(e)
    setups = []
    for (q, d), es in sorted(groups.items()):
        ex = {to_ticks(e["sweep_extreme"]) for e in es}
        pens = {m_of(e["penetration_ts"]) for e in es}
        if len(ex) != 1:
            # same confirmation candle but different penetration candles can give different E
            log.append({"confirmation": q, "direction": d, "issue": "TIE_WITH_DIFFERENT_EXTREMES",
                        "extremes": sorted(ex)})
        E = max(ex) if d == "BSL" else min(ex)  # recorded; only reached if the issue above occurs
        setups.append(Setup(q, d, E, es[0]["contract"], [e["event_id"] for e in es],
                            sorted(to_ticks(e["level_price"]) for e in es),
                            sorted({lab for e in es for lab in e["source_labels"]}),
                            q - min(pens), min(pens)))
    return setups


def simulate(setups: list[Setup], mkt: Market, p: Params):
    trades, skipped = [], []
    by_q: dict[int, list[Setup]] = {}
    for s in setups:
        by_q.setdefault(s.confirmation_minute, []).append(s)
    flat_after = -1   # last candle (open minute) on which the previous position was closed
    for q in sorted(by_q):
        group = by_q[q]
        if len({s.direction for s in group}) > 1:
            if p.opposite_same_candle is None:
                raise RuntimeError("S1 not authorized: opposite-direction setups confirmed on the same candle")
            skipped += [(s, "OPPOSITE_SETUPS_SAME_CANDLE") for s in group]
            continue
        s = group[0]
        # 1.7: ignore setups confirmed while a position is open (closed on candle X -> flat from X's close)
        if q < flat_after:
            skipped.append((s, "IN_POSITION"))
            continue
        day = local(q).date()
        sess = mkt.segment(q + 1)
        sid = sess[2]
        if mkt.rth_close.get(sid) is None:
            if p.trade_reference_closed_days is None:
                raise RuntimeError(f"S2 not authorized: setup on reference-closed trade date {sid}")
            if not p.trade_reference_closed_days:
                skipped.append((s, "REFERENCE_MARKET_CLOSED"))
                continue
        _, w1 = window_bounds(q, p)
        nxt = mkt.bars_from(s.contract, q + 1)
        if not nxt or nxt[0].m >= w1 or mkt.segment(nxt[0].m) != sess:
            skipped.append((s, "NO_ENTRY_BAR_BEFORE_WINDOW_END"))
            continue
        eb = nxt[0]
        # 1.4: a newer setup confirmed before this one could be entered replaces it
        if any(q < q2 < eb.m for q2 in by_q):
            skipped.append((s, "REPLACED_BEFORE_ENTRY"))
            continue
        long = s.direction == "SSL"
        fill = eb.o + (p.entry_slippage if long else -p.entry_slippage)
        d = (fill - s.extreme) if long else (s.extreme - fill)
        if d <= 0:
            skipped.append((s, "FILL_AT_OR_BEYOND_EXTREME"))
            continue
        stop = fill - p.stop_multiple * d if long else fill + p.stop_multiple * d
        target = fill + d if long else fill - d     # 0.5R with R = 2d
        tr = Trade(s, "LONG" if long else "SHORT", eb.m, fill, stop, target, d)
        if eb.m != q + 1:
            tr.flags.append("ENTRY_BAR_NOT_IMMEDIATELY_NEXT_MINUTE")
        rth_close = mkt.rth_close.get(sid)
        prev = eb
        for b in nxt:
            if mkt.segment(b.m) != sess:
                tr.exit_minute, tr.exit, tr.reason = prev.m, prev.c, "SEGMENT_END"
                break
            if rth_close is not None and b.m >= rth_close:
                tr.exit_minute = b.m
                tr.exit = b.o - p.exit_slippage if long else b.o + p.exit_slippage
                tr.reason = "TIME_EXIT_RTH_CLOSE"
                break
            first = b is eb
            if long:
                gap_stop = (not first) and b.o <= stop
                hit_stop = b.l <= stop
                hit_tgt = b.h >= target + p.target_through_ticks
            else:
                gap_stop = (not first) and b.o >= stop
                hit_stop = b.h >= stop
                hit_tgt = b.l <= target - p.target_through_ticks
            if gap_stop:
                tr.exit_minute, tr.reason = b.m, "STOP_GAP"
                tr.exit = b.o - p.stop_slippage if long else b.o + p.stop_slippage
                break
            if hit_stop:
                tr.exit_minute, tr.reason = b.m, "STOP"
                tr.exit = stop - p.stop_slippage if long else stop + p.stop_slippage
                tr.ambiguous_bar = hit_tgt
                break
            if hit_tgt:
                tr.exit_minute, tr.exit, tr.reason = b.m, target, "TARGET"
                break
            prev = b
        else:
            tr.reason = "DATA_END"
        if tr.exit is None:
            skipped.append((s, "NO_EXIT"))
            continue
        trades.append(tr)
        flat_after = tr.exit_minute
    return trades, skipped
