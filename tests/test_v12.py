"""Spec v1.2 decisions: N9 resume, N13b/N18b statistics, N22b notice-verified halts, APPROVED_V12."""
from dataclasses import fields
from decimal import Decimal

import pytest

from liqengine.calendars import ExchangeCalendar, ExchangeSession
from liqengine.data import Bar, BarStore, DataIntegrityError, MinuteEvidence, MinuteStatus
from liqengine.driver import run
from liqengine.models import CoarseBar, RejectReason, SweepStatus
from liqengine.policies import APPROVED_V12
from liqengine.statistics import convert
from tests.helpers import ALL_POLICIES, BASE, BSL, C, GRID, SSL, TICK, engine, feed, plan, seed, started
from tests.test_rollover import calendar, contracts, fill, inst

P = 1000
NS = 60 * 10**9


def test_approved_v12_sets_every_policy():
    assert all(getattr(APPROVED_V12, f.name) is not None for f in fields(APPROVED_V12))


# ------------------------------------------------------------------ N22(b)
def test_notice_verified_halt_acts_as_closure_without_quarantine():
    eng = started(APPROVED_V12)
    far = seed(eng, BSL, P + 100, BASE)
    r = seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 2, P - 1, P + 1)])                      # pending confirmation
    eng.process_minute(BASE + 1, MinuteStatus.VERIFIED_HALT, None)
    eng.process_minute(BASE + 2, MinuteStatus.VERIFIED_HALT, None)
    e = eng.events["S0000001"]
    assert e.status is SweepStatus.REJECTED and e.reject_reason is RejectReason.UNSCHEDULED_HALT
    assert not far.quarantined and r.consumed
    assert any(d["kind"] == "UNSCHEDULED_HALT_NOTICE_VERIFIED" for d in eng.data_quality)


def test_halt_resets_invalidation_counter_under_v12():
    eng = started(APPROVED_V12)
    seed(eng, BSL, P, BASE)
    feed(eng, BASE, [(P + 4, P - 3, P - 1), (P + 6, P + 4, P + 5), (P + 6, P + 4, P + 5)])
    eng.process_minute(BASE + 3, MinuteStatus.VERIFIED_HALT, None)
    feed(eng, BASE + 4, [(P + 6, P + 4, P + 5)])
    e = eng.events["S0000001"]
    assert not e.invalidated and e.invalidation_count == 1


def test_bar_inside_verified_halt_is_a_data_conflict():
    st = BarStore()
    st.add(Bar(BASE, 1, 1, 1, 1, 1, C))
    with pytest.raises(DataIntegrityError):
        st.classify(C, BASE, MinuteEvidence(verified_halts={BASE}))


def test_without_notice_the_same_gap_is_missing_data():
    st = BarStore()
    assert st.classify(C, BASE, MinuteEvidence()) is MinuteStatus.UNCERTAIN


# ------------------------------------------------------------------ N9(b)
def test_resume_after_suspension_on_highest_verified_volume_successor():
    cal = calendar(4)
    s = cal.sessions
    cs = contracts(A=s[1].end, B=s[2].end + 10**6)    # A expires; B was never compared (no crossover)
    i = inst(cs)
    st = BarStore()
    fill(st, "A", s[0], 1000, 10)
    fill(st, "A", s[1], 1000, 10)
    for k in range(4):
        fill(st, "B", s[k], 1040, 1 if k < 2 else 20)  # B never exceeds A while A is alive
    res = run(i, cal, None, st, APPROVED_V12.__class__(**{**APPROVED_V12.__dict__,
              "initial_contract": ALL_POLICIES.initial_contract}),
              s[0].session_id, s[-1].session_id, initial_contract="A")
    eng = res.engine
    resumed = [x for x in eng.log if x["event"] == "PROCESSING_RESUMED"]
    assert resumed and resumed[0]["minute"] == s[3].start and resumed[0]["new"] == "B"
    assert eng.contract == "B" and not eng.suspended
    old = [r for r in eng.refs.values() if r.price_history[0][1] == "A" and not r.consumed]
    assert old and all(r.quarantined and not r.coordinates_valid for r in old)


# ------------------------------------------------------------------ N18(b) one-sided coarse
def test_one_sided_running_high_reinstates_bsl_only():
    eng = started(APPROVED_V12)
    b = seed(eng, BSL, P, BASE)
    s_ = seed(eng, SSL, P - 50, BASE)
    feed(eng, BASE, [(P - 5, P - 9, P - 6), "U", (P - 5, P - 9, P - 6)])
    eng.apply_coarse_evidence(CoarseBar(C, BASE, BASE + 3, GRID.to_price(P - 5), None, BASE + 10,
                                        "venue-high", attribution_end_minute=BASE + 4))
    assert not b.quarantined and s_.quarantined


