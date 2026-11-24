# Quantitative Liquidity Engine — Master Specification (v1.0, as received 2026-11-24)

> Frozen copy of the specification as provided by the trader. Any change to a rule
> below requires explicit authorization and a new version number. Open questions
> are tracked in [`AMBIGUITIES.md`](AMBIGUITIES.md), not edited into this file.

## 1. Objective

Develop a standalone, deterministic liquidity engine that mathematically identifies, tracks, confirms, consumes, and invalidates liquidity sweeps.

The liquidity engine must remain independent of: entry conditions, stop-loss placement, take-profit placement, position sizing, risk management, trade execution. These strategy-specific parameters will be provided separately.

Do not introduce, modify, or optimize any liquidity rules without explicit authorization.

## 2. Mathematical Notation

- H(t) = candle high
- L(t) = candle low
- C(t) = candle close
- P = established liquidity price
- δ = instrument minimum tick size (0.25 points for MNQ)
- t = initial penetration candle index
- q = sweep confirmation candle index
- E = frozen sweep extreme

All calculations must be causal and use completed candles only.

## 3. Session Definitions

All times are Eastern Time, accounting for daylight saving time.

- CME session: 18:00 previous evening–17:00 current trading day.
- Maintenance break: 17:00–18:00.
- RTH session: 09:30–16:00.
- Pre-window session: 18:00 previous evening–09:30.
- Trading window: 09:30–11:30.

Respect actual exchange trading calendars, including holidays and shortened sessions.

## 4. Liquidity Identification

### 4.1 Eligible Liquidity Categories

1. Historical CME session highs and lows.
2. Historical RTH session highs and lows.
3. Historical pre-window highs and lows.
4. Confirmed 15-minute swing highs and lows.

Do not include 1-minute swing highs/lows or additional liquidity categories.

### 4.2 Session Liquidity

For every completed session S:

- Buyside liquidity: P = max(H(i)), for all candles i belonging to S.
- Sellside liquidity: P = min(L(i)), for all candles i belonging to S.

Session liquidity becomes eligible only after the respective session closes. Developing session highs and lows are not eligible.

### 4.3 15-Minute Swing Liquidity

Three-candle fractal (one left, one middle, one right candle).

- BSL: H(i) > H(i−1) AND H(i) > H(i+1); P = H(i)
- SSL: L(i) < L(i−1) AND L(i) < L(i+1); P = L(i)

The swing becomes eligible only after candle i+1 closes. Equal highs and equal lows do not qualify under this fractal definition. Retrospective sweep detection is prohibited.

## 5. Historical Liquidity Tracking

- Process all available historical data chronologically.
- All established liquidity remains active indefinitely until consumed.
- Do not impose an arbitrary historical lookback period.
- Track liquidity formation and consumption throughout the entire CME trading session.
- Consolidate liquidity references sharing the exact same price and direction.
- Preserve all originating source labels when consolidating references.
- Distinct price levels must be tracked independently.
- A new, independently confirmed liquidity reference may form at a previously consumed price.
- Simply revisiting a consumed price does not establish new liquidity.

Disclose that liquidity formed before the available dataset begins cannot be identified.

## 6. Mathematical Sweep Definition

All sweep penetration and confirmation calculations use completed 1-minute candles.

### 6.1 BSL Sweep
- Initial penetration: H(t) ≥ P + δ
- Confirmation: C(t+k) < P, k ∈ {0, 1, 2, 3}
- The first candle satisfying the confirmation condition determines q.

### 6.2 SSL Sweep
- Initial penetration: L(t) ≤ P − δ
- Confirmation: C(t+k) > P, k ∈ {0, 1, 2, 3}
- The first candle satisfying the confirmation condition determines q.

### 6.3 Confirmation Period
- Candle 0: initial penetration candle; Candles 1–3: the three subsequent 1-minute candles.
- Same-candle penetration and confirmation are permitted.
- Only one qualifying close is required.
- If no qualifying close occurs within this period, reject the sweep.
- A close exactly equal to the liquidity price does not qualify.

## 7. Liquidity Consumption

