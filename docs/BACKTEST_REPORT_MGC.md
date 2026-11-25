# Cross-market test — MGC (micro gold), v0.1 / v0.2 / v0.3 — **ALL FAIL**

**Type:** historical backtest, simulated fills. **Pre-registration:** commits 5267ace (v0.1/v0.2 on MGC) and 166535f
(v0.3), both before any gold result. No rule was changed after seeing results.
**Data:** MGC 1m, entries 2020-01-01 → 2026-11-20. The whole period is out-of-sample for these fixed rules.
**Contract value:** $1.00/tick, $10/point (definitions). **Commission:** $1.00 round trip (illustrative).

## Engine validation on gold (before any backtest)
- Calendars and overrides signed off by the trader (checklist sections A–E).
- Engine: 1,735 sessions, 34 rollovers (all with a measured adjustment, rules N34/N35), no suspension.
- Independent re-derivation from raw bars (`tools/validate_run.py`): **0 mismatches**. Checked: 60,164 events,
  33,325 invalidations, 60,164 consumptions, 55,283 swings, 4,644 complete session/RTH/pre-window extremes.
  5,746 windows with missing minutes are quarantined per N3.
- Determinism: second run in progress at time of writing (see the addendum when complete).

## Results (1 tick slippage, $1.00 RT commission)

| Variant | Trades | Win rate | Breakeven win rate | Expectancy (R) | SE | $/trade | PF | Total | Max DD | Worst streak |
|---|---|---|---|---|---|---|---|---|---|---|
| v0.1 (2×E stop, 0.5R) | 3,200 | 60.1% | 66.7% | **−0.161** | 0.014 | −3.33 | 0.82 | −$10,664 | −$12,845 | 9 |
| v0.2 (E stop, 1R) | 3,401 | 43.0% | 50.0% | **−0.286** | 0.018 | −2.76 | 0.80 | −$9,376 | −$10,464 | 23 |
| v0.3 (2×E stop, BE at +0.5R, 1R) | 3,114 | 32.6%* | — | **−0.153** | 0.016 | −3.58 | 0.81 | −$11,136 | −$12,908 | 19 |

*Breakeven exits fill at entry minus 1 tick of slippage and count as losses.

**Pass criterion (> 0, beyond 2 SE): NOT MET by any variant.** Each is about 10–16 SE below zero. All three are
negative in **every year 2020–2026**, with 0 ticks of slippage, and with touch-fill targets.

### Why v0.3 fails
Once price reached +0.5R (60.3% of trades), it went on to +1R only **53.2%** of the time. Breaking even before
costs needed **65.8%**. MNQ (in-sample) shows the same thing: 51.2% achieved vs 61.9% needed.

## MNQ under the same final rules (spec v1.2 + N34/N35; start 2022-10-05)

| Variant | Full period gross (R) | 2025+ at $1 RT (R, SE) |
|---|---|---|
| v0.1 | −0.079 | −0.106 (0.019) |
| v0.2 | −0.181 | −0.226 (0.024) |
| v0.3 (in-sample, not evidence) | −0.086 | −0.115 (0.022) |

## Conclusion
Across two markets, 7 years of gold and 4 years of MNQ, the setup "fade every confirmed liquidity sweep in
09:30–11:30 ET, entering at the next open" has **negative expectancy under every stop and target structure tested**.
Changing exits has not fixed it. The entry premise itself shows no edge in this data. Results are descriptive of
these rules over these samples; they do not say a different, independently motivated idea would fail.
