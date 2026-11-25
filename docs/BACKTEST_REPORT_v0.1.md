# Backtest Report — Strategy v0.1 (sweep reversal, 2× extreme stop, 0.5R target)

**Type:** historical backtest, simulated fills. Not forward-tested, not live.
**Data:** MNQ 1m (Databento GLBX.MDP3), 2022-10-14 → 2026-10-20, engine run validated 2026-11-25.
**Rules:** `docs/STRATEGY_SPEC.md` sections 1–7, all approved by the trader before the run. No parameters were
changed after seeing results.
**Size:** 1 contract; $0.50/tick, $2.00/point (from exchange definitions).
**Verification:** a random sample of 400 trades was re-derived from the raw bars by a separate script, with 0
mismatches.

## Headline (base: 1 tick slippage on entry/stop/time exit, gross of commission)

| Metric | Value |
|---|---|
| Eligible setups | 3,953 (464 ignored while in a position, 4 on NYSE-closed days) |
| Trades | **3,485** on 1,003 days |
| Win rate | **61.8%**. Breakeven for 1R risk / 0.5R target is **66.7%** before costs |
| Avg win / avg loss | +$35.02 / −$60.30 |
| Expectancy | **−0.079R, −$1.38 per trade** |
| Profit factor | 0.94 |
| Total | −$4,824.50 (−275.9R) |
| Max drawdown | −$9,541.50 |
| Longest losing streak | 11 |
| Average risk | 132.6 ticks (33.2 pts, ≈ $66) |
| Exits | 2,153 target, 1,325 stop, 1 gap stop, 6 time exit |

## Robustness — negative in every variant tested

| Variant | Trades | Win | Expectancy (R) | $/trade | PF |
|---|---|---|---|---|---|
| Base (1 tick slip) | 3,485 | 61.8% | −0.079 | −1.38 | 0.94 |
| 0 tick slippage | 3,498 | 62.2% | −0.067 | −0.83 | 0.96 |
| 2 tick slippage | 3,464 | 61.3% | −0.093 | −2.17 | 0.91 |
| Target fills on touch | 3,488 | 62.1% | −0.074 | −1.20 | 0.95 |
| + $0.50 RT commission (illustrative) | 3,485 | — | −0.094 | −1.88 | 0.92 |
| + $1.00 RT commission (illustrative) | 3,485 | — | −0.109 | −2.38 | 0.90 |
| + $1.50 RT commission (illustrative) | 3,485 | — | −0.124 | −2.88 | 0.88 |

**Same-candle ambiguity:** 157 trades hit stop and target inside one 1m candle. The approved rule assumes the stop
first. If **all** of them had hit the target first (best case), expectancy would be **−0.009R** (SE 0.012), still
not positive before commissions. With 1m bars the true value lies between −0.079R and −0.009R.

**Breakeven commission:** −$1.38 per round trip. The strategy loses before any commission.

## Breakdowns (descriptive; see the warning below)

- **By confirmation candle k:** k=0 (same-candle confirmation, 2,266 trades) −0.129R (SE 0.016). k=1–3 (1,219
  trades) **+0.013R (SE 0.020), indistinguishable from zero**.
- **By risk size:** risk under 32 ticks: 292 trades, all strongly negative (win rates 10–41%). Risk of 128+ ticks:
  1,316 trades, −0.002R.
- **By side:** long −0.061R, short −0.099R. **By entry half-hour:** all negative. **By year:** 2022–2025 negative,
  2026 (partial) −0.071R.
- **By liquidity label:** most subsets are negative. A few small subsets are positive (e.g. PRE_WINDOW only, 148
  trades; RTH+SWING_15M, 80 trades).

## Warning: do not pick the winning slices from this table
Sorting a 3,485-trade sample into dozens of buckets guarantees that some buckets look good by chance. None of
the positive slices above is evidence. Any of them (e.g. "skip k=0", "minimum risk", "pre-window levels only")
would be a **new strategy variation**. It should be written down as a hypothesis *before* testing, then tested on
data it was not discovered on (out-of-sample / walk-forward). Otherwise it is hindsight fitting.

## Conclusion
On this sample the approved rules produced **negative expectancy**. The result holds in every cost, slippage and
fill variant and in almost every subgroup. The 0.5R target needs >66.7% winners, and the setup delivered 61–62%.
This is a property of these rules over these four years. It is not a prediction, and it doesn't say which part of
the idea is wrong.
