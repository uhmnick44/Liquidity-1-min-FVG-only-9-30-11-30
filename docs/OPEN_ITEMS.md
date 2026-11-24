# Open Items Register — every unresolved ambiguity (as of 2026-11-24)

> **RESOLVED 2026-11-24:** every recommendation below was approved. The binding record is [`SPEC_v1.2.md`](SPEC_v1.2.md). Only the Section C data/verification gaps remain.

These are all the ambiguities left after spec v1.1 and the dataset audit, in one place.

- **Section A:** behaviour the spec does not determine. The engine raises `UnauthorizedPolicyError` instead of choosing, or has no rule at all.
- **Section B:** readings of your text that I implemented and need you to ratify.
- **Section C:** verification and data gaps. These are not rule questions, but they block a real run.

A "Recommendation" is my proposal, not a rule. Nothing becomes a rule until you approve it.

Reply format: `N4: RESET`, `N12: c`, `N1: ratified`, etc.

---

## A. Decisions required — behaviour not defined by the spec

| ID | Question | Options | Recommendation | Why it matters / evidence |
|---|---|---|---|---|
| **N3** | A session / RTH / pre-window high or low is computed over a window that contains missing or uncertain minutes. What happens to that level? | `QUARANTINE` (register it as ineligible; it can be reinstated by verified coarse evidence) · `DO_NOT_REGISTER` | `QUARANTINE`: it keeps the level when venue session high/low data can verify it later. | The true extreme may have printed in the missing minute. The audit found 6 such sessions in 4 years. |
| **N4** | Invalidation close-counter across a scheduled closure (maintenance break, weekend, holiday). | `CONTINUE` · `RESET` | `RESET`: consistent with A5 (closure ends confirmation) and A11.1 (fractals restart after closure). | Only affects confirmed sweeps still tracked at 17:00 ET. Never relevant inside 09:30–11:30. |
| **N5** | Invalidation counter across a *verified* zero-trade minute. The minute has no close. | `UNCHANGED` (skip the minute) · `RESET` (it breaks "consecutive") | `RESET`: consistent with I11, which counts confirmation by clock minutes, so a minute without a close is a break. | Currently moot: no zero-trade evidence source exists (N12). |
| **N6** | Pre-window on a day the reference market (NYSE) is closed. | `DO_NOT_GENERATE` · `GENERATE_TO_NOMINAL_TIME` (end at 09:30 ET anyway) | `DO_NOT_GENERATE`: the pre-window is defined as ending at the RTH open, and A8 suspends RTH-specific levels that day. | Affects holidays where CME trades and the NYSE is closed (e.g. MLK Day, Presidents' Day). |
| **N7** | An exchange trade date contains a scheduled intra-session halt. Is the "CME session" the whole trade date or each segment? | `TRADE_DATE` · `SEGMENT` | `TRADE_DATE`: v1.0 §3 defines the session by trading day. | Changes which highs/lows exist on holiday-shortened days. Linked to N27. |
| **N8** | How is the first active contract chosen at dataset start? | `EXPLICIT` (you name it) · `HIGHEST_VERIFIED_VOLUME` (first session with verified volumes; processing starts the next session) | `HIGHEST_VERIFIED_VOLUME`: it is rule-based and consistent with A12, but depends on N13. | The first session in the data is 2022-10-02 (MNQZ2). |
| **N9** | After an I2.4 suspension (current contract ineligible, no verified successor), how and when does processing resume? | (a) never in this run (current behaviour) · (b) at the next session open once a successor is selected by highest verified volume among eligible contracts, with all pre-existing liquidity quarantined because no Δ can be measured | (b): (a) ends the backtest at the first data problem near an expiry. | With the 1m-sum volume source this would trigger at the first expiry (see N13). |
| **N10** | A newly formed, verified level has the same price and direction as an existing **quarantined** level (or a new quarantined level meets a verified one). | `SEPARATE` (two references) · `MERGE_KEEP_QUARANTINE` · `MERGE_LIFT_QUARANTINE` | `SEPARATE`: it keeps the verified level eligible without vouching for the old one's history. | Downside of `SEPARATE`: one bar can then create two sweep events at the same price. The strategy layer must not count them twice. |
| **N11** | Do quarantined (unconsumed) levels count toward a dual-sided sweep? | `True` · `False` | `True`: v1.0 §8.1 exists because the intrabar sequence is unknown, and that is equally true for quarantined levels. | Rare. Only matters after a data gap. |
| **N12** | What evidence verifies that a missing minute had zero trades (A3)? | (a) Databento daily condition `available` · (b) an independent second vendor's trade data · (c) none (every missing minute stays `UNCERTAIN`) | **(c)**, or (b) if you buy one. **(a) is contradicted by the data** (see evidence). | 2022-12-13 08:31 ET (the CPI release minute) is missing on an "available" day, between minutes of 1,650 and 4,966 contracts. Impact of (c): 6 quarantine events in 4 years, none inside 09:30–11:30. |
| **N13** | Source of "verified completed-session volume" for rollover. | (a) sum of 1m bars (verifiable only if every minute is observed) · (b) exchange cleared volume (Databento `statistics` stat_type 6), usable only from its publication time | **(b)** | **Blocker:** under (a) the second-deferred contract was fully observed in only 50 of 1,046 sessions. I3 requires every eligible successor verified, so nearly every roll decision would be suspended. Note that (b) includes block trades and is published the next day, so rolls would happen one session later than with (a). |
| **N13b** | Alternative to N13(b): amend I3 so that only successors with verifiable volume are "eligible". | approve · reject | Reject unless you prefer it to buying statistics data. | This would be a rule change, so it needs your explicit authorization. |
| **N18** | Which coarse data counts as "verified" for A4 quarantine resolution? | (a) Databento `ohlcv-1h` · (b) venue-published session high/low (Databento `statistics` stat_type 4/5) · (c) none | **(b)** | (a) is built from the same trade feed, so it cannot fill a feed gap. Under (b), any level outside the venue's session range is proven untouched, which would reinstate most levels after each gap. |
| **N19** | Databento buckets bars by `ts_recv` (capture-server receive time), not exchange time. Trades near a minute boundary can land in the neighbouring bar. | accept vendor bars · rebuild bars from `trades` using exchange `ts_event` | Accept for now, documented. Revisit if results are sensitive to the boundary minute. | Affects which minute a penetration or close falls in, at the margin. |
| **N22** | **Unscheduled exchange halts** (e.g. the apparent 2025-11-28 event: no MNQ prints 21:45→08:29 ET). These are neither scheduled closures nor missing data. | (a) treat as missing data (current: quarantine everything, invalidate setups) · (b) treat as a closure (terminate confirmations, no quarantine) **only when confirmed by an official exchange notice**, otherwise (a) | (b) | Under (a), every active level is quarantined after each halt unless N18(b) evidence is available. |
| **N24** | Databento symbol-mapping `end_date`: inclusive or exclusive? | verify from docs | Verify. Currently treated as exclusive, and inclusive-only matches are reported. | In the audited data, **0** records depended on it. |
| **N27** | Holiday trade-date assignment, e.g. which trade date the Thanksgiving-evening session belongs to. | from the official CME calendar | Take it from CME notices, never infer it. | Decides which highs/lows exist and when they become available. Linked to N7. |
| **N28** | Databento condition status is per **UTC date**, while sessions span two UTC dates. How should a "degraded" day map to sessions? | flag both sessions touching the date · flag only overlapping minutes | Flag both sessions, as audit metadata only. | Only informative if N12 ≠ (a). |

## B. Interpretations implemented — need ratification

| ID | What I implemented | Basis |
|---|---|---|
| **N1** | **Processing order within a minute:** invalidation counters, then pending confirmations, then penetration (only against levels available at or before the bar's open), then deadline rejections, then new levels (testable from the next bar), then closure rules. | v1.0 §15 required this to be settled explicitly, and your amendment didn't address it. This is the only order without look-ahead. |
| **N2** | **Dataset start:** a run begins at a configured session boundary. Minutes without data inside the run are classified normally (uncertain). | Original A13 was not addressed. |
| **N14** | **Quarantine scope:** a missing or uncertain minute quarantines **every** unconsumed level. | A4: an unobserved minute's price range is unbounded. |
| **N15** | **Quarantined levels:** they are still tested for penetration. If one is penetrated, it is consumed, and its sweep event is recorded for audit only and is never tradeable, even after reinstatement. | A4 ("ineligible"), I6 ("never retroactively authorize"). |
| **N16** | **Setups on a missing minute:** it invalidates **every** tracked confirmed setup (reason `DATA_GAP`) and rejects every pending confirmation. | v1.0 §13: the missing close could have been a qualifying or a resetting close. |
| **N17** | **Rollover without a valid translation:** after I1 (no Δ) or I5 (off-grid), levels are quarantined **and frozen**, with no further penetration tests in a price system they are not in. | I1, I5. Without this, old-contract prices would be compared with new-contract bars. |
| **N20** | **Amendment letters vs. text:** your amendment's option letters (A3, A4, A6, A8, A9, I-items) don't match my earlier option lists. I implemented the amendment **text**. | — |
| **N23** | **Identical cross-file duplicates:** they are kept once, and a conflicting duplicate aborts the load. 2,025 identical records on 2025-10-01. | I8. Identical records are the same observation, not a repair. |
| **N25** | **Spread exclusion:** spreads are excluded by symbol containing `-`, pending definition data. | The audit confirmed spread prices are off the 0.25 grid. |
| **N29** | **Coarse-evidence attribution:** a coarse high/low proves penetration "during the gap" only if it exceeds every observed bar in its window **and** every missing minute in that window belongs to this level's quarantine. | A4 steps 3–4. Prevents blaming a gap that occurred before the level existed. |
| **N30** | **Suspension:** on a suspension, tracked setups expire (`PROCESSING_SUSPENDED`) and pending confirmations are rejected. | I2.4 gives no rule for open setups. |
| **N31** | **Eligibility needs the exchange open:** a setup also requires the exchange to be open at the confirmation's observable time (q+1). | A9: "cannot authorize execution during exchange closures". |

## C. Verification and data gaps (block a real run)

| ID | Gap | What closes it |
|---|---|---|
| **N21** | MNQ contract specs (tick size, cycle, last-trade rule) are not verified, because cmegroup.com blocks automated access. The data is consistent with a 0.25 tick and a last trade at 09:30 ET, but that is an observation only. | You check CME, or I use the Databento `definition` schema. |
| **N26** | No verified exchange calendar. The candidate library is contradicted by the data: 13:15 vs 13:00 ET early closes on 9 days, and trading on 2025-01-08/09 that the library treats as closed. | A calendar built from CME holiday notices for 2022-10 → 2026-10. |
| **C1** | No verified NYSE reference calendar (holidays, early closes). | Built from nyse.com, which is reachable here; I can draft it for your review. |
| **C2** | Contract definitions (expiration timestamps, `min_price_increment`, outright/spread, activation) are missing from the batches. | Databento `definition` schema for MNQ.FUT, 2022-10 → 2026-10. |
| **C3** | Statistics data (cleared volume, session high/low) is needed if you pick N13(b) / N18(b). | Databento `statistics` schema for MNQ.FUT, same range. |
| **C4** | Liquidity formed before 2022-10-02 18:00 ET cannot be identified. | Disclosure only; there is no fix. |
