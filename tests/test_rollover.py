"""A12 / I1-I5 / I10: contract selection and rollover."""
from dataclasses import replace
from decimal import Decimal

import pytest

from liqengine.calendars import ExchangeCalendar, ExchangeSession
from liqengine.data import Bar, BarStore
from liqengine.driver import UnverifiedConfigurationError, run
from liqengine.engine import LiquidityEngine
from liqengine.instrument import Contract, InstrumentConfig
from liqengine.models import ExpiryReason, SourceLabel, SweepStatus
from liqengine.policies import InitialContract
from liqengine.rollover import ContractSelector, SessionVolume
from liqengine.ticks import TickGrid
from tests.helpers import ALL_POLICIES, BASE, BSL, GRID, SSL, TICK, feed, plan, seed

LEN = 60     # minutes per synthetic session
GAP = 120    # closure between sessions


def contracts(**exp):
    out = []
    for i, (sym, e) in enumerate(sorted(exp.items(), key=lambda kv: kv[1])):
        out.append(Contract(sym, i + 1, expiration=e, tick_size=TICK, in_expiration_cycle=True))
    return out


def inst(cs):
    return InstrumentConfig(root="TEST", has_expirations=True, tick_size=TICK, exchange_calendar_id="XCAL",
                            reference_calendar_id=None, contracts=cs, verified=True)


def calendar(n):
    sessions = []
    for i in range(n):
        a = BASE + i * (LEN + GAP)
        sessions.append(ExchangeSession(f"2025-03-{4 + i:02d}", ((a, a + LEN),)))
    return ExchangeCalendar("XCAL", "test-1", True, "synthetic", sessions)


def fill(store, sym, session, price, vol, last_close=None):
    for m in session.minutes():
        c = last_close if (last_close is not None and m == session.end - 1) else price
        store.add(Bar(m, c, max(price, c), min(price, c), c, vol, sym))


def vol(sym, sid, v, at):
    return SessionVolume(sym, sid, v, at, "test")


# ------------------------------------------------------------------ selector unit tests
def sel_setup():
    cs = contracts(A=10**8, B=10**8 + 1000, C=10**8 + 2000)
    return inst(cs), calendar(1).sessions[0]


def test_strictly_greater_volume_required():
    i, s = sel_setup()
    st = BarStore()
    sel = ContractSelector(i, "A")
    v = {"A": vol("A", s.session_id, 100, s.end), "B": vol("B", s.session_id, 100, s.end),
         "C": vol("C", s.session_id, 1, s.end)}
    assert sel.evaluate(s, v, st, None, s.end) is None


def test_equal_volume_successors_tie_break_earliest_expiry():
    i, s = sel_setup()
    st = BarStore()
    for sym in "ABC":
        fill(st, sym, s, 1000, 1)
    sel = ContractSelector(i, "A")
    v = {"A": vol("A", s.session_id, 10, s.end), "B": vol("B", s.session_id, 50, s.end),
         "C": vol("C", s.session_id, 50, s.end)}
    d = sel.evaluate(s, v, st, None, s.end)
    assert d.to_contract == "B" and "EQUAL_VOLUME_TIE_EARLIEST_EXPIRY" in d.flags


def test_forward_only_older_contract_never_reselected():
    i, s = sel_setup()
    sel = ContractSelector(i, "B")
    sel.replaced.append("A")
    st = BarStore()
    v = {"A": vol("A", s.session_id, 10**6, s.end), "B": vol("B", s.session_id, 10, s.end),
         "C": vol("C", s.session_id, 5, s.end)}
    assert sel.evaluate(s, v, st, None, s.end) is None


def test_unverified_volume_suspends_decision():
    i, s = sel_setup()
    sel = ContractSelector(i, "A")
    v = {"A": vol("A", s.session_id, 10, s.end), "B": vol("B", s.session_id, None, s.end),
         "C": vol("C", s.session_id, 5, s.end)}
    assert sel.evaluate(s, v, BarStore(), None, s.end) is None
    assert sel.uncertain[-1]["reason"] == "SUCCESSOR_VOLUME_UNVERIFIED"


