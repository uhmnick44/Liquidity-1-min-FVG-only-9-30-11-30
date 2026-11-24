"""Spec §6-§10: penetration, confirmation, consumption, extreme, invalidation."""
import pytest

from liqengine.models import ConsumptionKind, InvalidationReason, RejectReason, SourceLabel, SweepStatus
from tests.helpers import BASE, BSL, SSL, feed, seed, started

P = 1000  # level in ticks


def only_event(eng):
    assert len(eng.events) == 1
    return next(iter(eng.events.values()))


# ---------------------------------------------------------------- penetration
def test_bsl_high_equal_to_level_is_not_penetration():
    eng = started()
    r = seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P, P - 4, P - 2)])
    assert not r.consumed and not eng.events


def test_bsl_high_one_tick_above_is_penetration_and_consumes():
    eng = started()
    r = seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 1, P - 4, P + 1)])
    assert r.consumed and r.consumed_minute == BASE
    assert r.consumption_kind is ConsumptionKind.OBSERVED_PENETRATION
    assert only_event(eng).penetration_minute == BASE


def test_ssl_low_equal_not_penetration_one_tick_below_is():
    eng = started()
    r = seed(eng, SSL, P, BASE)
    feed(eng, BASE, [(P + 4, P, P + 2)])
    assert not r.consumed
    feed(eng, BASE + 1, [(P + 4, P - 1, P + 2)])
    assert r.consumed and only_event(eng).penetration_minute == BASE + 1


# ---------------------------------------------------------------- confirmation
def test_same_candle_confirmation_k0():
    eng = started()
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 3, P - 4, P - 1)])
    e = only_event(eng)
    assert e.status is SweepStatus.CONFIRMED and e.confirmation_minute == BASE
    assert e.extreme_ticks == P + 3


def test_close_equal_to_level_does_not_confirm():
    eng = started()
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 3, P - 4, P), (P + 1, P - 1, P), (P, P - 1, P), (P, P - 1, P)])
    e = only_event(eng)
    assert e.status is SweepStatus.REJECTED and e.reject_reason is RejectReason.NO_CONFIRMATION_WITHIN_WINDOW
    assert e.rejected_minute == BASE + 3


def test_confirmation_on_k3_is_accepted():
    eng = started()
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 2, P, P + 1), (P + 5, P, P + 1), (P + 1, P, P + 1), (P + 1, P - 3, P - 1)])
    e = only_event(eng)
    assert e.status is SweepStatus.CONFIRMED and e.confirmation_minute == BASE + 3
    assert e.extreme_ticks == P + 5   # max high over t..q includes candle 1


def test_confirmation_on_k4_is_too_late_but_level_stays_consumed():
    eng = started()
    r = seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 2, P, P + 1)] * 4 + [(P + 1, P - 3, P - 1)])
    e = only_event(eng)
    assert e.status is SweepStatus.REJECTED
    assert r.consumed and not eng._index[BSL]


def test_ssl_confirmation_and_extreme():
    eng = started()
    seed(eng, SSL, P, BASE)
    feed(eng, BASE, [(P, P - 2, P - 1), (P, P - 6, P - 1), (P + 3, P - 1, P + 1)])
    e = only_event(eng)
    assert e.status is SweepStatus.CONFIRMED and e.confirmation_minute == BASE + 2
    assert e.extreme_ticks == P - 6


def test_extreme_frozen_after_confirmation():
    eng = started()
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 2, P - 3, P - 1), (P + 9, P - 3, P - 1)])
    assert only_event(eng).extreme_ticks == P + 2


def test_consumption_is_permanent_and_revisit_does_not_create_liquidity():
    eng = started()
    r = seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 2, P - 3, P - 1), (P - 1, P - 5, P - 3), (P + 2, P - 3, P - 1)])
    assert r.consumed and len(eng.events) == 1 and len(eng.refs) == 1


def test_new_independent_level_at_consumed_price_gets_new_id():
    eng = started()
    r1 = seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 2, P - 3, P - 1)])
    r2 = seed(eng, BSL, P, BASE + 1, origin="second")
    assert r2.ref_id != r1.ref_id and not r2.consumed
    feed(eng, BASE + 1, [(P + 1, P - 3, P - 1)])
    assert r2.consumed and len(eng.events) == 2


def test_consolidation_same_price_same_direction_preserves_labels():
    eng = started()
    r1 = seed(eng, BSL, P, BASE, label=SourceLabel.RTH, origin="a")
    r2 = seed(eng, BSL, P, BASE, label=SourceLabel.SWING_15M, origin="b")
    r3 = seed(eng, SSL, P, BASE, label=SourceLabel.SWING_15M, origin="c")  # same price, other side
    assert r3.ref_id != r1.ref_id
    eng._index_remove(r3); r3.consumed = True  # take it out of play for the rest of the test
    assert r1 is r2 and r3 is not r1
    assert {s.label for s in r1.sources} == {SourceLabel.RTH, SourceLabel.SWING_15M}
    feed(eng, BASE, [(P + 1, P - 3, P - 1)])
    e = only_event(eng)
    assert set(e.source_labels) == {"RTH", "SWING_15M"}


# ---------------------------------------------------------------- invalidation
def confirmed_bsl():
    eng = started()
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 4, P - 3, P - 1)])  # E = P+4, q = BASE
    return eng, only_event(eng)


