"""Performance statistics for a list of trades (user preference §4: win rate, avg win/loss,
expectancy, profit factor, max drawdown, losing streaks, frequency, by time / setup / k / stop size)."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .backtest import USD_PER_TICK, local


def summarize(trades, commission_rt: Decimal = Decimal(0)):
    n = len(trades)
    if not n:
        return {"trades": 0}
    usd = [float(t.pnl_ticks * USD_PER_TICK - commission_rt) for t in trades]
    r = [u / float(t.risk * USD_PER_TICK) for u, t in zip(usd, trades)]
    wins = [x for x in usd if x > 0]
    losses = [x for x in usd if x <= 0]
    eq = peak = mdd = 0.0
    eq_r = peak_r = mdd_r = 0.0
    streak = worst = 0
    for u, rr in zip(usd, r):
        eq += u; peak = max(peak, eq); mdd = min(mdd, eq - peak)
        eq_r += rr; peak_r = max(peak_r, eq_r); mdd_r = min(mdd_r, eq_r - peak_r)
        streak = streak + 1 if u <= 0 else 0
        worst = max(worst, streak)
    days = {local(t.entry_minute).date() for t in trades}
    return {
        "trades": n, "win_rate": round(len(wins) / n, 4),
        "avg_win_usd": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss_usd": round(sum(losses) / len(losses), 2) if losses else None,
        "expectancy_usd": round(sum(usd) / n, 3), "expectancy_r": round(sum(r) / n, 4),
        "total_usd": round(sum(usd), 2), "total_r": round(sum(r), 2),
        "profit_factor": round(sum(wins) / -sum(losses), 3) if losses and sum(losses) < 0 else None,
        "max_drawdown_usd": round(mdd, 2), "max_drawdown_r": round(mdd_r, 2),
        "longest_losing_streak": worst, "trading_days_with_trades": len(days),
        "avg_risk_ticks": round(sum(t.risk for t in trades) / n, 2),
    }


def breakdown(trades, key, commission_rt=Decimal(0)):
    g = defaultdict(list)
    for t in trades:
        g[key(t)].append(t)
    return {str(k): summarize(v, commission_rt) for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))}


def risk_bucket(t):
    x = t.risk
    for lo, hi in ((0, 8), (8, 16), (16, 32), (32, 64), (64, 128), (128, 10**9)):
        if lo <= x < hi:
            return f"{lo:>3}-{hi if hi < 10**9 else 'inf'} ticks"


def half_hour(t):
    lt = local(t.entry_minute)
    return f"{lt.hour:02d}:{0 if lt.minute < 30 else 30:02d}"


def breakeven_commission(trades):
    """Round-trip commission (USD/contract) at which expectancy is zero."""
    if not trades:
        return None
    return round(sum(float(t.pnl_ticks * USD_PER_TICK) for t in trades) / len(trades), 3)