def test_volume_not_yet_available_is_refused():
    i, s = sel_setup()
    sel = ContractSelector(i, "A")
    v = {"A": vol("A", s.session_id, 10, s.end + 5)}
    with pytest.raises(ValueError):
        sel.evaluate(s, v, BarStore(), None, s.end)


@pytest.mark.parametrize("p_old,p_new", [(1000, 1040), (1000, 960)])
def test_delta_from_final_scheduled_candle_closes(p_old, p_new):
    i, s = sel_setup()
    st = BarStore()
    fill(st, "A", s, 900, 1, last_close=p_old)
    fill(st, "B", s, 900, 1, last_close=p_new)
    sel = ContractSelector(i, "A")
    v = {"A": vol("A", s.session_id, 10, s.end), "B": vol("B", s.session_id, 20, s.end),
         "C": vol("C", s.session_id, 0, s.end)}
    d = sel.evaluate(s, v, st, None, s.end)
    assert d.delta == GRID.to_price(p_new) - GRID.to_price(p_old) and d.reference_minute == s.end - 1


def test_missing_reference_price_gives_no_delta():
    i, s = sel_setup()
    st = BarStore()
    fill(st, "A", s, 1000, 1)
    sel = ContractSelector(i, "A")
    v = {"A": vol("A", s.session_id, 10, s.end), "B": vol("B", s.session_id, 20, s.end),
         "C": vol("C", s.session_id, 0, s.end)}
    d = sel.evaluate(s, v, st, None, s.end)
    assert d.delta is None and "REFERENCE_PRICE_UNAVAILABLE" in d.flags


# ------------------------------------------------------------------ engine translation
def eng_with_levels():
    cs = contracts(A=10**8, B=10**8 + 1000)
    e = LiquidityEngine(inst(cs), ALL_POLICIES, "A", GRID)
    e.begin_session(plan([(BASE, BASE + 10)]))
    hi = seed(e, BSL, 1100, BASE)
    lo = seed(e, SSL, 900, BASE)
    used = seed(e, BSL, 1010, BASE)
    seed(e, SSL, 990, BASE)
    feed(e, BASE, [(1011, 995, 1005)] + [(1006, 995, 1000)] * 9, contract="A")  # consumes 1010 BSL, confirmed
    return e, hi, lo, used


@pytest.mark.parametrize("delta", [Decimal("10.00"), Decimal("-7.25")])
def test_translation_positive_negative_preserves_history(delta):
    e, hi, lo, used = eng_with_levels()
    ev = e.events["S0000001"]
    assert ev.status is SweepStatus.CONFIRMED
    e.rollover(BASE + 100, "B", GRID, delta, {})
    assert hi.price == GRID.to_price(1100) + delta and hi.contract == "B"
    assert hi.sources[0].original_price == GRID.to_price(1100)
    assert hi.price_history[0][1] == "A" and hi.price_history[-1] == (BASE + 100, "B", hi.price)
    assert used.consumed and used.price == GRID.to_price(1010)            # not translated / reactivated
    assert ev.expired and ev.expiry_reason is ExpiryReason.ROLLOVER


def test_artificial_roll_gap_is_not_a_sweep():
    e, hi, lo, used = eng_with_levels()
    e.rollover(BASE + 100, "B", GRID, Decimal("50.00"), {})   # +200 ticks
    n = len(e.events)
    e.begin_session(plan([(BASE + 100, BASE + 110)]))
    feed(e, BASE + 100, [(1205, 1195, 1200)], contract="B")   # old-contract coordinates would show a 1100 BSL "sweep"
    assert len(e.events) == n and not hi.consumed


def test_unavailable_delta_quarantines_and_freezes():
    e, hi, lo, used = eng_with_levels()
    e.rollover(BASE + 100, "B", GRID, None, {})
    assert hi.quarantined and not hi.coordinates_valid and hi.contract == "A"
    e.begin_session(plan([(BASE + 100, BASE + 110)]))
    feed(e, BASE + 100, [(5000, 1, 1000)], contract="B")
    assert not hi.consumed and not lo.consumed     # frozen: no lifecycle updates on stale coordinates


