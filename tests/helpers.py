from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from liqengine.calendars import ExchangeSession, SessionPlan
from liqengine.data import Bar, MinuteStatus
from liqengine.engine import LiquidityEngine
from liqengine.instrument import Contract, InstrumentConfig
from liqengine.models import Direction, SourceLabel
from liqengine.policies import (
    CounterAcrossClosure, CounterAcrossZeroTrade, ExchangeSessionUnit, IncompleteWindowLiquidity,
    InitialContract, PolicyConfig, PreWindowWhenReferenceClosed, QuarantineCollision,
)
from liqengine.ticks import TickGrid
from liqengine.timeutil import dt_to_minute

TICK = Decimal("0.25")
GRID = TickGrid(TICK)
C = "TESTH5"

# 2025-03-04 14:30Z, aligned to 15 minutes
BASE = dt_to_minute(datetime(2025, 3, 4, 14, 30, tzinfo=timezone.utc))
assert BASE % 15 == 0

ALL_POLICIES = PolicyConfig(
    incomplete_window_liquidity=IncompleteWindowLiquidity.QUARANTINE,
    counter_across_closure=CounterAcrossClosure.CONTINUE,
    counter_across_zero_trade=CounterAcrossZeroTrade.UNCHANGED,
    pre_window_when_reference_closed=PreWindowWhenReferenceClosed.DO_NOT_GENERATE,
    exchange_session_unit=ExchangeSessionUnit.TRADE_DATE,
    initial_contract=InitialContract.EXPLICIT,
    quarantine_collision=QuarantineCollision.SEPARATE,
    dual_sided_includes_quarantined=True,
)


def instrument(expiring=False, contracts=None):
    contracts = contracts or [Contract(C, 1, expiration=10**9, tick_size=TICK, in_expiration_cycle=True)]
    return InstrumentConfig(root="TEST", has_expirations=expiring, tick_size=TICK,
                            exchange_calendar_id="XCAL", reference_calendar_id=None,
                            contracts=contracts, verified=True)


def engine(policies=ALL_POLICIES, inst=None):
    inst = inst or instrument()
    return LiquidityEngine(inst, policies, C, GRID)


def plan(segments, rth=None, pre=None, sid="2025-03-04"):
    return SessionPlan(ExchangeSession(sid, tuple(segments)), rth, pre, reference_closed=False)


def bar(m, h, l, c, o=None, contract=C, v=1):
    o = c if o is None else o
    return Bar(m, o, h, l, c, v, contract)


def feed(eng, start, specs, contract=C):
    """specs: list of (h, l, c) / (o, h, l, c) tuples, 'Z' zero-trade, 'M' confirmed missing, 'U' uncertain."""
    m = start
    for s in specs:
        if s == "Z":
            eng.process_minute(m, MinuteStatus.VERIFIED_ZERO_TRADE, None)
        elif s == "M":
            eng.process_minute(m, MinuteStatus.CONFIRMED_MISSING, None)
        elif s == "U":
            eng.process_minute(m, MinuteStatus.UNCERTAIN, None)
        else:
            if len(s) == 3:
                h, l, c = s
                o = c
            else:
                o, h, l, c = s
            eng.process_minute(m, MinuteStatus.OBSERVED, Bar(m, o, h, l, c, 1, contract))
        m += 1
    return m


def seed(eng, direction, ticks, availability, label=SourceLabel.SWING_15M, origin="seed", formation=None):
    return eng._register(direction, ticks, label, origin, formation if formation is not None else availability - 1,
                         availability)


def started(policies=ALL_POLICIES, length=600, start=BASE, inst=None):
    """Engine with one open single-segment session [start, start+length)."""
    eng = engine(policies, inst)
    eng.begin_session(plan([(start, start + length)]))
    return eng


BSL, SSL = Direction.BSL, Direction.SSL
