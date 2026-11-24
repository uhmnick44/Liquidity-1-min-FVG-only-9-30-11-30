"""A6/A7/A16 eligibility, strategy independence, determinism, data ingestion (A2/I8), audit."""
import json
from datetime import datetime, time, timezone
from decimal import Decimal

import pytest

from liqengine.audit import audit
from liqengine.calendars import ExchangeCalendar, ExchangeSession
from liqengine.data import BarStore, DataIntegrityError, MinuteEvidence, MinuteStatus, load_databento_csv
from liqengine.eligibility import CURRENT_STRATEGY_WINDOW, TradingWindow, evaluate_setup
from liqengine.export import write_outputs
from liqengine.instrument import Contract, InstrumentConfig
from liqengine.ticks import OffGridError, TickGrid, fixed_point_to_decimal
from liqengine.timeutil import dt_to_minute
from tests.helpers import BSL, C, engine, feed, plan, seed

P = 1000


def et(h, mi, day=4):   # March 2025 is EST until the 9th: ET = UTC-5
    return dt_to_minute(datetime(2025, 3, day, h + 5, mi, tzinfo=timezone.utc))


def day_calendar():
    return ExchangeCalendar("X", "1", True, "", [ExchangeSession("2025-03-04", ((et(0, 0), et(17, 0)),))])


def sweep_at(pen_minute, confirm_offset=0, extra=None):
    """Engine with a session around pen_minute; returns the sweep event."""
    eng = engine()
    eng.begin_session(plan([(pen_minute - 30, pen_minute + 120)]))
    feed(eng, pen_minute - 30, [(P - 2, P - 5, P - 3)] * 30)
    seed(eng, BSL, P, pen_minute)
    specs = [(P + 2, P - 1, P + 1)] * confirm_offset + [(P + 2, P - 3, P - 1)] + (extra or [])
    feed(eng, pen_minute, specs)
    return eng, eng.events["S0000001"]


W = CURRENT_STRATEGY_WINDOW


def test_penetration_before_window_is_ineligible():
    _, e = sweep_at(et(9, 29), confirm_offset=1)
    assert evaluate_setup(e, W, day_calendar()).reason == "PENETRATION_OUTSIDE_WINDOW"


def test_penetration_at_0930_eligible_and_active_from_confirmation_close():
    _, e = sweep_at(et(9, 30))
    v = evaluate_setup(e, W, day_calendar())
    assert v.eligible and v.active_from == et(9, 31) and v.active_until == et(11, 30)
    assert v.end_reason == "WINDOW_EXPIRED"


def test_confirmation_on_1128_bar_eligible_on_1129_bar_not():
    _, e1 = sweep_at(et(11, 28))
    assert evaluate_setup(e1, W, day_calendar()).eligible
    _, e2 = sweep_at(et(11, 29))
    v = evaluate_setup(e2, W, day_calendar())
    assert not v.eligible and v.reason == "CONFIRMATION_OBSERVABLE_AT_OR_AFTER_WINDOW_END"
    assert e2.status.value == "CONFIRMED"   # still recorded by the engine (A7)


def test_invalidation_ends_active_interval():
    _, e = sweep_at(et(10, 0), extra=[(P + 6, P + 4, P + 5)] * 3)
    v = evaluate_setup(e, W, day_calendar())
    assert v.eligible and v.active_until == et(10, 4) and v.end_reason.startswith("INVALIDATED")


def test_engine_state_independent_of_strategy_window():
    eng, e = sweep_at(et(10, 0), extra=[(P + 6, P + 4, P + 5)] * 3)
    before = json.dumps(eng.event_snapshots(), sort_keys=True) + json.dumps(eng.reference_snapshots(), sort_keys=True)
    for w in (W, TradingWindow(time(9, 45), time(10, 0), "America/New_York"), TradingWindow(time(3), time(4), "UTC")):
        evaluate_setup(e, w, day_calendar())
    after = json.dumps(eng.event_snapshots(), sort_keys=True) + json.dumps(eng.reference_snapshots(), sort_keys=True)
    assert before == after
    assert not evaluate_setup(e, TradingWindow(time(9, 45), time(10, 0), "America/New_York"), day_calendar()).eligible


def test_consumption_outside_window_stays_consumed_inside_window():
    eng = engine()
    eng.begin_session(plan([(et(9, 0), et(10, 0))]))
    r = seed(eng, BSL, P, et(9, 0))
    feed(eng, et(9, 0), [(P + 1, P - 5, P - 2)] + [(P - 2, P - 5, P - 3)] * 59)
    assert r.consumed and r.consumed_minute == et(9, 0)
    assert not evaluate_setup(eng.events["S0000001"], W, day_calendar()).eligible


def test_determinism_identical_inputs_identical_outputs(tmp_path):
    outs = []
    for k in range(2):
        eng, e = sweep_at(et(10, 0), extra=[(P + 6, P + 4, P + 5)] * 3 + ["U", "Z"])
        write_outputs(eng, tmp_path / str(k), {"run": "x"})
        outs.append({f.name: f.read_text() for f in sorted((tmp_path / str(k)).iterdir())})
    assert outs[0] == outs[1]


