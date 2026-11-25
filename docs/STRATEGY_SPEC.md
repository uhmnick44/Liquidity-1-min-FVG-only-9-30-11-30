# Strategy Specification — DRAFT v0.1 (being defined with the trader)

Strategy layer on top of the validated liquidity engine (spec v1.2). It reads engine outputs only, and never
changes liquidity state (A16). **No code exists for it yet.** Code is written after every section below is
approved.

Status legend: **[APPROVED]** trader decision · **[OPEN]** not yet defined.

## 1. Setup selection — [APPROVED 2026-11-25]
1.1 **Qualifying sweeps:** any sweep event that is `CONFIRMED` and passes the trading-window eligibility layer
    (09:30–11:30 ET: penetration bar opens in the window, confirmation observable before 11:30, reference not
    quarantined, not dual-sided).
1.2 **Direction:** BSL sweep → **short**. SSL sweep → **long**.
1.3 **"Last sweep":** the qualifying sweep with the **latest confirmation** (confirmation candle closed most
    recently) is the active setup.
1.4 **Replacement:** a newer qualifying sweep **replaces** any older setup that has not been entered yet. The older
    setup is cancelled.
1.5 **Opposite directions:** the same replacement applies regardless of direction. For example, a later SSL sweep
    (long) cancels an earlier BSL setup (short) that is waiting for entry.
1.6 **Same-candle ties:** sweeps confirmed on the same candle and the same side form **one setup**. They share one
    confirmation candle and one frozen extreme E, and all their levels are recorded on it.
1.7 **In a position:** while a trade is open, new sweeps are ignored. Setup selection resumes after the position
    is closed.

## 2. 1-minute FVG — [REMOVED 2026-11-25]
The trader confirmed the FVG plays no part in this strategy's entry. (The repository name predates this.)

## 3. Entry trigger — [APPROVED 2026-11-25]
3.1 **Trigger:** enter when the active setup's sweep is confirmed, i.e. at the close of the confirmation candle q
    (a BSL setup closes back below the level → short; an SSL setup closes back above the level → long).
3.2 **Fill model (backtest):** a market order fills at the **open of the next 1-minute candle (q+1)**, plus
    **1 tick slippage** against the trade. Sensitivity is also reported at 0 and 2 ticks. Entry must occur before
    11:30 ET (A7).
3.3 **Late confirmations:** confirmation on any candle k = 0..3 of the engine's window is valid. Results are
    reported by k.
3.4 **Distance filter:** none in the base strategy. The entry-to-level distance is recorded on every trade, for a
    possible separately tested variation.
## 4. Stop loss — [APPROVED 2026-11-25]
4.1 **Distance:** stop distance = **2 × |fill price − E|**, where E is the setup's frozen sweep extreme (for a
    same-candle tie, the shared E). Long: stop = fill − 2 × (fill − E). Short: stop = fill + 2 × (E − fill).
    Computed in integer ticks; always on the 0.25 grid, never rounded.
4.2 **Reference price:** the actual entry fill (next-candle open + slippage).
4.3 **Invalid geometry:** if the fill is at or beyond E (long: fill ≤ E; short: fill ≥ E), the trade is
    **skipped** and logged with reason `FILL_AT_OR_BEYOND_EXTREME`.
4.4 **Execution:** a resting stop order. It fills on the first candle whose range touches the stop, with 1 tick of
    slippage against the trade (0 and 2 ticks reported as sensitivity). If a candle opens beyond the stop, the
    fill is that open (+ slippage).
4.5 **Maximum stop size:** none in the base strategy. The stop distance is recorded on every trade; dollar risk is
    handled by position sizing (section 6).
## 5. Take profit / exit — [APPROVED 2026-11-25]
5.1 **Target:** exactly **0.5R**, where R = stop distance. Because the stop is 2 × |fill − E|, the target is
    |fill − E| from the fill on the profit side. Integer ticks, always on grid.
5.2 **Target fill:** a resting limit order. It counts as filled only when a candle trades **at least 1 tick
    through** the target (long: high ≥ target + 1 tick; short: low ≤ target − 1 tick), at the target price.
    Touch-fills are reported as sensitivity.
5.3 **Same-candle ambiguity:** if one 1m candle reaches both the stop and the target-fill condition (including the
    entry candle q+1), the **stop is assumed first**. Occurrences are counted.
5.4 **Time exit:** a position still open at the RTH close (16:00 ET, or the reference early close, e.g. 13:00) is
    closed at the open of that candle + 1 tick slippage.
5.5 **Management:** none. No breakeven, partials or trailing stop.
5.6 **Note:** a 1R-risk / 0.5R-reward structure breaks even at a 66.7% win rate before costs. Results are
    reported by stop size and with/without costs.
## 6. Risk, sizing, limits, costs — [APPROVED 2026-11-25: trader adopted the recommendations]
6.1 **Size:** 1 MNQ contract per trade. Results are reported in ticks, points, R and USD per contract.
    Contract value: definitions give unit_of_measure_qty = 2 (IPNT, USD), so **$2.00 per index point, $0.50 per
    tick**. (The field min_price_increment_amount reads 0.005 in 1e-9 units, which does not match. Noted, not
    used.)
