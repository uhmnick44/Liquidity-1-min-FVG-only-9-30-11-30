"""Spec v1.1: closures (A5), zero-trade (I7/I11), missing data (§13, A4, I6),
unauthorized-policy guards (N-items)."""
from dataclasses import replace
from decimal import Decimal

import pytest

from liqengine.models import CoarseBar, ConsumptionKind, InvalidationReason, RejectReason, SweepStatus
from liqengine.policies import (
    CounterAcrossClosure, CounterAcrossZeroTrade, PolicyConfig, UnauthorizedPolicyError,
)
from tests.helpers import ALL_POLICIES, BASE, BSL, C, GRID, SSL, engine, feed, plan, seed, started

P = 1000


def px(t):
    return GRID.to_price(t)


# ------------------------------------------------------------------ A5 closures
def test_scheduled_closure_terminates_pending_confirmation():
    eng = engine()
    eng.begin_session(plan([(BASE, BASE + 2), (BASE + 60, BASE + 120)]))
    r = seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P, P - 1, P), (P + 2, P - 1, P + 1)])     # penetration on last minute of segment
    e = eng.events["S0000001"]
    assert e.status is SweepStatus.REJECTED and e.reject_reason is RejectReason.SCHEDULED_CLOSURE
    feed(eng, BASE + 60, [(P, P - 5, P - 3)])                    # would have confirmed after reopen
    assert e.status is SweepStatus.REJECTED and r.consumed


@pytest.mark.parametrize("pol,expected", [(CounterAcrossClosure.CONTINUE, True), (CounterAcrossClosure.RESET, False)])
def test_invalidation_counter_across_closure_policy(pol, expected):
    eng = engine(replace(ALL_POLICIES, counter_across_closure=pol))
    eng.begin_session(plan([(BASE, BASE + 3), (BASE + 60, BASE + 120)]))
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 4, P - 3, P - 1), (P + 6, P + 4, P + 5), (P + 6, P + 4, P + 5)])
    feed(eng, BASE + 60, [(P + 6, P + 4, P + 5)])
    assert eng.events["S0000001"].invalidated is expected


def test_counter_across_closure_requires_authorization():
    eng = engine(replace(ALL_POLICIES, counter_across_closure=None))
    eng.begin_session(plan([(BASE, BASE + 2), (BASE + 60, BASE + 120)]))
    seed(eng, BSL, P, BASE)
    with pytest.raises(UnauthorizedPolicyError) as ei:
        feed(eng, BASE, [(P + 4, P - 3, P - 1), (P, P - 3, P - 1)])
    assert ei.value.item == "N4"


def test_multi_segment_session_requires_n7():
    eng = engine(replace(ALL_POLICIES, exchange_session_unit=None))
    with pytest.raises(UnauthorizedPolicyError):
        eng.begin_session(plan([(BASE, BASE + 2), (BASE + 60, BASE + 120)]))


# ------------------------------------------------------------------ I7 / I11 zero-trade
def test_zero_trade_minutes_advance_confirmation_window():
    eng = started()
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 2, P, P + 1), "Z", "Z", "Z"])
    e = eng.events["S0000001"]
    assert e.reject_reason is RejectReason.NO_CONFIRMATION_WITHIN_WINDOW and e.rejected_minute == BASE + 3


def test_zero_trade_then_confirmation_inside_window():
    eng = started()
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 2, P, P + 1), "Z", "Z", (P, P - 3, P - 1)])
    e = eng.events["S0000001"]
    assert e.status is SweepStatus.CONFIRMED and e.confirmation_minute == BASE + 3


@pytest.mark.parametrize("pol,expected", [(CounterAcrossZeroTrade.UNCHANGED, True), (CounterAcrossZeroTrade.RESET, False)])
def test_zero_trade_invalidation_counter_policy(pol, expected):
    eng = started(replace(ALL_POLICIES, counter_across_zero_trade=pol))
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 4, P - 3, P - 1), (P + 6, P + 4, P + 5), "Z", (P + 6, P + 4, P + 5), (P + 6, P + 4, P + 5)])
    e = eng.events["S0000001"]
    if expected:
        assert e.invalidated and e.invalidation_minute == BASE + 4
    else:
        assert not e.invalidated and e.invalidation_count == 2


def test_zero_trade_counter_requires_authorization():
    eng = started(replace(ALL_POLICIES, counter_across_zero_trade=None))
    seed(eng, BSL, P, BASE)
    with pytest.raises(UnauthorizedPolicyError):
        feed(eng, BASE, [(P + 4, P - 3, P - 1), "Z"])


def test_zero_trade_does_not_quarantine_or_invalidate():
    eng = started()
    r = seed(eng, BSL, P + 50, BASE)
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 4, P - 3, P - 1)] + ["Z"] * 10)
    assert not r.quarantined and not eng.events["S0000001"].invalidated


# ------------------------------------------------------------------ §13 missing data
@pytest.mark.parametrize("kind", ["U", "M"])
def test_missing_minute_rejects_pending_invalidates_confirmed_quarantines_all(kind):
    eng = started()
    far = seed(eng, BSL, P + 100, BASE)
    low = seed(eng, SSL, P - 100, BASE)
    seed(eng, BSL, P, BASE)
    seed(eng, BSL, P + 10, BASE)
    feed(eng, BASE, [(P + 4, P - 3, P - 1), (P + 11, P + 2, P + 10)])   # S1 confirmed, S2 pending
    feed(eng, BASE + 2, [kind])
    s1, s2 = eng.events["S0000001"], eng.events["S0000002"]
    assert s1.invalidated and s1.invalidation_reason is InvalidationReason.DATA_GAP
    assert s2.status is SweepStatus.REJECTED and s2.reject_reason is RejectReason.DATA_GAP
    assert far.quarantined and low.quarantined and not far.consumed
    assert any(d["kind"] == "MISSING_DATA_START" for d in eng.data_quality)