def test_running_high_stamped_mid_minute_not_misattributed():
    eng = started(APPROVED_V12)
    r = seed(eng, BSL, P, BASE)
    feed(eng, BASE, ["U", (P - 5, P - 9, P - 6), (P + 3, P - 9, P - 6)])   # minute BASE+2 printed P+3
    # running high P+3 stamped inside minute BASE+2: bounds [BASE, BASE+2), printed within [BASE, BASE+3)
    eng.apply_coarse_evidence(CoarseBar(C, BASE, BASE + 2, GRID.to_price(P + 3), None, BASE + 10,
                                        "venue-high", attribution_end_minute=BASE + 3))
    assert r.consumed and r.consumption_kind.value == "OBSERVED_PENETRATION"   # not re-blamed on the gap
    assert not any(e.reject_reason is RejectReason.PENETRATED_DURING_MISSING_DATA for e in eng.events.values())


# ------------------------------------------------------------------ N13(b) statistics conversion
def stat(st, iid, recv, ev=None, price=0, qty=0, ref=None, action=1):
    return {"stat_type": st, "instrument_id": iid, "ts_recv": recv, "ts_event": ev or recv,
            "price": price, "quantity": qty, "ts_ref": ref, "update_action": action}


def cal2():
    return ExchangeCalendar("X", "1", True, "", [ExchangeSession("2025-03-04", ((BASE, BASE + 60),)),
                                                 ExchangeSession("2025-03-05", ((BASE + 180, BASE + 240),))])


def test_cleared_volume_maps_to_last_ended_session_and_is_causal():
    i = inst(contracts(A=10**9))
    recs = [stat(6, 1, (BASE + 190) * NS + 5, qty=1234),
            stat(6, 1, (BASE + 200) * NS, qty=1300),             # later revision: reported, not used
            stat(6, 1, (BASE + 210) * NS, qty=99, action=2)]     # delete: reported, not applied
    vols, coarse, rep = convert(recs, lambda iid, d: "A", cal2(), i)
    v = vols[("A", "2025-03-04")]
    assert v.volume == 1234 and v.available_minute == BASE + 191
    assert rep.revised_volume and rep.deletes_ignored == 1


def test_cleared_volume_ts_ref_mismatch_not_used():
    i = inst(contracts(A=10**9))
    wrong = 1700000000 * 10**9
    vols, _, rep = convert([stat(6, 1, (BASE + 190) * NS, qty=5, ref=wrong)], lambda *_: "A", cal2(), i)
    assert not vols and rep.ts_ref_mismatch


def test_session_high_records_become_one_sided_coarse_bars():
    i = inst(contracts(A=10**9))
    px = int(GRID.to_price(P) * 10**9)
    recs = [stat(5, 1, (BASE + 20) * NS + 7, ev=(BASE + 20) * NS + 3, price=px),   # running, mid-session
            stat(4, 1, (BASE + 70) * NS, ev=(BASE + 65) * NS, price=px)]           # after session end
    _, coarse, rep = convert(recs, lambda *_: "A", cal2(), i)
    hi, lo = coarse
    assert hi.high == GRID.to_price(P) and hi.low is None and (hi.end_minute, hi.attribution_end) == (BASE + 20, BASE + 21)
    assert lo.low == GRID.to_price(P) and (lo.end_minute, lo.attribution_end) == (BASE + 60, BASE + 60)
    assert hi.available_minute == BASE + 21 and rep.used_extremes == 2


def test_per_contract_halt_evidence():
    st = BarStore()
    ev = MinuteEvidence(verified_halts={C: {BASE}})
    assert st.classify(C, BASE, ev) is MinuteStatus.VERIFIED_HALT
    assert st.classify("OTHER", BASE, ev) is MinuteStatus.UNCERTAIN


def test_n32_cleared_volume_below_bar_volume_rejected_then_revision_used():
    i = inst(contracts(A=10**9))
    recs = [stat(6, 1, (BASE + 190) * NS, qty=0), stat(6, 1, (BASE + 200) * NS, qty=500)]
    vols, _, rep = convert(recs, lambda *_: "A", cal2(), i, bar_volume=lambda sym, s: 400)
    v = vols[("A", "2025-03-04")]
    assert v.volume == 500 and v.available_minute == BASE + 201
    assert rep.rejected_below_bar_volume[0]["cleared"] == 0