def test_incompatible_grid_quarantined_never_rounded():
    e, hi, lo, used = eng_with_levels()
    e.rollover(BASE + 100, "B", TickGrid(Decimal("0.5")), Decimal("0"), {})
    # 1100 ticks * 0.25 = 275.00 (on 0.5 grid), 900*0.25 = 225.00 (on grid), 990*0.25=247.50 (on grid)
    assert not hi.quarantined
    e2, hi2, *_ = eng_with_levels()
    e2.rollover(BASE + 100, "B", TickGrid(Decimal("0.5")), Decimal("0.25"), {})
    assert hi2.quarantined and not hi2.coordinates_valid and hi2.price == GRID.to_price(1100)


# ------------------------------------------------------------------ driver end-to-end
def e2e(n_sessions=4, explicit=True, expA=None):
    cal = calendar(n_sessions)
    s = cal.sessions
    expA = expA if expA is not None else s[-1].end + 10
    cs = contracts(A=expA, B=s[-1].end + 10**6)
    i = inst(cs)
    st = BarStore()
    fill(st, "A", s[0], 1000, 10)
    fill(st, "B", s[0], 1040, 1)
    fill(st, "A", s[1], 1000, 1, last_close=1004)   # crossover session: B > A
    fill(st, "B", s[1], 1040, 10, last_close=1048)
    for k in range(2, n_sessions):
        fill(st, "A", s[k], 1000, 1)
        fill(st, "B", s[k], 1044, 10)
    pol = replace(ALL_POLICIES, initial_contract=InitialContract.EXPLICIT if explicit
                  else InitialContract.HIGHEST_VERIFIED_VOLUME)
    return run(i, cal, None, st, pol, s[0].session_id, s[-1].session_id,
               initial_contract="A" if explicit else None), cal


def test_rollover_at_next_session_open_after_crossover():
    res, cal = e2e()
    (d,) = res.decisions
    assert d.crossover_session_id == cal.sessions[1].session_id
    assert d.delta == GRID.to_price(1048) - GRID.to_price(1004)
    roll = [x for x in res.engine.log if x["event"] == "ROLLOVER"]
    assert roll[0]["minute"] == cal.sessions[2].start
    # session-1 low from A (1000) is still untouched and is translated by +44 ticks
    # (the session-1 high was consumed in session 2 by the 1004 print, so it is not translated)
    s1 = [r for r in res.engine.refs.values()
          if any(src.label is SourceLabel.EXCHANGE_SESSION and src.origin_id == cal.sessions[0].session_id
                 for src in r.sources)]
    hi = next(r for r in s1 if r.direction is BSL)
    lo = next(r for r in s1 if r.direction is SSL)
    assert hi.consumed and hi.contract == "A"
    assert lo.contract == "B" and lo.price == GRID.to_price(1000) + d.delta and lo.price_history[0][1] == "A"


def test_highest_verified_volume_initial_selection():
    res, cal = e2e(explicit=False)
    assert res.selection_log[0]["initial_contract"] == "A"
    assert res.engine.log[0]["minute"] == cal.sessions[1].start   # processing starts next session


def test_suspension_when_current_expires_without_successor():
    cal = calendar(3)
    s = cal.sessions
    cs = contracts(A=s[1].end, B=s[1].end + 10)   # B expires too early to ever be a successor after
    i = inst(cs)
    st = BarStore()
    for k in range(3):
        fill(st, "A", s[k], 1000, 10)
    res = run(i, cal, None, st, ALL_POLICIES, s[0].session_id, s[-1].session_id, initial_contract="A")
    assert res.engine.suspended
    assert any(d["kind"] == "PROCESSING_SUSPENDED" for d in res.engine.data_quality)


def test_unverified_or_mismatched_calendar_is_refused():
    cal = calendar(2)
    cal.verified = False
    i = inst(contracts(A=10**9))
    with pytest.raises(UnverifiedConfigurationError):
        run(i, cal, None, BarStore(), ALL_POLICIES, cal.sessions[0].session_id, cal.sessions[0].session_id,
            initial_contract="A")
    cal.verified = True
    cal.calendar_id = "OTHER"
    with pytest.raises(UnverifiedConfigurationError):
        run(i, cal, None, BarStore(), ALL_POLICIES, cal.sessions[0].session_id, cal.sessions[0].session_id,
            initial_contract="A")