def test_quarantined_level_penetration_consumes_but_flags_event():
    eng = started()
    r = seed(eng, BSL, P, BASE)
    feed(eng, BASE, ["U", (P + 2, P - 3, P - 1)])
    e = eng.events["S0000001"]
    assert r.consumed and e.reference_quarantined_at_penetration and "REFERENCE_QUARANTINED" in e.dq_flags


def coarse(start, end, hi, lo, avail, contract=C):
    return CoarseBar(contract, start, end, px(hi), px(lo), avail, "test-1h")


def test_coarse_evidence_untouched_reinstates_from_availability_only():
    eng = started()
    r = seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P - 5, P - 9, P - 6), "U", (P - 5, P - 9, P - 6)])
    assert r.quarantined
    eng.apply_coarse_evidence(coarse(BASE, BASE + 3, P - 5, P - 9, BASE + 10))
    assert not r.quarantined and r.reinstatements == [BASE + 10]
    q = next(iter(r.quarantine_reasons.values()))
    assert q.resolved_minute == BASE + 10 and q.since_minute == BASE + 1


def test_coarse_evidence_proves_penetration_in_gap():
    eng = started()
    r = seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P - 5, P - 9, P - 6), "U", (P - 5, P - 9, P - 6)])
    eng.apply_coarse_evidence(coarse(BASE, BASE + 3, P + 3, P - 9, BASE + 10))
    assert r.consumed and r.consumption_kind is ConsumptionKind.INFERRED_FROM_COARSE_DATA
    e = eng.events[r.consuming_event_id]
    assert e.reject_reason is RejectReason.PENETRATED_DURING_MISSING_DATA and e.penetration_minute is None


def test_coarse_evidence_ambiguous_keeps_quarantine():
    eng = started()
    # gap G1 happens before the level exists; gap G2 after. A coarse high above the
    # level could have printed in G1, so it proves nothing about the level.
    feed(eng, BASE, ["U", (P - 5, P - 9, P - 6)])
    r = seed(eng, BSL, P, BASE + 2)
    feed(eng, BASE + 2, ["U", (P - 4, P - 9, P - 6)])
    eng.apply_coarse_evidence(coarse(BASE, BASE + 4, P + 3, P - 9, BASE + 10))
    assert r.quarantined and not r.consumed


def test_coarse_evidence_before_its_availability_is_refused():
    from liqengine.engine import EngineStateError
    eng = started()
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, ["U", (P - 5, P - 9, P - 6), (P - 5, P - 9, P - 6)])
    with pytest.raises(EngineStateError):
        eng.apply_coarse_evidence(coarse(BASE, BASE + 3, P - 5, P - 9, BASE + 2))


def test_reinstatement_requires_level_unconsumed():
    eng = started()
    r = seed(eng, BSL, P, BASE)
    feed(eng, BASE, ["U", (P + 2, P - 3, P - 1)])  # consumed while quarantined
    eng.apply_coarse_evidence(coarse(BASE, BASE + 1, P - 5, P - 9, BASE + 10))
    assert r.consumed and r.quarantined and not r.reinstatements


def test_events_during_quarantine_never_become_eligible_after_reinstatement():
    from liqengine.calendars import ExchangeCalendar, ExchangeSession
    from liqengine.eligibility import TradingWindow, evaluate_setup
    from datetime import time
    eng = started()
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, ["U", (P + 2, P - 3, P - 1)])
    e = eng.events["S0000001"]
    cal = ExchangeCalendar("X", "1", True, "", [ExchangeSession("2025-03-04", ((BASE - 600, BASE + 600),))])
    v = evaluate_setup(e, TradingWindow(time(9, 0), time(12, 0), "America/New_York"), cal)
    assert not v.eligible and v.reason == "REFERENCE_QUARANTINED"


# ------------------------------------------------------------------ N3 incomplete windows
def test_incomplete_window_requires_policy():
    eng = engine(replace(ALL_POLICIES, incomplete_window_liquidity=None))
    eng.begin_session(plan([(BASE, BASE + 5)]))
    with pytest.raises(UnauthorizedPolicyError) as ei:
        feed(eng, BASE, [(P, P - 1, P), "U", (P, P - 1, P), (P, P - 1, P), (P, P - 1, P)])
    assert ei.value.item == "N3"


def test_incomplete_window_quarantine_and_do_not_register():
    from liqengine.policies import IncompleteWindowLiquidity as I
    for pol in (I.QUARANTINE, I.DO_NOT_REGISTER):
        eng = engine(replace(ALL_POLICIES, incomplete_window_liquidity=pol))
        eng.begin_session(plan([(BASE, BASE + 5)]))
        feed(eng, BASE, [(P + 3, P - 1, P), "U", (P, P - 2, P), (P, P - 1, P), (P, P - 1, P)])
        sess = [r for r in eng.refs.values() if any(s.label.value == "EXCHANGE_SESSION" for s in r.sources)]
        if pol is I.QUARANTINE:
            assert len(sess) == 2 and all(r.quarantined for r in sess)
            assert {r.ticks for r in sess} == {P + 3, P - 2}
        else:
            assert sess == []


def test_dual_sided_with_quarantined_side_requires_policy():
    eng = started(replace(ALL_POLICIES, dual_sided_includes_quarantined=None))
    seed(eng, BSL, P + 5, BASE)
    feed(eng, BASE, ["U"])
    seed(eng, SSL, P - 5, BASE + 1)
    with pytest.raises(UnauthorizedPolicyError) as ei:
        feed(eng, BASE + 1, [(P + 6, P - 6, P)])
    assert ei.value.item == "N11"