An active liquidity reference is permanently consumed upon its first qualifying penetration (BSL: H(t) ≥ P + δ; SSL: L(t) ≤ P − δ), regardless of: successful confirmation, failed confirmation, trading-window eligibility, subsequent invalidation, or whether a trade is executed.

Consumption of a liquidity reference and confirmation of a sweep event are tracked separately. A consumed reference cannot be reactivated.

## 8. Dual-Sided and Multiple Sweeps

### 8.1 Dual-Sided
If the same 1-minute candle penetrates both active BSL and active SSL:
- Reject all sweep signals initiated by those penetrations.
- Permanently consume all penetrated liquidity references.
- Record the event as an ambiguous dual-sided sweep.
- Do not infer an unknown intrabar price sequence.

### 8.2 Multiple Same-Direction
If a single candle penetrates multiple distinct levels on the same side:
- Register, confirm, and invalidate each penetration independently.
- Consume every penetrated reference.
- Multiple confirmed sweeps do not automatically authorize multiple trades.

## 9. Sweep Extreme

- BSL: E = max(H(j)), t ≤ j ≤ q
- SSL: E = min(L(j)), t ≤ j ≤ q

Includes the penetration and confirmation candles. E is frozen after confirmation.

## 10. Sweep Invalidation (confirmed setups, before trade entry)

- BSL: invalidate when min(C(n−2), C(n−1), C(n)) > E
- SSL: invalidate when max(C(n−2), C(n−1), C(n)) < E

Constraints: all three candles after q; consecutive; counter resets to zero on any nonqualifying close (including a close exactly equal to E); wicks alone never invalidate; invalidation occurs on the third qualifying close; invalidated setups cannot be reactivated; invalidation does not restore consumed liquidity; opposing confirmed sweeps coexist and are evaluated independently.

## 11. Trading-Window Eligibility

Both initial penetration and confirmation must occur within 09:30–11:30 ET.
- First eligible candle opens 09:30; final eligible candle opens 11:29 and closes 11:30.
- Confirmation at exactly 11:30 is permitted.
- New trade entries at or after 11:30 are prohibited.
- Sweeps initiated outside the window are ineligible for trading.
- All unexecuted setups expire at 11:30. Expiration does not restore consumed liquidity.

Liquidity formation and consumption continue to be tracked outside the window.

## 12. Futures Contract Rollover

- Preserve historical liquidity across rollovers using the back-adjusted analytical price series.
- Compare within a consistent analytical coordinate system; translate to actual contract prices for execution.
- Preserve consumption states and source labels.
- Never interpret artificial inter-contract gaps as sweeps.
- Cancel pending sweep confirmations during rollovers; expire confirmed-but-unexecuted setups.
- All rollover processing must be causal.

## 13. Missing-Data Handling

On an unexpected data gap: reject affected pending confirmations; invalidate affected pending setups; record a data-quality issue; never synthesize candles; never assume missing candles satisfy any condition. Scheduled exchange closures are not data gaps.

Historical liquidity potentially consumed during missing data: **unresolved — policy requires authorization.**

## 14. Output

Per liquidity reference / sweep event, where applicable: reference ID, sweep event ID, instrument and contract, source labels, formation timestamp, availability timestamp, direction, price, penetration timestamp, confirmation timestamp, frozen extreme, consumption status, confirmation status, invalidation status, invalidation timestamp, consecutive invalidation-close count, trading-window eligibility, expiration status, rejection/expiration reason, data-quality flags.

The strategy engine must access these outputs without modifying the liquidity engine.

## 15. Implementation and Validation

Deterministic chronological processing; no lookahead; integer-tick comparisons; separate reference and sweep-event state; identical inputs → identical outputs; unit tests for every definition and boundary (successful/failed sweeps, same-candle confirmation, duplicate levels, dual-sided, historical liquidity, invalidation, missing data, session boundaries, rollovers). Identify ambiguities before proceeding; explicitly resolve same-timestamp formation/penetration ordering; no silent assumptions; no parameter optimization; no strategy performance tests until the engine passes validation.

Do not implement entries, stops, targets, or sizing until separately specified.
