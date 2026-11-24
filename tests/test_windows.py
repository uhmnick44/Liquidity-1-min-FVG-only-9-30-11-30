"""Spec §4 + A8/A9/A11: session, RTH, pre-window and 15m fractal liquidity; calendars; DST."""
from dataclasses import replace
from datetime import date, datetime, time, timezone

import pytest

from liqengine.calendars import (
    CalendarUnavailable, ExchangeCalendar, ExchangeSession, ReferenceCalendar, ReferenceDay, plan_session,
)
from liqengine.models import SourceLabel
from liqengine.policies import PreWindowWhenReferenceClosed, UnauthorizedPolicyError
from liqengine.timeutil import dt_to_minute, local_to_minute
from tests.helpers import ALL_POLICIES, BASE, BSL, SSL, engine, feed, plan

P = 1000


def refs_with(eng, label):
    return [r for r in eng.refs.values() if any(s.label is label for s in r.sources)]


def candle(h, l, n=15, at=None):
    """15 one-minute bars; the high/low printed at offset `at` (default 5)."""
    at = 5 if at is None else at
    out = []
    for i in range(n):
        out.append((h, l, (h + l) // 2) if i == at else (h - 1, l + 1, (h + l) // 2))
    return out


# ------------------------------------------------------------------ 15m fractals
def test_bsl_fractal_registered_after_third_candle_closes():
    eng = engine()
    eng.begin_session(plan([(BASE, BASE + 600)]))
    feed(eng, BASE, candle(P, P - 30) + candle(P + 5, P - 28) + candle(P + 2, P - 26))
    sw = refs_with(eng, SourceLabel.SWING_15M)
    assert len(sw) == 1
    r = sw[0]
    assert r.direction is BSL and r.ticks == P + 5
    assert r.sources[0].availability_minute == BASE + 45
    assert r.sources[0].formation_minute == BASE + 15 + 5


def test_ssl_fractal():
    eng = engine()
    eng.begin_session(plan([(BASE, BASE + 600)]))
    feed(eng, BASE, candle(P + 30, P) + candle(P + 28, P - 4) + candle(P + 26, P - 1))
    (r,) = refs_with(eng, SourceLabel.SWING_15M)
    assert r.direction is SSL and r.ticks == P - 4


def test_equal_highs_do_not_qualify():
    eng = engine()
    eng.begin_session(plan([(BASE, BASE + 600)]))
    feed(eng, BASE, candle(P + 5, P - 30) + candle(P + 5, P - 28) + candle(P + 2, P - 26))
    assert refs_with(eng, SourceLabel.SWING_15M) == []


def test_no_fractal_across_scheduled_closure():
    eng = engine()
    eng.begin_session(plan([(BASE, BASE + 30), (BASE + 60, BASE + 120)]))
    feed(eng, BASE, candle(P, P - 30) + candle(P + 5, P - 28))
    feed(eng, BASE + 60, candle(P + 2, P - 26))
    assert refs_with(eng, SourceLabel.SWING_15M) == []


def test_shortened_candle_excluded():
    eng = engine()
    eng.begin_session(plan([(BASE + 5, BASE + 600)]))
    # would be a swing (P+5 > P, P+5 > P+2) if the 10-minute first candle counted
    feed(eng, BASE + 5, candle(P, P - 30, n=10) + candle(P + 5, P - 28) + candle(P + 2, P - 26))
    assert refs_with(eng, SourceLabel.SWING_15M) == []
    feed(eng, BASE + 45, candle(P + 1, P - 24) + candle(P + 4, P - 22) + candle(P + 3, P - 20))
    assert [r.ticks for r in refs_with(eng, SourceLabel.SWING_15M)] == [P + 4]


def test_candle_with_missing_minute_excluded_and_restarts():
    eng = engine()
    eng.begin_session(plan([(BASE, BASE + 600)]))
    bad = candle(P + 5, P - 28)
    bad[7] = "U"
    feed(eng, BASE, candle(P, P - 30) + bad + candle(P + 2, P - 26))
    assert refs_with(eng, SourceLabel.SWING_15M) == []


def test_zero_trade_minutes_inside_traded_candle_keep_it_valid():
    eng = engine()
    eng.begin_session(plan([(BASE, BASE + 600)]))
    mid = candle(P + 5, P - 28)
    mid[0] = mid[14] = "Z"
    feed(eng, BASE, candle(P, P - 30) + mid + candle(P + 2, P - 26))
    assert [r.ticks for r in refs_with(eng, SourceLabel.SWING_15M)] == [P + 5]


def test_fully_zero_trade_candle_cannot_participate_and_restarts():
    eng = engine()
    eng.begin_session(plan([(BASE, BASE + 600)]))
    feed(eng, BASE, candle(P, P - 30) + ["Z"] * 15 + candle(P + 5, P - 28) + candle(P + 2, P - 26))
    assert refs_with(eng, SourceLabel.SWING_15M) == []   # (c1, zero, c3) and (zero, c3, c4) invalid


# ------------------------------------------------------------------ session windows
def test_session_rth_prewindow_registered_at_their_close_only():
    eng = engine()
    eng.begin_session(plan([(BASE, BASE + 120)], rth=(BASE + 30, BASE + 90), pre=(BASE, BASE + 30)))
    flat = lambda h, l, n: [(h, l, (h + l) // 2)] * n  # noqa: E731
    feed(eng, BASE, flat(P + 10, P - 10, 30))       # pre-window H/L
    pre = refs_with(eng, SourceLabel.PRE_WINDOW)
    assert {r.ticks for r in pre} == {P + 10, P - 10}
    assert all(r.sources[0].availability_minute == BASE + 30 for r in pre)
    feed(eng, BASE + 30, flat(P + 9, P - 9, 60))
    rth = refs_with(eng, SourceLabel.RTH)
    assert {r.ticks for r in rth} == {P + 9, P - 9}
    feed(eng, BASE + 90, flat(P + 9, P - 9, 30))
    sess = refs_with(eng, SourceLabel.EXCHANGE_SESSION)
    assert all(s.availability_minute == BASE + 120 for r in sess for s in r.sources
               if s.label is SourceLabel.EXCHANGE_SESSION)
    # session high P+10 consolidated with the pre-window high
    assert any({s.label for s in r.sources} == {SourceLabel.PRE_WINDOW, SourceLabel.EXCHANGE_SESSION} for r in sess)


def test_developing_session_extreme_is_not_liquidity():
    eng = engine()
    eng.begin_session(plan([(BASE, BASE + 120)]))
    feed(eng, BASE, [(P, P - 5, P - 2), (P + 3, P - 5, P - 2), (P + 6, P - 5, P - 2)])
    assert not eng.events


# ------------------------------------------------------------------ calendars / DST / A8 / A9
NY = "America/New_York"


def ex_session(d: date):
    prev = date.fromordinal(d.toordinal() - 1)
    return ExchangeSession(d.isoformat(), ((local_to_minute(prev, time(18), NY), local_to_minute(d, time(17), NY)),))


def ref_cal(days):
    return ReferenceCalendar("NYSE", "test", True, "test", NY, {d.day: d for d in days})


def test_rth_bounds_follow_dst():
    before, after = date(2025, 3, 7), date(2025, 3, 10)   # US DST began 2025-03-09
    cal = ref_cal([ReferenceDay(before, time(9, 30), time(16)), ReferenceDay(after, time(9, 30), time(16))])
    p1 = plan_session(ex_session(before), cal, lambda: None, None, NY)
    p2 = plan_session(ex_session(after), cal, lambda: None, None, NY)
    assert p1.rth[0] == dt_to_minute(datetime(2025, 3, 7, 14, 30, tzinfo=timezone.utc))
    assert p2.rth[0] == dt_to_minute(datetime(2025, 3, 10, 13, 30, tzinfo=timezone.utc))
    assert p1.pre_window == (ex_session(before).start, p1.rth[0])


def test_early_close_ends_rth_at_scheduled_close():
    d = date(2025, 11, 28)
    cal = ref_cal([ReferenceDay(d, time(9, 30), time(13))])
    p = plan_session(ex_session(d), cal, lambda: None, None, NY)
    assert p.rth[1] == local_to_minute(d, time(13), NY)


def test_reference_closed_no_rth_and_prewindow_policy():
    d = date(2025, 1, 20)
    cal = ref_cal([ReferenceDay(d, None, None)])
    p = plan_session(ex_session(d), cal, lambda: "DO_NOT_GENERATE", None, NY)
    assert p.rth is None and p.pre_window is None and p.reference_closed
    p = plan_session(ex_session(d), cal, lambda: "GENERATE_TO_NOMINAL_TIME", time(9, 30), NY)
    assert p.pre_window[1] == local_to_minute(d, time(9, 30), NY)

    def unset():
        return ALL_POLICIES.__class__().require("pre_window_when_reference_closed", "test")
    with pytest.raises(UnauthorizedPolicyError):
        plan_session(ex_session(d), cal, unset, None, NY)


def test_missing_calendar_entry_is_not_inferred():
    cal = ref_cal([])
    with pytest.raises(CalendarUnavailable):
        plan_session(ex_session(date(2025, 3, 7)), cal, lambda: None, None, NY)


def test_nonexistent_dst_time_raises():
    with pytest.raises(ValueError):
        local_to_minute(date(2025, 3, 9), time(2, 30), NY)


def test_exchange_calendar_rejects_overlaps():
    from liqengine.calendars import CalendarError
    s1 = ExchangeSession("2025-03-04", ((0, 100),))
    s2 = ExchangeSession("2025-03-05", ((50, 200),))
    with pytest.raises(CalendarError):
        ExchangeCalendar("X", "1", True, "", [s1, s2])
