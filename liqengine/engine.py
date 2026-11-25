"""Mathematical liquidity engine: identification and lifecycle tracking.

Processing contract (spec v1.1, item A1 / N1 ordering):
For each scheduled minute m of the active contract, in strictly increasing
order, `process_minute` runs these steps. A bar for minute m covers [m, m+1)
and is observable at m+1.
  1. Invalidation counters of sweeps confirmed BEFORE m consume bar m's close.
  2. Pending sweeps (penetrated before m) test bar m's close for confirmation.
  3. Penetration: bar m is tested only against references whose availability
     minute is <= m. Penetrated references are consumed, the dual-sided check
     runs, new sweep events are created and test k = 0 on bar m's close.
  4. Pending sweeps whose deadline t+3 == m and that remain unconfirmed are
     rejected.
  5. Source windows closing at m+1 (15m candle, pre-window, RTH, exchange
     session/segment) are finalized, and their new references are registered
     with availability m+1.
  6. If m+1 ends a scheduled trading segment, closure rules apply (A5, N4,
     A11.1).
The strategy layer (eligibility.py) is never consulted.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right, insort
from dataclasses import dataclass, field
from decimal import Decimal

from .calendars import SessionPlan
from .data import Bar, MinuteStatus
from .instrument import InstrumentConfig
from .models import (
    CoarseBar, ConsumptionKind, Direction, ExpiryReason, InvalidationReason, LiquidityReference,
    QuarantineReason, RejectReason, SourceLabel, SourceRecord, SweepEvent, SweepStatus,
)
from .policies import (
    CounterAcrossClosure, CounterAcrossZeroTrade, ExchangeSessionUnit, IncompleteWindowLiquidity,
    PolicyConfig, QuarantineCollision,
)
from .ticks import OffGridError, TickGrid

CONFIRMATION_WINDOW = 3   # k in {0,1,2,3} (spec §6.3) - not a tunable parameter


class EngineStateError(RuntimeError):
    pass


@dataclass
class _WindowAgg:
    """Running extreme over a source window (session / RTH / pre-window)."""
    label: SourceLabel
    origin_id: str
    start: int
    end: int
    high: int | None = None
    high_minute: int | None = None
    low: int | None = None
    low_minute: int | None = None
    observed: int = 0
    missing_minutes: set[int] = field(default_factory=set)

    def add_bar(self, b: Bar):
        self.observed += 1
        if self.high is None or b.high > self.high:
            self.high, self.high_minute = b.high, b.minute
        if self.low is None or b.low < self.low:
            self.low, self.low_minute = b.low, b.minute


@dataclass
class _Candle15:
    bucket: int
    segment_key: tuple
    processed: int = 0
    observed: int = 0
    invalid: bool = False
    high: int | None = None
    high_minute: int | None = None
    low: int | None = None
    low_minute: int | None = None

    def add_bar(self, b: Bar):
        self.observed += 1
        if self.high is None or b.high > self.high:
            self.high, self.high_minute = b.high, b.minute
        if self.low is None or b.low < self.low:
            self.low, self.low_minute = b.low, b.minute


class LiquidityEngine:
    def __init__(self, instrument: InstrumentConfig, policies: PolicyConfig, contract: str, grid: TickGrid):
        self.instrument = instrument
        self.policies = policies
        self.contract = contract
        self.grid = grid
        self.refs: dict[str, LiquidityReference] = {}
        self.events: dict[str, SweepEvent] = {}
        self._index: dict[Direction, dict[int, list[str]]] = {Direction.BSL: {}, Direction.SSL: {}}
        self._keys: dict[Direction, list[int]] = {Direction.BSL: [], Direction.SSL: []}
        self._pending: list[str] = []
        self._confirmed: list[str] = []
        self._ref_seq = 0
        self._evt_seq = 0
        self._gap_seq = 0
        self._open_gap: str | None = None
        self._last_minute: int | None = None
        self._plan: SessionPlan | None = None
        self._aggs: list[_WindowAgg] = []
        self._candle: _Candle15 | None = None
        self._fractal: list[_Candle15] = []
        self._observed_bars: dict[str, dict[int, Bar]] = {}
        self._missing_log: dict[str, set[int]] = {}
        self.suspended = False
        self._in_halt = False
        self._q_ids: set[str] = set()
        self.log: list[dict] = []
        self.data_quality: list[dict] = []

    # ------------------------------------------------------------------ utils
    def _log(self, minute: int, event: str, **kw):
        self.log.append({"minute": minute, "event": event, **kw})

    def _new_ref_id(self) -> str:
        self._ref_seq += 1
        return f"L{self._ref_seq:07d}"

    def _new_evt_id(self) -> str:
        self._evt_seq += 1
        return f"S{self._evt_seq:07d}"

    def _index_add(self, ref: LiquidityReference):
        bucket = self._index[ref.direction].setdefault(ref.ticks, [])
        if not bucket:
            insort(self._keys[ref.direction], ref.ticks)
        bucket.append(ref.ref_id)

    def _index_remove(self, ref: LiquidityReference):
        bucket = self._index[ref.direction].get(ref.ticks, [])
        if ref.ref_id in bucket:
            bucket.remove(ref.ref_id)
            if not bucket:
                del self._index[ref.direction][ref.ticks]
                keys = self._keys[ref.direction]
                keys.pop(bisect_left(keys, ref.ticks))

    def _testable_refs(self):
        for d in (Direction.BSL, Direction.SSL):
            for t in list(self._keys[d]):
                for rid in list(self._index[d].get(t, [])):
                    yield self.refs[rid]

    # ------------------------------------------------------------ sessions
    def begin_session(self, plan: SessionPlan):
        if self.suspended:
            raise EngineStateError("engine suspended")
        if self._plan is not None:
            raise EngineStateError("previous session not finished")
        s = plan.session
        self._plan = plan
        self._aggs = []
        unit = None
        if len(s.segments) > 1:
            unit = self.policies.require(
                "exchange_session_unit", f"session {s.session_id} has {len(s.segments)} scheduled segments"
            )
        if unit is ExchangeSessionUnit.SEGMENT:
            for i, (a, b) in enumerate(s.segments):
                self._aggs.append(_WindowAgg(SourceLabel.EXCHANGE_SESSION, f"{s.session_id}#seg{i}", a, b))
        else:
            self._aggs.append(_WindowAgg(SourceLabel.EXCHANGE_SESSION, s.session_id, s.start, s.end))
        if plan.pre_window is not None and plan.pre_window[0] < plan.pre_window[1]:
            self._aggs.append(_WindowAgg(SourceLabel.PRE_WINDOW, s.session_id, *plan.pre_window))
        if plan.rth is not None and plan.rth[0] < plan.rth[1]:
            self._aggs.append(_WindowAgg(SourceLabel.RTH, s.session_id, *plan.rth))
        self._log(s.start, "SESSION_BEGIN", session_id=s.session_id, flags=list(plan.flags))

    def _end_session(self):
        self._log(self._plan.session.end, "SESSION_END", session_id=self._plan.session.session_id)
        self._plan = None
        self._aggs = []

    # -------------------------------------------------------------- minute
    def process_minute(self, m: int, status: MinuteStatus, bar: Bar | None):
        if self.suspended:
            raise EngineStateError("engine suspended")
        plan = self._plan
        if plan is None or not plan.session.is_scheduled(m):
            raise EngineStateError(f"minute {m} is not a scheduled minute of the current session")
        if self._last_minute is not None and m <= self._last_minute:
            raise EngineStateError("minutes must be strictly increasing")
        if (status is MinuteStatus.OBSERVED) != (bar is not None):
            raise EngineStateError("bar must be given exactly when status is OBSERVED")
        if bar is not None and (bar.minute != m or bar.contract != self.contract):
            raise EngineStateError("bar does not match minute/contract")
        self._last_minute = m
        seg_idx = plan.session.segment_of(m)

        if status is MinuteStatus.OBSERVED:
            self._open_gap = None
            self._observed_bars.setdefault(self.contract, {})[m] = bar
            self._step_invalidation_bar(m, bar)
            self._step_pending_bar(m, bar)
            self._step_penetration(m, bar)
        elif status is MinuteStatus.VERIFIED_ZERO_TRADE:
            self._open_gap = None
            self._step_zero_trade(m)
        elif status is MinuteStatus.VERIFIED_HALT:
            if not self._in_halt:
                self._closure(m, RejectReason.UNSCHEDULED_HALT, "UNSCHEDULED_HALT_START")
        else:
            self._step_missing(m, status)
        self._in_halt = status is MinuteStatus.VERIFIED_HALT
        self._step_deadline(m)

        # source windows
        for agg in self._aggs:
            if agg.start <= m < agg.end:
                if bar is not None:
                    agg.add_bar(bar)
                elif status.is_missing_data:
                    agg.missing_minutes.add(m)
        self._step_candle(m, status, bar, (plan.session.session_id, seg_idx))

        t1 = m + 1
        if t1 % 15 == 0:
            self._close_candle(t1)
        for agg in [a for a in self._aggs if a.end == t1]:
            self._finalize_window(agg, t1)
        seg_end = plan.session.segments[seg_idx][1]
        if t1 == seg_end:
            self._closure(t1)
            if seg_idx == len(plan.session.segments) - 1:
                self._end_session()

    # -------------------------------------------------------- lifecycle steps
    def _step_invalidation_bar(self, m: int, bar: Bar):
        for eid in list(self._confirmed):
            e = self.events[eid]
            if e.confirmation_minute >= m:
                continue
            beyond = bar.close > e.extreme_ticks if e.direction is Direction.BSL else bar.close < e.extreme_ticks
            e.invalidation_count = e.invalidation_count + 1 if beyond else 0
            if e.invalidation_count == 3:
                e.invalidated = True
                e.invalidation_reason = InvalidationReason.THREE_CLOSES_BEYOND_EXTREME
                e.invalidation_minute = m
                self._confirmed.remove(eid)
                self._log(m, "SWEEP_INVALIDATED", event_id=eid, reason=e.invalidation_reason.value)

    def _confirms(self, e: SweepEvent, close: int) -> bool:
        return close < e.level_ticks if e.direction is Direction.BSL else close > e.level_ticks

    def _extend_extreme(self, e: SweepEvent, bar: Bar):
        if e.direction is Direction.BSL:
            e.running_extreme = bar.high if e.running_extreme is None else max(e.running_extreme, bar.high)
        else:
            e.running_extreme = bar.low if e.running_extreme is None else min(e.running_extreme, bar.low)

    def _confirm(self, e: SweepEvent, m: int):
        e.status = SweepStatus.CONFIRMED
        e.confirmation_minute = m
        e.extreme_ticks = e.running_extreme
        e.extreme_price = self.grid.to_price(e.extreme_ticks)
        self._confirmed.append(e.event_id)
        self._log(m, "SWEEP_CONFIRMED", event_id=e.event_id, extreme=str(e.extreme_price))

    def _reject(self, e: SweepEvent, m: int, reason: RejectReason):
        e.status = SweepStatus.REJECTED
        e.reject_reason = reason
        e.rejected_minute = m
        self._log(m, "SWEEP_REJECTED", event_id=e.event_id, reason=reason.value)

    def _step_pending_bar(self, m: int, bar: Bar):
        for eid in list(self._pending):
            e = self.events[eid]
            self._extend_extreme(e, bar)
            if self._confirms(e, bar.close):
                self._pending.remove(eid)
                self._confirm(e, m)

    def _step_penetration(self, m: int, bar: Bar):
        hi_keys = self._keys[Direction.BSL][: bisect_right(self._keys[Direction.BSL], bar.high - 1)]
        lo_keys = self._keys[Direction.SSL][bisect_left(self._keys[Direction.SSL], bar.low + 1):]
        bsl = [self.refs[r] for k in hi_keys for r in self._index[Direction.BSL][k]]
        ssl = [self.refs[r] for k in lo_keys for r in self._index[Direction.SSL][k]]
        for r in bsl + ssl:
            assert min(s.availability_minute for s in r.sources) <= m, "availability ordering violated"
        if not bsl and not ssl:
            return
        dual = False
        if bsl and ssl:
            bn = any(not r.quarantined for r in bsl)
            sn = any(not r.quarantined for r in ssl)
            if bn and sn:
                dual = True
            else:
                inc = self.policies.require(
                    "dual_sided_includes_quarantined",
                    f"minute {m}: bar penetrates both sides and a side contains only quarantined levels",
                )
                dual = True if inc else (bn and sn)
        if dual:
            self.data_quality.append({"minute": m, "kind": "DUAL_SIDED_SWEEP",
                                      "refs": sorted(r.ref_id for r in bsl + ssl)})
        for r in sorted(bsl + ssl, key=lambda r: r.ref_id):
            self._consume(r, m)
            e = SweepEvent(
                event_id=self._new_evt_id(), ref_id=r.ref_id, direction=r.direction,
                instrument=self.instrument.root, contract=self.contract, level_price=r.price,
                level_ticks=r.ticks, source_labels=tuple(sorted({s.label.value for s in r.sources})),
                penetration_minute=m, deadline_minute=m + CONFIRMATION_WINDOW,
                reference_quarantined_at_penetration=r.quarantined, dual_sided=dual,
            )
            if r.quarantined:
                e.dq_flags.append("REFERENCE_QUARANTINED")
            r.consuming_event_id = e.event_id
            self.events[e.event_id] = e
            self._log(m, "SWEEP_PENETRATION", event_id=e.event_id, ref_id=r.ref_id)
            if dual:
                self._reject(e, m, RejectReason.DUAL_SIDED)
                continue
            self._extend_extreme(e, bar)
            if self._confirms(e, bar.close):
                self._confirm(e, m)
            else:
                self._pending.append(e.event_id)

    def _consume(self, r: LiquidityReference, m: int, kind=ConsumptionKind.OBSERVED_PENETRATION):
        r.consumed = True
        r.consumed_minute = m
        r.consumption_kind = kind
        self._index_remove(r)
        self._log(m, "LIQUIDITY_CONSUMED", ref_id=r.ref_id, kind=kind.value)

    def _step_zero_trade(self, m: int):
        tracking = [self.events[e] for e in self._confirmed if self.events[e].confirmation_minute < m]
        if tracking:
            pol = self.policies.require(
                "counter_across_zero_trade", f"minute {m}: verified zero-trade minute while sweeps are tracked"
            )
            if pol is CounterAcrossZeroTrade.RESET:
                for e in tracking:
                    e.invalidation_count = 0

    def _step_missing(self, m: int, status: MinuteStatus):
        if self._open_gap is None:
            self._gap_seq += 1
            self._open_gap = f"G{self._gap_seq:06d}"
            self.data_quality.append({"minute": m, "kind": "MISSING_DATA_START", "gap_id": self._open_gap,
                                      "status": status.value, "contract": self.contract})
        gid = self._open_gap
        self._missing_log.setdefault(self.contract, set()).add(m)
        for eid in list(self._pending):
            e = self.events[eid]
            self._pending.remove(eid)
            self._reject(e, m, RejectReason.DATA_GAP)
            e.dq_flags.append(f"DATA_GAP:{gid}")
        for eid in list(self._confirmed):
            e = self.events[eid]
            e.invalidated = True
            e.invalidation_reason = InvalidationReason.DATA_GAP
            e.invalidation_minute = m
            e.dq_flags.append(f"DATA_GAP:{gid}")
            self._confirmed.remove(eid)
            self._log(m, "SWEEP_INVALIDATED", event_id=eid, reason="DATA_GAP")
        for r in self._testable_refs():
            q = r.quarantine_reasons.get(gid)
            if q is None:
                q = QuarantineReason(gid, "DATA_GAP", m, self.contract, set())
                r.quarantine_reasons[gid] = q
                self._q_ids.add(r.ref_id)
                if not r.quarantined:
                    r.quarantined = True
                    self._log(m, "LIQUIDITY_QUARANTINED", ref_id=r.ref_id, reason=gid)
            q.unresolved_minutes.add(m)
            q.all_minutes.add(m)

    def _step_deadline(self, m: int):
        for eid in list(self._pending):
            e = self.events[eid]
            if e.deadline_minute <= m:
                self._pending.remove(eid)
                self._reject(e, m, RejectReason.NO_CONFIRMATION_WITHIN_WINDOW)

    # ----------------------------------------------------------- 15m candles
    def _step_candle(self, m: int, status: MinuteStatus, bar: Bar | None, seg_key):
        b = m // 15
        if self._candle is None or self._candle.bucket != b:
            if self._candle is not None:
                # previous candle never reached its close (shortened); invalid
                self._fractal.clear()
            self._candle = _Candle15(b, seg_key)
        c = self._candle
        if c.segment_key != seg_key:
            c.invalid = True
        c.processed += 1
        if bar is not None:
            c.add_bar(bar)
        elif status.is_missing_data or status is MinuteStatus.VERIFIED_HALT:
            c.invalid = True

    def _close_candle(self, t1: int):
        c = self._candle
        self._candle = None
        if c is None or c.bucket != t1 // 15 - 1:
            self._fractal.clear()
            return
        valid = not c.invalid and c.processed == 15 and c.observed > 0
        if not valid:
            self._fractal.clear()
            self._log(t1, "CANDLE15_EXCLUDED", bucket_start=c.bucket * 15,
                      reason="NO_TRADES" if c.observed == 0 and not c.invalid and c.processed == 15 else "INCOMPLETE")
            return
        if self._fractal and self._fractal[-1].bucket != c.bucket - 1:
            self._fractal.clear()
        self._fractal.append(c)
        if len(self._fractal) > 3:
            self._fractal.pop(0)
        if len(self._fractal) == 3:
            a, mid, z = self._fractal
            origin = f"15m@{mid.bucket * 15}"
            if mid.high > a.high and mid.high > z.high:
                self._register(Direction.BSL, mid.high, SourceLabel.SWING_15M, origin, mid.high_minute, t1)
            if mid.low < a.low and mid.low < z.low:
                self._register(Direction.SSL, mid.low, SourceLabel.SWING_15M, origin, mid.low_minute, t1)

    # ---------------------------------------------------------- source windows
    def _finalize_window(self, agg: _WindowAgg, t1: int):
        if agg.observed == 0:
            self._log(t1, "WINDOW_NO_TRADES", label=agg.label.value, origin=agg.origin_id,
                      missing=len(agg.missing_minutes))
            return
        quarantine = None
        if agg.missing_minutes:
            pol = self.policies.require(
                "incomplete_window_liquidity",
                f"{agg.label.value} {agg.origin_id} contains {len(agg.missing_minutes)} missing/uncertain minutes",
            )
            if pol is IncompleteWindowLiquidity.DO_NOT_REGISTER:
                self._log(t1, "WINDOW_NOT_REGISTERED", label=agg.label.value, origin=agg.origin_id)
                return
            quarantine = set(agg.missing_minutes)
        self._register(Direction.BSL, agg.high, agg.label, agg.origin_id, agg.high_minute, t1, quarantine)
        self._register(Direction.SSL, agg.low, agg.label, agg.origin_id, agg.low_minute, t1, quarantine)

    def _register(self, direction: Direction, ticks: int, label: SourceLabel, origin: str,
                  formation: int, availability: int, incomplete_minutes: set[int] | None = None):
        price = self.grid.to_price(ticks)
        src = SourceRecord(label, origin, formation, availability, self.contract, price)
        new_q = None
        if incomplete_minutes is not None:
            src.flags.append("INCOMPLETE_SOURCE_WINDOW")
            rid = f"W:{label.value}:{origin}"
            new_q = QuarantineReason(rid, "INCOMPLETE_SOURCE_WINDOW", availability, self.contract,
                                     set(incomplete_minutes), all_minutes=set(incomplete_minutes))
        existing = [self.refs[r] for r in self._index[direction].get(ticks, [])]
        target = None
        if existing:
            verified_existing = [r for r in existing if not r.quarantined]
            if new_q is None and verified_existing and len(verified_existing) == len(existing):
                target = verified_existing[0]
            else:
                pol = self.policies.require(
                    "quarantine_collision",
                    f"new {label.value} {direction.value} level {price} collides with a level where a quarantine is involved",
                )
                if pol is QuarantineCollision.SEPARATE:
                    if new_q is None and verified_existing:
                        target = verified_existing[0]   # no quarantine on either side of this pair
                else:
                    target = existing[0]
                    if pol is QuarantineCollision.MERGE_LIFT_QUARANTINE and (new_q is None or not target.quarantined):
                        new_q = None
                        src.flags.append("MERGED_WITH_LIFT")
                        if target.quarantined:
                            for q in target.quarantine_reasons.values():
                                if not q.resolved:
                                    q.resolved_minute = availability
                            target.quarantined = False
                            target.reinstatements.append(availability)
                            target.dq_flags.append("QUARANTINE_LIFTED_BY_COLLISION")
                            self._log(availability, "LIQUIDITY_REINSTATED", ref_id=target.ref_id, by="COLLISION")
        if target is not None:
            target.sources.append(src)
            if new_q is not None:
                target.quarantine_reasons[new_q.reason_id] = new_q
                target.quarantined = True
                self._q_ids.add(target.ref_id)
            self._log(availability, "LIQUIDITY_CONSOLIDATED", ref_id=target.ref_id, label=label.value)
            return target
        r = LiquidityReference(
            ref_id=self._new_ref_id(), direction=direction, instrument=self.instrument.root,
            contract=self.contract, price=price, ticks=ticks, sources=[src],
            price_history=[(availability, self.contract, price)],
        )
        if new_q is not None:
            r.quarantine_reasons[new_q.reason_id] = new_q
            r.quarantined = True
            self._q_ids.add(r.ref_id)
        self.refs[r.ref_id] = r
        self._index_add(r)
        self._log(availability, "LIQUIDITY_REGISTERED", ref_id=r.ref_id, label=label.value,
                  direction=direction.value, price=str(price), quarantined=r.quarantined)
        return r

    # ------------------------------------------------------------- closures
    def _closure(self, t1: int, reason: RejectReason = RejectReason.SCHEDULED_CLOSURE,
                 label: str = "SCHEDULED_CLOSURE"):
        """Closure rules at boundary t1: scheduled closure (A5), or a notice-
        verified unscheduled halt starting at minute t1 (N22(b))."""
        for eid in list(self._pending):
            e = self.events[eid]
            self._pending.remove(eid)
            self._reject(e, t1 - 1, reason)
        if self._confirmed:
            pol = self.policies.require(
                "counter_across_closure", f"scheduled closure at minute {t1} while sweeps are tracked"
            )
            if pol is CounterAcrossClosure.RESET:
                for eid in self._confirmed:
                    self.events[eid].invalidation_count = 0
        self._fractal.clear()
        if self._candle is not None and t1 % 15 != 0:
            self._candle = None  # shortened candle discarded
        self._open_gap = None
        if reason is RejectReason.UNSCHEDULED_HALT:
            self.data_quality.append({"minute": t1, "kind": "UNSCHEDULED_HALT_NOTICE_VERIFIED"})
        self._log(t1, label)

    # ------------------------------------------------------------- rollover
    def rollover(self, minute: int, new_contract: str, new_grid: TickGrid, delta: Decimal | None, info: dict):
        if self._plan is not None or self._pending:
            raise EngineStateError("rollover only between sessions with no pending confirmations")
        for eid in list(self._confirmed):
            e = self.events[eid]
            e.expired = True
            e.expiry_reason = ExpiryReason.ROLLOVER
            e.expiry_minute = minute
            self._confirmed.remove(eid)
        old = self.contract
        for r in list(self._testable_refs()):
            self._index_remove(r)
            if delta is None:
                self._quarantine_unresolvable(r, minute, "ROLLOVER_REFERENCE_UNAVAILABLE", f"R:{old}->{new_contract}")
                r.coordinates_valid = False
                continue
            new_price = r.price + delta
            try:
                ticks = new_grid.to_ticks(new_price)
            except OffGridError:
                self._quarantine_unresolvable(r, minute, "INCOMPATIBLE_GRID", f"R:{old}->{new_contract}")
                r.coordinates_valid = False
                r.dq_flags.append(f"OFF_GRID_TRANSLATION:{new_price}")
                continue
            r.price, r.ticks, r.contract = new_price, ticks, new_contract
            r.price_history.append((minute, new_contract, new_price))
            self._index_add(r)
        self.contract, self.grid = new_contract, new_grid
        self._fractal.clear()
        self._candle = None
        self._log(minute, "ROLLOVER", old=old, new=new_contract,
                  delta=None if delta is None else str(delta), **info)

    def _quarantine_unresolvable(self, r: LiquidityReference, minute: int, kind: str, rid: str):
        r.quarantine_reasons[rid] = QuarantineReason(rid, kind, minute, None, None)
        self._q_ids.add(r.ref_id)
        if not r.quarantined:
            r.quarantined = True
        self._log(minute, "LIQUIDITY_QUARANTINED", ref_id=r.ref_id, reason=kind)

    def suspend(self, minute: int, reason: str):
        for eid in list(self._confirmed):
            e = self.events[eid]
            e.expired, e.expiry_reason, e.expiry_minute = True, ExpiryReason.PROCESSING_SUSPENDED, minute
            self._confirmed.remove(eid)
        for eid in list(self._pending):
            self._pending.remove(eid)
            self._reject(self.events[eid], minute, RejectReason.PROCESSING_SUSPENDED)
        self.suspended = True
        self.data_quality.append({"minute": minute, "kind": "PROCESSING_SUSPENDED", "reason": reason})
        self._log(minute, "PROCESSING_SUSPENDED", reason=reason)

    def resume(self, minute: int, new_contract: str, new_grid: TickGrid, info: dict):
        """N9(b): resume after an I2.4 suspension on a newly selected contract.
        No price adjustment can be measured, so every unconsumed level is
        quarantined and frozen (same treatment as I1 / N17)."""
        if not self.suspended:
            raise EngineStateError("resume() requires a suspended engine")
        old = self.contract
        for r in list(self._testable_refs()):
            self._index_remove(r)
            self._quarantine_unresolvable(r, minute, "SUSPENSION_NO_ADJUSTMENT", f"RESUME:{old}->{new_contract}")
            r.coordinates_valid = False
        self.contract, self.grid = new_contract, new_grid
        self._fractal.clear()
        self._candle = None
        self.suspended = False
        self.data_quality.append({"minute": minute, "kind": "PROCESSING_RESUMED", "contract": new_contract, **info})
        self._log(minute, "PROCESSING_RESUMED", old=old, new=new_contract, **info)

    # ------------------------------------------------------ coarse evidence
    def apply_coarse_evidence(self, ev: CoarseBar):
        """A4 step 2-5 / I6. Called by the driver before processing minute
        ev.available_minute (never earlier)."""
        if self._last_minute is not None and ev.available_minute <= self._last_minute:
            raise EngineStateError("coarse evidence applied late; driver must apply it in time order")
        if not any(q.unresolved_minutes and not q.resolved and q.contract == ev.contract
                   for r in self._quarantined_refs() for q in r.quarantine_reasons.values()):
            return  # nothing this evidence could resolve
        bars = self._observed_bars.get(ev.contract, {})
        missing_log = self._missing_log.get(ev.contract, set())
        window = set(range(ev.start_minute, ev.end_minute))                 # minutes the extreme bounds
        attr = set(range(ev.start_minute, ev.attribution_end))             # minutes it printed within
        obs_a = [bars[mm] for mm in range(ev.start_minute, ev.attribution_end) if mm in bars]
        obs_b = [b for b in obs_a if b.minute < ev.end_minute]
        missing_in_window = attr & missing_log
        grid = TickGrid(self.instrument.contract(ev.contract).tick_size) if self.instrument.has_expirations else self.grid
        hi = None if ev.high is None else grid.to_ticks(ev.high)
        lo = None if ev.low is None else grid.to_ticks(ev.low)
        bound_hi = max((b.high for b in obs_b), default=None)
        bound_lo = min((b.low for b in obs_b), default=None)
        if (hi is not None and bound_hi is not None and hi < bound_hi) or \
                (lo is not None and bound_lo is not None and lo > bound_lo):
            self.data_quality.append({"minute": ev.available_minute, "kind": "COARSE_EVIDENCE_INCONSISTENT",
                                      "source": ev.source, "contract": ev.contract})
            return
        obs_hi = max((b.high for b in obs_a), default=None)
        obs_lo = min((b.low for b in obs_a), default=None)
        hi_from_gap = hi is not None and bool(missing_in_window) and (obs_hi is None or hi > obs_hi)
        lo_from_gap = lo is not None and bool(missing_in_window) and (obs_lo is None or lo < obs_lo)
        A = ev.available_minute
        for r in self._quarantined_refs():
            p = r.price_in(ev.contract)
            if p is None:
                continue
            P = grid.to_ticks(p)
            for q in r.quarantine_reasons.values():
                if q.resolved or q.unresolved_minutes is None or q.contract != ev.contract:
                    continue
                covered = q.unresolved_minutes & window
                if not covered:
                    continue
                # The coarse extreme is attributable to this reason's minutes only if
                # every missing minute in the coarse window belongs to this reason.
                attributable = missing_in_window <= q.all_minutes
                if (hi if r.direction is Direction.BSL else lo) is None:
                    continue  # this evidence says nothing about this side
                if q.kind == "DATA_GAP":
                    if r.direction is Direction.BSL:
                        untouched, proven = hi <= P, hi_from_gap and attributable and hi >= P + 1
                    else:
                        untouched, proven = lo >= P, lo_from_gap and attributable and lo <= P - 1
                    if untouched:
                        q.unresolved_minutes -= covered
                    elif proven:
                        self._consume_from_evidence(r, q, A, ev)
                elif q.kind == "INCOMPLETE_SOURCE_WINDOW":
                    if r.direction is Direction.BSL:
                        ok, disproven = hi <= P, hi_from_gap and attributable and hi > P
                    else:
                        ok, disproven = lo >= P, lo_from_gap and attributable and lo < P
                    if ok:
                        q.unresolved_minutes -= covered
                    elif disproven and not q.disproven:
                        q.disproven = True
                        r.dq_flags.append(f"SOURCE_EXTREME_DISPROVEN:{q.reason_id}")
                        self._log(A, "SOURCE_EXTREME_DISPROVEN", ref_id=r.ref_id, reason=q.reason_id)
                if not q.unresolved_minutes and not q.disproven:
                    q.resolved_minute = A
                    self._log(A, "QUARANTINE_REASON_RESOLVED", ref_id=r.ref_id, reason=q.reason_id, source=ev.source)
            self._maybe_reinstate(r, A)

    def _consume_from_evidence(self, r: LiquidityReference, q: QuarantineReason, A: int, ev: CoarseBar):
        flag = f"PENETRATION_PROVEN_INSIDE_GAP:{q.reason_id}"
        if flag in r.dq_flags:
            return
        r.dq_flags.append(flag)
        if r.consumed:
            # Already consumed by an observed bar while quarantined: that observed
            # penetration was not the first penetration.
            if r.consuming_event_id:
                self.events[r.consuming_event_id].dq_flags.append("NOT_FIRST_PENETRATION")
            return
        r.consumed = True
        r.consumed_minute = None
        r.consumption_kind = ConsumptionKind.INFERRED_FROM_COARSE_DATA
        self._index_remove(r)
        price = r.price_in(ev.contract)
        e = SweepEvent(
            event_id=self._new_evt_id(), ref_id=r.ref_id, direction=r.direction,
            instrument=self.instrument.root, contract=ev.contract, level_price=price,
            level_ticks=r.ticks, source_labels=tuple(sorted({s.label.value for s in r.sources})),
            penetration_minute=None, deadline_minute=None, reference_quarantined_at_penetration=True,
        )
        e.dq_flags.append(flag)
        self.events[e.event_id] = e
        r.consuming_event_id = e.event_id
        self._reject(e, A, RejectReason.PENETRATED_DURING_MISSING_DATA)
        self._log(A, "LIQUIDITY_CONSUMED", ref_id=r.ref_id, kind="INFERRED_FROM_COARSE_DATA", source=ev.source)

    def _quarantined_refs(self):
        """References that carry any quarantine reason, in id order (deterministic)."""
        return [self.refs[k] for k in sorted(self._q_ids)]

    def _maybe_reinstate(self, r: LiquidityReference, A: int):
        if not r.quarantined or r.consumed or not r.coordinates_valid:
            return
        if all(q.resolved and not q.disproven for q in r.quarantine_reasons.values()):
            r.quarantined = False
            r.reinstatements.append(A)
            self._log(A, "LIQUIDITY_REINSTATED", ref_id=r.ref_id)

    # -------------------------------------------------------------- outputs
    def reference_snapshots(self) -> list[dict]:
        return [self.refs[k].to_dict() for k in sorted(self.refs)]

    def event_snapshots(self) -> list[dict]:
        return [self.events[k].to_dict() for k in sorted(self.events)]

    def pending_event_ids(self) -> list[str]:
        return list(self._pending)

    def tracking_event_ids(self) -> list[str]:
        return list(self._confirmed)