6.2 **Trades per day:** no maximum in the base test. Section 1.7 already allows only one position at a time.
6.3 **Daily loss limit:** none in the base test, so the raw rule set is measured. Daily P&L distribution, losing
    streaks and drawdown are reported. Daily limits can then be tested as separate variations. (A hard daily
    limit for live trading is a separate execution-discipline decision.)
6.4 **Commission:** the trader's actual rate is unknown. Results are reported **gross**, at **illustrative** round
    trips of $0.50 / $1.00 / $1.50 per contract (scenarios, not the trader's rate), and as the round-trip cost at
    which expectancy reaches zero.
6.5 **Prop-firm rules:** not modelled in the base test.

## 7. Additional decisions — [APPROVED 2026-11-25]
7.1 **S2:** no trades on trade dates when the reference market (NYSE) is closed (2025-07-04 and 2026-06-19 in
    this dataset; 4 setups skipped).
7.2 **S1** (opposite-direction setups confirmed on the same candle) never occurs in 2022-10 → 2026-10. The code
    raises an error if it ever does, so no assumption is made silently.

---

# Variation v0.2 — [APPROVED 2026-11-25, PRE-REGISTERED before any v0.2 result was seen]
Identical to v0.1 (sections 1–7) except:
- **4.1' Stop:** stop **at E** (stop multiple 1). Long: stop = E; short: stop = E. R = d = |fill − E|.
- **5.1' Target:** **1R**, i.e. target = fill ± d. If a future target multiple gives a fractional tick, the target
  is **rounded toward the entry** (trader decision 2a). With 1R it is always a whole number of ticks.
- Breakeven win rate before costs: 50%.

**Evaluation protocol (pre-registered):**
- **Design period (in-sample):** entries from 2022-10-14 through 2024-12-31.
- **Test period (out-of-sample, deciding):** entries from 2025-01-01 through 2026-10-20.
- **Pass criterion:** out-of-sample expectancy **> 0 after base slippage (1 tick) and $1.00 round-trip
  commission** (illustrative). Results at $0.50 and $1.50 are also reported. A positive estimate within 2 standard
  errors of zero is reported as **inconclusive**, not as an edge.
- No parameter of v0.2 may be changed after the results are seen. Any change becomes v0.3, tested on new data.

---

# Cross-market test: MGC (micro gold) — [APPROVED 2026-11-25, PRE-REGISTERED before any gold result]
Same hypotheses, no rule changes. **v0.1 and v0.2 exactly as specified above**, run on MGC.
- **Data:** Databento GLBX.MDP3 MGC.FUT (job GLBX-20261122-FLQH67SQMY ohlcv-1m, plus definition/statistics/status).
- **Test period:** entries **2020-01-01 → 2026-11-20**. 2016–2019 is excluded on data quality alone: the front
  contract is missing 1.8–17.5% of 09:30–11:30 ET minutes then, versus ≤0.4% from 2020 on. This was decided before
  any strategy result.
- **Roll cycle (I3):** G, J, M, Q, Z. V (Oct) is excluded: it is never the highest-volume contract in the data,
  and its thin volume would block rollover decisions.
- **Reference market (RTH / pre-window / time exit):** NYSE hours and holidays, identical to MNQ.
- **Trading window:** 09:30–11:30 ET.
- **Tick size and USD value:** from the MGC definitions, never assumed.
- **Evaluation:** the whole test period is out-of-sample for these fixed rules. **Pass = expectancy > 0 after
  1 tick slippage and $1.00 round-trip commission (illustrative), more than 2 standard errors above zero.**
  Results are also shown by year. A result within 2 SE is inconclusive.
- A pass would be a lead for forward testing, not proof of an edge.

---

# Variation v0.3 — breakeven at +0.5R, target 1R — [APPROVED 2026-11-25, PRE-REGISTERED before any v0.3 result]
Identical to v0.1 (entry, stop at 2 × |fill − E|, all selection/execution rules) except:
- **Target:** 1R (target = fill ± R, R = stop distance), filled when price trades 1 tick through.
- **Breakeven:** when a candle trades **1 tick through +0.5R** (long: high ≥ entry + ⌈0.5R⌉ + 1 tick), the stop
  moves to the **entry price**, effective from the **next 1-minute candle**. With 1m bars the trigger is known only at
  that candle's close, so no intrabar order is assumed. A breakeven stop fills at the entry price with 1 tick of
  slippage (`BREAKEVEN_STOP`).
- Same-candle stop/target ambiguity: stop assumed first. Time exit at the NYSE close. Unchanged.
- Breakeven condition before costs: P(reach 1R | reached +0.5R) must exceed ≈ P(no +0.5R) / P(+0.5R).

**Evaluation (pre-registered):**
- **Gold (MGC), 2020-01-01 → 2026-11-20:** the deciding, out-of-sample test (data unseen when v0.3 was proposed).
  Pass = expectancy > 0 after 1 tick slippage and $1.00 round trip, more than 2 standard errors above zero.
- **MNQ 2022–2026:** reported for comparison but **in-sample**. v0.3 was proposed after seeing MNQ v0.1 results, so
  MNQ cannot confirm it.