def test_three_consecutive_closes_above_extreme_invalidate_on_third():
    eng, e = confirmed_bsl()
    feed(eng, BASE + 1, [(P + 6, P + 4, P + 5), (P + 6, P + 4, P + 5)])
    assert not e.invalidated and e.invalidation_count == 2
    feed(eng, BASE + 3, [(P + 6, P + 4, P + 5)])
    assert e.invalidated and e.invalidation_minute == BASE + 3
    assert e.invalidation_reason is InvalidationReason.THREE_CLOSES_BEYOND_EXTREME


def test_close_equal_to_extreme_resets_counter():
    eng, e = confirmed_bsl()
    feed(eng, BASE + 1, [(P + 6, P, P + 5), (P + 6, P, P + 5), (P + 6, P, P + 4), (P + 6, P, P + 5)])
    assert not e.invalidated and e.invalidation_count == 1


def test_wick_beyond_extreme_does_not_count():
    eng, e = confirmed_bsl()
    feed(eng, BASE + 1, [(P + 20, P, P + 1)] * 5)
    assert not e.invalidated and e.invalidation_count == 0


def test_confirmation_candle_close_not_counted():
    eng = started()
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 1, P - 3, P + 1), (P + 1, P - 3, P - 1)])  # q = BASE+1, E = P+1
    e = only_event(eng)
    assert e.confirmation_minute == BASE + 1 and e.invalidation_count == 0
    feed(eng, BASE + 2, [(P + 5, P + 2, P + 3)] * 3)
    assert e.invalidated and e.invalidation_minute == BASE + 4


def test_ssl_invalidation_and_no_reactivation():
    eng = started()
    r = seed(eng, SSL, P, BASE)
    feed(eng, BASE, [(P + 3, P - 4, P + 1)])
    e = only_event(eng)
    feed(eng, BASE + 1, [(P - 4, P - 8, P - 5)] * 3)
    assert e.invalidated and r.consumed
    feed(eng, BASE + 4, [(P + 5, P - 1, P + 3)] * 3)
    assert e.invalidated and r.consumed and len(eng.events) == 1


def test_opposing_confirmed_sweeps_coexist_independently():
    eng = started()
    seed(eng, BSL, P + 10, BASE)
    seed(eng, SSL, P - 10, BASE)
    feed(eng, BASE, [(P + 12, P, P + 8), (P, P - 13, P - 8)])
    evs = sorted(eng.events.values(), key=lambda e: e.event_id)
    assert all(e.status is SweepStatus.CONFIRMED for e in evs)
    feed(eng, BASE + 2, [(P + 20, P + 13, P + 15)] * 3)   # invalidates BSL only
    b, s = (e for e in evs if e.direction is BSL), (e for e in evs if e.direction is SSL)
    assert next(b).invalidated and not next(s).invalidated


# ---------------------------------------------------------------- multiple / dual
def test_multiple_same_direction_levels_are_independent():
    eng = started()
    seed(eng, BSL, P, BASE)
    seed(eng, BSL, P + 3, BASE)
    feed(eng, BASE, [(P + 5, P - 2, P + 1), (P + 5, P - 2, P - 1)])
    evs = {e.level_ticks: e for e in eng.events.values()}
    assert evs[P].confirmation_minute == BASE + 1     # close P+1 > P on candle 0 does not confirm P
    assert evs[P + 3].confirmation_minute == BASE     # close P+1 < P+3 confirms on candle 0
    assert evs[P].extreme_ticks == P + 5 and evs[P + 3].extreme_ticks == P + 5


def test_dual_sided_rejects_both_and_consumes_both():
    eng = started()
    rb = seed(eng, BSL, P + 5, BASE)
    rs = seed(eng, SSL, P - 5, BASE)
    feed(eng, BASE, [(P + 6, P - 6, P)])
    assert rb.consumed and rs.consumed
    assert all(e.status is SweepStatus.REJECTED and e.reject_reason is RejectReason.DUAL_SIDED
               and e.dual_sided for e in eng.events.values())
    assert any(d["kind"] == "DUAL_SIDED_SWEEP" for d in eng.data_quality)


def test_dual_sided_bar_still_confirms_earlier_pending_event():
    eng = started()
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 2, P - 1, P + 1)])         # pending
    seed(eng, BSL, P + 10, BASE + 1)
    seed(eng, SSL, P - 10, BASE + 1)
    feed(eng, BASE + 1, [(P + 11, P - 11, P - 2)])   # dual-sided for the new levels
    first = eng.events["S0000001"]
    assert first.status is SweepStatus.CONFIRMED and first.confirmation_minute == BASE + 1
    assert first.extreme_ticks == P + 11
    assert all(e.dual_sided for k, e in eng.events.items() if k != "S0000001")


def test_reference_not_testable_before_availability():
    eng = started()
    feed(eng, BASE, [(P + 5, P - 5, P)])
    r = seed(eng, BSL, P, BASE + 1)
    assert not r.consumed
    feed(eng, BASE + 1, [(P + 1, P - 5, P)])
    assert r.consumed


@pytest.mark.parametrize("bad", ["Z", "U"])
def test_zero_trade_and_missing_minutes_do_not_confirm(bad):
    eng = started()
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 2, P, P + 1), bad, bad, bad])
    e = only_event(eng)
    assert e.status is SweepStatus.REJECTED
