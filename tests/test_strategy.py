"""Strategy spec v0.1 rules (docs/STRATEGY_SPEC.md)."""
from datetime import datetime, timezone

import pytest

from strategy.backtest import TICK, Bar, Market, Params, build_setups, simulate

SID = "2025-03-04"


def et(h, mi):  # 2025-03-04 is EST (UTC-5)
    return int(datetime(2025, 3, 4, h + 5, mi, tzinfo=timezone.utc).timestamp()) // 60


def iso(m):
    return datetime.fromtimestamp(m * 60, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def px(t):
    return str(t * TICK)


def ev(eid, direction, level, extreme, pen, conf, **kw):
    d = {"event_id": eid, "ref_id": "L" + eid, "status": "CONFIRMED", "dual_sided": False,
         "reference_quarantined_at_penetration": False, "penetration_ts": iso(pen), "confirmation_ts": iso(conf),
         "direction": direction, "sweep_extreme": px(extreme), "level_price": px(level), "contract": "A",
         "source_labels": ["SWING_15M"]}
    d.update(kw)
    return d


def market(bars, rth_close=et(16, 0), sid=SID):
    return Market({"A": bars}, [(et(0, 0) - 360, et(17, 0), sid)], {sid: rth_close})


def flat(start, n, price, spread=2):
    return [Bar(start + i, price, price + spread, price - spread, price) for i in range(n)]


P = Params(trade_reference_closed_days=False, opposite_same_candle="SKIP")


def run(events, bars, p=P, **mk):
    mkt = market(bars, **mk)
    return simulate(build_setups(events, mkt, p, []), mkt, p)


# ---------------------------------------------------------------- entry / stop / target geometry
def test_long_geometry_next_open_plus_slippage():
    q = et(10, 0)
    bars = [Bar(q, 1000, 1001, 995, 1000), Bar(q + 1, 1004, 1005, 1003, 1004)] + flat(q + 2, 600, 1004, 1)
    trades, _ = run([ev("1", "SSL", 999, 990, q, q)], bars)
    (t,) = trades
    assert t.side == "LONG" and t.entry == 1005              # open 1004 + 1 tick
    assert t.dist_e == 15 and t.stop == 1005 - 30 and t.target == 1005 + 15
    assert t.risk == 30 and t.entry_minute == q + 1


def test_short_geometry():
    q = et(10, 0)
    bars = [Bar(q, 1000, 1005, 999, 1000), Bar(q + 1, 996, 997, 995, 996)] + flat(q + 2, 600, 996, 1)
    trades, _ = run([ev("1", "BSL", 1001, 1010, q, q)], bars)
    (t,) = trades
    assert t.side == "SHORT" and t.entry == 995 and t.stop == 995 + 30 and t.target == 995 - 15


def test_fill_beyond_extreme_skipped():
    q = et(10, 0)
    bars = [Bar(q, 1000, 1001, 995, 1000), Bar(q + 1, 989, 990, 985, 989)] + flat(q + 2, 10, 989)
    trades, skipped = run([ev("1", "SSL", 999, 990, q, q)], bars)
    assert not trades and skipped[0][1] == "FILL_AT_OR_BEYOND_EXTREME"


# ---------------------------------------------------------------- exits
def test_target_needs_one_tick_through():
    q = et(10, 0)
    bars = [Bar(q, 1000, 1001, 995, 1000), Bar(q + 1, 1004, 1005, 1003, 1004),
            Bar(q + 2, 1010, 1020, 1009, 1015),           # touches target 1020 exactly: no fill
            Bar(q + 3, 1015, 1021, 1012, 1018)]           # trades through: fill at target
    bars += flat(q + 4, 300, 1018, 1)
    (t,), _ = run([ev("1", "SSL", 999, 990, q, q)], bars)
    assert t.reason == "TARGET" and t.exit_minute == q + 3 and t.exit == 1020 and t.r == 0.5


def test_stop_with_slippage_and_r():
    q = et(10, 0)
    bars = [Bar(q, 1000, 1001, 995, 1000), Bar(q + 1, 1004, 1005, 1003, 1004), Bar(q + 2, 1000, 1001, 970, 980)]
    (t,), _ = run([ev("1", "SSL", 999, 990, q, q)], bars + flat(q + 3, 5, 980))
    assert t.reason == "STOP" and t.exit == 975 - 1 and t.pnl_ticks == -31


def test_same_bar_stop_and_target_assumes_stop():
    q = et(10, 0)
    bars = [Bar(q, 1000, 1001, 995, 1000), Bar(q + 1, 1004, 1005, 1003, 1004), Bar(q + 2, 1004, 1030, 960, 1000)]
    (t,), _ = run([ev("1", "SSL", 999, 990, q, q)], bars + flat(q + 3, 5, 1000))
    assert t.reason == "STOP" and t.ambiguous_bar


def test_gap_through_stop_fills_at_open():
    q = et(10, 0)
    bars = [Bar(q, 1000, 1001, 995, 1000), Bar(q + 1, 1004, 1005, 1003, 1004), Bar(q + 5, 960, 962, 955, 958)]
    (t,), _ = run([ev("1", "SSL", 999, 990, q, q)], bars + flat(q + 6, 5, 958))
    assert t.reason == "STOP_GAP" and t.exit == 959


def test_time_exit_at_rth_close():
    q = et(10, 0)
    bars = [Bar(q, 1000, 1001, 995, 1000)] + flat(q + 1, et(16, 30) - q - 1, 1006, 1)
    (t,), _ = run([ev("1", "SSL", 999, 990, q, q)], bars)
    assert t.reason == "TIME_EXIT_RTH_CLOSE" and t.exit_minute == et(16, 0) and t.exit == 1005


def test_early_close_time_exit():
    q = et(10, 0)
    bars = [Bar(q, 1000, 1001, 995, 1000)] + flat(q + 1, et(14, 0) - q - 1, 1006, 1)
    (t,), _ = run([ev("1", "SSL", 999, 990, q, q)], bars, rth_close=et(13, 0))
    assert t.exit_minute == et(13, 0)


# ---------------------------------------------------------------- selection rules
def test_in_position_new_setups_ignored():
    q = et(10, 0)
    bars = [Bar(q, 1000, 1001, 995, 1000)] + flat(q + 1, 30, 1006, 1) + flat(q + 31, 300, 1006, 30)
    evs = [ev("1", "SSL", 999, 990, q, q), ev("2", "SSL", 999, 990, q + 5, q + 5)]
    trades, skipped = run(evs, bars)
    assert len(trades) == 1 and ("IN_POSITION" in [s[1] for s in skipped])


def test_setup_after_exit_is_taken():
    q = et(10, 0)
    bars = [Bar(q, 1000, 1001, 995, 1000), Bar(q + 1, 1004, 1005, 1003, 1004), Bar(q + 2, 1000, 1001, 970, 980),
            Bar(q + 3, 980, 981, 979, 980)] + flat(q + 4, 400, 985, 1)
    evs = [ev("1", "SSL", 999, 990, q, q), ev("2", "SSL", 975, 970, q + 2, q + 2)]  # confirmed on the stop-out candle
    trades, _ = run(evs, bars)
    assert len(trades) == 2 and trades[1].setup.event_ids == ["2"]


def test_same_candle_same_side_is_one_setup():
    q = et(10, 0)
    bars = [Bar(q, 1000, 1001, 995, 1000)] + flat(q + 1, 400, 1006, 1)
    evs = [ev("1", "SSL", 999, 990, q, q), ev("2", "SSL", 997, 990, q, q, source_labels=["RTH"])]
    trades, _ = run(evs, bars)
    (t,) = trades
    assert t.setup.event_ids == ["1", "2"] and t.setup.labels == ["RTH", "SWING_15M"]


def test_opposite_same_candle_requires_decision():
    q = et(10, 0)
    bars = [Bar(q, 1000, 1001, 995, 1000)] + flat(q + 1, 50, 1000)
    evs = [ev("1", "SSL", 999, 990, q - 2, q), ev("2", "BSL", 1003, 1010, q - 1, q)]
    with pytest.raises(RuntimeError):
        run(evs, bars, p=Params(trade_reference_closed_days=False))
    _, skipped = run(evs, bars)
    assert all(r == "OPPOSITE_SETUPS_SAME_CANDLE" for _, r in skipped)


# ---------------------------------------------------------------- window rules (A7, 3.2)
def test_window_rules():
    bars = flat(et(9, 0), 400, 1000)
    pre = run([ev("1", "SSL", 999, 990, et(9, 29), et(9, 31))], bars)[0]
    late = run([ev("1", "SSL", 999, 990, et(11, 29), et(11, 29))], bars)[0]
    ok = run([ev("1", "SSL", 999, 990, et(11, 28), et(11, 28))], flat(et(9, 0), 500, 1000))[0]
    assert not pre and not late and len(ok) == 1 and ok[0].entry_minute == et(11, 29)


def test_reference_closed_day_requires_decision():
    q = et(10, 0)
    bars = [Bar(q, 1000, 1001, 995, 1000)] + flat(q + 1, 50, 1006, 1)
    with pytest.raises(RuntimeError):
        run([ev("1", "SSL", 999, 990, q, q)], bars, p=Params(opposite_same_candle="SKIP"), rth_close=None)
