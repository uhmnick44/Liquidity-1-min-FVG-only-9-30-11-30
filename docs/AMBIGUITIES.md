# Liquidity Engine — Ambiguity Register (spec v1.0)

Status: **OPEN. No engine code has been written.** Each item needs an explicit
decision (accept the recommendation, pick another option, or redefine) before
implementation. Recommendations are proposals, not rules. Decisions get recorded
here with a date and become part of spec v1.1.

Legend — **[BLOCKING]**: the engine can't be built correctly without it.
**[DEFAULTABLE]**: a recommendation exists that adds no new trading logic.

---

## A1. Same-timestamp formation vs. penetration ordering [BLOCKING — spec §15 requires explicit resolution]

**Finding:** For all four categories, a reference can't be penetrated (by ≥ δ)
between its formation and its availability, so ordering only matters at the
exact availability instant T.

- Session extreme: P = max/min over every bar in S, so no bar inside S exceeds P by δ.
- 15m swing: every 1-min bar inside candle i has H ≤ H(i), and every 1-min bar
  inside i+1 has H ≤ H(i+1) < H(i). The same holds for lows.
- This only holds if the 15m candles are built from the *same* 1-min data (see A11).

**Proposed processing order for each completed 1-min bar b (open time T₀, close time T₁):**

1. Test b only against references whose availability timestamp ≤ T₀.
2. Penetration detection → consume every penetrated reference → apply the dual-sided check (§8.1).
3. Create sweep events for the non-rejected penetrations, then test k = 0 confirmation on b's close.
4. Pending events from earlier bars test their confirmation on b's close (k = 1..3); events past k = 3 are rejected.
5. Events confirmed on *earlier* bars update their invalidation counter with b's close. An event confirmed on b does not count b (§10.3: candles must come after q).
6. Trading-window expiry if T₁ = 11:30 (after steps 3–5, so a confirmation on the 11:29 bar is recorded first; see A7).
7. Register references whose availability = T₁ (sessions closing at T₁, and swings whose candle i+1 closes at T₁). The first bar that can penetrate them opens at T₁.
8. Consolidation (A14) happens at registration time, against **active** references only.

## A2. Bar timestamp convention and input data [BLOCKING]

- Is each bar labeled by its open time or its close time? Vendors differ. Proposal: the engine requires open-time labels in UTC or tz-aware form, and converts to America/New_York with the IANA tz database.
- **What data do you have?** Vendor, file format, MNQ only or also NQ/ES/MES, per-contract files or a vendor-built continuous series, date range.

## A3. What counts as an "unexpected data gap" [BLOCKING]

Many vendors omit a 1-min bar when no trades print in that minute. That is a
real market condition, not a data fault, but it looks the same as a feed dropout.

- (a) Any missing minute during scheduled trading hours is a gap. This is strict, and overnight it can flag a lot.
- (b) A gap is ≥ N consecutive missing minutes. N is a new parameter and needs your authorization.
- (c) A gap is only what the vendor flags (if your vendor provides that).

Related: does the k ∈ {0..3} window count **bars received** or **clock minutes**? They are the same only if there are no missing minutes.
**Recommendation:** (a), with k counting clock minutes. Also measure how often missing minutes actually occur in your data before finalizing. The answer is a data question, not a preference.

## A4. Liquidity possibly consumed during missing data [BLOCKING — spec §13 requires authorization]

We can't know the high and low during a gap. The options:

1. **Ignore.** References stay active. Risk: a later "sweep" of a level that was already traded through inside the gap.
2. **Bridge inference.** If the interval between the last close before the gap and the first open after it crosses P ± δ, mark the reference consumed (reason `INFERRED_GAP`). Price could still have traded outside that interval, so this under-detects.
3. **Quarantine.** Mark references inside the bridge interval `UNRESOLVED`. They are excluded from sweep generation permanently, and this status is separate from `CONSUMED`.
4. **Supplemental data.** Use a coarser vendor bar covering the gap (its high and low are real prints, not synthetic candles) to decide consumption. This needs that data to exist.

Sub-question: should session extremes and 15m swings computed over windows that contain a gap be (i) registered with a data-quality flag, or (ii) not registered?

**Recommendation:** 4 where the data exists, otherwise 3, plus (i). None of the options recovers the truth; they only differ in which error you prefer.

## A5. Confirmation/invalidation windows across scheduled closures [BLOCKING]

Example: penetration on the 16:58 bar. Is the 18:00 bar "candle 2"? This also covers weekend and holiday halts. The spec says closures aren't gaps, but it doesn't say whether the windows span them.

- (a) Windows count bars across the closure.
- (b) A scheduled closure terminates pending confirmations (reason `SESSION_CLOSE`).

**Recommendation:** (b). This doesn't affect 09:30–11:30 trading on normal days, but it does affect recorded event statistics.

## A6. Invalidation/expiry scope for sweeps outside the trading window [BLOCKING]

Expiry (§11) is defined only for window-eligible setups. For ineligible confirmed sweeps:

- (a) Don't track invalidation (status N/A).
- (b) Track until the end of the CME session.
- (c) Track until invalidated (unbounded).

**Recommendation:** (a). It adds the least logic. Revisit if a future strategy uses a different window.