# ------------------------------------------------------------------ ticks
def test_tick_grid_exact_and_off_grid():
    g = TickGrid(Decimal("0.25"))
    assert g.to_ticks("21000.75") == 84003
    with pytest.raises(OffGridError):
        g.to_ticks("21000.10")
    with pytest.raises(TypeError):
        g.to_ticks(21000.75)
    assert fixed_point_to_decimal(21000750000000) == Decimal("21000.75")


# ------------------------------------------------------------------ Databento CSV
INST = InstrumentConfig(root="MNQ", has_expirations=True, tick_size=Decimal("0.25"), exchange_calendar_id="X",
                        reference_calendar_id=None, verified=True, contracts=[
                            Contract("MNQH5", 42, expiration=10**9, tick_size=Decimal("0.25"), in_expiration_cycle=True),
                            Contract("MNQH5-MNQM5", 43, expiration=10**9, tick_size=Decimal("0.25"),
                                     in_expiration_cycle=False, is_outright=False)])
HDR = "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume\n"
T0 = 1741098600 * 10**9   # 2025-03-04T14:30:00Z


def write(tmp_path, rows):
    p = tmp_path / "x.csv"
    p.write_text(HDR + "".join(rows))
    return p


def row(ts, iid=42, o="21000.00", h="21001.00", l="20999.00", c="21000.25", v=3, rtype=33):
    return f"{ts},{rtype},1,{iid},{o},{h},{l},{c},{v}\n"


def test_csv_pretty_and_fixed_point_prices(tmp_path):
    p = write(tmp_path, [row(T0), row(T0 + 60 * 10**9, o="21000250000000", h="21001000000000",
                                     l="20999000000000", c="21000500000000")])
    st, rep = load_databento_csv(p, INST)
    b0, b1 = st.get("MNQH5", T0 // 60 // 10**9), st.get("MNQH5", T0 // 60 // 10**9 + 1)
    assert b0.close == 84001 and b1.close == 84002 and rep.rows == 2


def test_csv_iso_timestamps(tmp_path):
    p = write(tmp_path, [row("2025-03-04T14:30:00.000000000Z".replace(".000000000", ""))])
    st, _ = load_databento_csv(p, INST)
    assert st.get("MNQH5", T0 // 60 // 10**9) is not None


@pytest.mark.parametrize("rows,msg", [
    ([row(T0), row(T0)], "duplicate"),
    ([row(T0, c="21000.10")], "grid"),
    ([row(T0 + 1)], "minute-aligned"),
    ([row(T0, rtype=34)], "rtype"),
    ([row(T0, h="20999.50")], "inconsistent"),
])
def test_csv_integrity_errors(tmp_path, rows, msg):
    with pytest.raises((DataIntegrityError, ValueError)) as ei:
        load_databento_csv(write(tmp_path, rows), INST)
    assert msg in str(ei.value)


def test_csv_out_of_order_reported_unknown_ids_and_spreads_excluded(tmp_path):
    p = write(tmp_path, [row(T0 + 60 * 10**9), row(T0), row(T0, iid=99), row(T0, iid=43)])
    st, rep = load_databento_csv(p, INST)
    assert rep.out_of_order == 1 and rep.unknown_instrument_ids == {99: 1}
    assert rep.rejected_non_outright == {"MNQH5-MNQM5": 1} and len(st.bars["MNQH5"]) == 2


def test_parquet_loader(tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    from liqengine.data import load_databento_parquet
    t = pa.table({"ts_event": [T0], "rtype": [33], "instrument_id": [42], "open": [21000000000000],
                  "high": [21001000000000], "low": [20999000000000], "close": [21000250000000], "volume": [3]})
    pq.write_table(t, tmp_path / "x.parquet")
    st, _ = load_databento_parquet(tmp_path / "x.parquet", INST)
    assert st.get("MNQH5", T0 // 60 // 10**9).close == 84001


def test_minute_classification_and_audit():
    st = BarStore()
    from liqengine.data import Bar
    m0 = T0 // 60 // 10**9
    for k in (0, 1, 5, 9):
        st.add(Bar(m0 + k, 1, 1, 1, 1, 1, "MNQH5"))
    st.add(Bar(m0 + 200, 1, 1, 1, 1, 1, "MNQH5"))  # outside schedule
    ev = MinuteEvidence({"MNQH5": {m0 + 2}}, {"MNQH5": {m0 + 3}}, source="test")
    assert st.classify("MNQH5", m0 + 2, ev) is MinuteStatus.VERIFIED_ZERO_TRADE
    assert st.classify("MNQH5", m0 + 3, ev) is MinuteStatus.CONFIRMED_MISSING
    assert st.classify("MNQH5", m0 + 4, ev) is MinuteStatus.UNCERTAIN
    assert st.classify("MNQH5", m0 + 4, None) is MinuteStatus.UNCERTAIN   # never assume zero trades
    cal = ExchangeCalendar("X", "1", True, "", [ExchangeSession("2025-03-04", ((m0, m0 + 10),))])
    rep = audit(st, cal, ev)["contracts"]["MNQH5"]
    assert rep["scheduled_minute_status"] == {"OBSERVED": 4, "VERIFIED_ZERO_TRADE": 1, "CONFIRMED_MISSING": 1,
                                              "UNCERTAIN": 4}
    assert rep["bars_outside_scheduled_hours"] == 1
    assert rep["non_observed_run_lengths"] == {"3": 2}
