# Backtest Report — Strategy v0.2 (stop at E, 1R target) — **FAILED pre-registered test**

**Type:** historical backtest, simulated fills. **Pre-registration:** commit e4d714e (before any v0.2 result).
Same data, engine run and rules as v0.1, except the stop is at E and the target is 1R. Breakeven win rate: 50% before
costs.

## Deciding result: out-of-sample (entries 2025-01-01 → 2026-10-20), 1 tick slippage

| Commission (illustrative RT) | Trades | Win rate | Expectancy (R) | $/trade | PF | Total | Max DD | Losing streak |
|---|---|---|---|---|---|---|---|---|
| $0 | 1,791 | 42.0% | −0.177 | −2.94 | 0.87 | −$5,261.50 | −$5,734.50 | 16 |
| **$1.00 (criterion)** | 1,791 | 42.0% | **−0.226 (SE 0.024)** | −3.94 | 0.83 | −$7,052.50 | −$7,281.50 | 16 |
| $1.50 | 1,791 | 42.0% | −0.251 | −4.44 | 0.81 | −$7,948.00 | −$8,171.00 | 16 |

**Criterion (OOS expectancy > 0 after $1.00 RT): NOT MET.** The estimate is about 9 standard errors below zero.

## Design period (2022-10-14 → 2024-12-31)
2,063 trades, win rate 41.9%, −0.184R gross, −0.254R at $1.00 RT (SE 0.023). Also negative.

## Robustness (out-of-sample, $1.00 RT)
- **Slippage:** 0 ticks −0.205R; 2 ticks −0.249R.
- **Target fill on touch:** −0.224R.
- **Same-candle stop and target (186 OOS trades):** stop-first (rule) −0.226R. In the **best case** (all
  target-first) −0.012R (SE 0.024), still not positive. **The failure does not depend on intrabar order.**

## Comparison with v0.1 (full period, gross, 1 tick slippage)

| | v0.1 (2× E stop, 0.5R) | v0.2 (E stop, 1R) |
|---|---|---|
| Trades | 3,485 | 3,854 |
| Win rate vs breakeven | 61.8% vs 66.7% | 42.0% vs 50.0% |
| Expectancy | −0.079R | −0.181R |

Putting the stop at E stops out many more trades (2,237 stops vs 1,617 targets). Price trades back to the sweep
extreme often enough that a stop there is hit more than half the time.

## Status
v0.2 is rejected under its pre-registered rules. Per protocol, it must not be "fixed" by adjusting its
parameters on this data. Any further change is v0.3, defined before testing, and the 2025–2026 period has now been
used, so a v0.3 needs **new data (forward test)** or a different held-out period declared in advance.