Also: should the 09:30–11:30 window be **configurable** (default 09:30–11:30), given the engine is meant to serve future strategies? Recommendation: yes. The window is an input; the rules stay unchanged.

## A7. Setups confirmed on the 11:29 bar [DEFAULTABLE]

A sweep confirmed at the 11:30 close is eligible (§11), but it expires at 11:30, and entries at/after 11:30 are prohibited. So it can never be traded.
**Recommendation:** record it as `CONFIRMED` then `EXPIRED` at the same timestamp, with an explicit flag. Please confirm this is intended rather than an oversight.

## A8. RTH and trading window on non-standard days [BLOCKING]

- On days CME trades but the NYSE is closed (some US holidays), does an RTH session, a trading window, or a pre-window exist?
- On early-close days, the NYSE and CME equity closes can differ. Does RTH end at 16:00, at the NYSE early close, or at the CME halt?

**Recommendation:** RTH, pre-window and trading window exist only on NYSE trading days. RTH ends at the earlier of 16:00 or the scheduled CME halt/close. Exact holiday times will come from a calendar source (candidate: `pandas_market_calendars` CME Equity and NYSE calendars), checked against official CME/NYSE holiday notices. I won't hard-code times from memory.

## A9. CME session on holiday-affected trade dates [BLOCKING]

Around some holidays, one CME trade date contains an intra-session halt or spans more than one calendar day.

- (a) Session = CME trade date (halts are scheduled closures inside one session).
- (b) Session = each contiguous trading segment.

**Recommendation:** (a).

## A10. Pre-window anchoring [DEFAULTABLE]

The pre-window runs from 18:00 on the prior evening to 09:30, and ends at the RTH open of the same trade date. It is tied to A8: no NYSE day means no pre-window.

## A11. 15-minute candle construction [BLOCKING]

- Build from the same 1-min data, aligned to :00/:15/:30/:45 ET? Recommendation: yes. This is required for A1's proof.
- Should the fractal span closures (i−1 = 16:45 bar, i = 18:00 bar; weekends)? (a) Continuous series; (b) reset at each CME session. The spec defines the fractal on consecutive candles and imposes no lookback, which suggests (a).
- Partial 15m bars (early close at a non-quarter time): include them with a flag, or exclude them?
- 15m bars containing a data gap (A3): recommend that they can't participate in a fractal (flagged).

## A12. Rollover method [BLOCKING]

- **The back-adjustment must be additive (difference), not ratio.** Ratio adjustment moves prices off the 0.25 grid, which breaks integer-tick comparisons (§15).
- **Causality:** a classic back-adjusted series is restated at every roll using later data. Differences within a segment are invariant, so the causal equivalent is: store references in current-contract ticks, and at each roll shift all active references by the roll offset known at that moment. Then the analytical price equals the actual price of the current contract, and history keeps the cumulative offsets. This satisfies §12 without lookahead.
- You need to specify: (1) the roll rule (a fixed calendar offset before expiry, or a volume crossover judged on the prior session's data); (2) the roll instant (recommend the CME session open at 18:00); (3) offset measurement (recommend the difference of simultaneous closes of the old and new contracts at the last bar before the roll). (3) requires data for both contracts on roll days.
- Confirmed setups expire at the roll (§12), so frozen extremes never need shifting.

## A13. Dataset start [DEFAULTABLE]

The first session in the data is usually partial, so its high/low is not the true session extreme.
**Recommendation:** register session liquidity only for sessions covered from their scheduled start. As §5 requires, the output will state that liquidity formed before the dataset begins is unknown.

## A14. Consolidation and timestamps [DEFAULTABLE]

- Merge only into **active** references. A new reference at a consumed price gets a new ID (§5).
- A merged reference keeps the earliest ID and a list of (source label, formation ts, availability ts). It produces one sweep event per penetration, no matter how many labels it carries.
- Formation timestamp = open time of the first 1-min bar that printed P inside the source window. If several bars tie, take the first one.

## A15. Dual-sided scope [DEFAULTABLE]

Rejection applies only to events *initiated* on the dual-sided bar, including every level on both sides. Events pending from earlier bars still evaluate that bar's close for confirmation, and confirmed events still count it for invalidation.

## A16. Engine/strategy boundary [DEFAULTABLE]

The engine can't know whether a trade was entered. Proposal: the engine computes invalidation and expiry for every eligible confirmed setup as if unexecuted. The strategy layer decides whether a post-entry invalidation is relevant to it. "Unexecuted" in §11 then becomes a strategy-layer interpretation.

## A17. Off-grid prices [DEFAULTABLE]

If an input price isn't an exact multiple of δ, raise a hard error. Never round silently.

## A18. Session-open gaps [DEFAULTABLE]

A real gap at 18:00 after the maintenance break, or at the Sunday open, is genuine trading. It penetrates and consumes levels under the normal rules. Only roll gaps (A12) get special treatment.

## A19. Implementation stack [DEFAULTABLE]

Python 3.11+, a pure-Python core (deterministic, no vectorized lookahead risk), pytest, and an optional exchange-calendar dependency. Outputs go to CSV/Parquet, plus an in-memory read-only API for the strategy layer.
