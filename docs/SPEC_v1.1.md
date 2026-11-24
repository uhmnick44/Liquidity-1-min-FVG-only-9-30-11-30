# Mathematical Liquidity Engine — Consolidated Specification v1.1

Sources: [`SPEC.md`](SPEC.md) (v1.0, frozen) plus the approved v1.1 amendment (A2–A16, I1–I11, Parts III–VI).
Where they differ, the amendment wins only where it explicitly modifies v1.0. Everything else in v1.0 stands.

Status legend: **[v1.0]** unchanged original rule · **[v1.1 Axx/Ixx]** approved amendment ·
**[IMPL]** implementation semantics forced by the rules (no new behaviour) · **[OPEN Nxx]** not determined by
the spec; the engine refuses to proceed (raises `UnauthorizedPolicyError`) until you authorize a policy.

Code map: `liqengine/engine.py` (identification + lifecycle), `rollover.py`, `calendars.py`, `data.py`,
`eligibility.py` (strategy layer), `driver.py` (wiring), `audit.py`.

---

## 1. Notation and time

- H, L, C, P, δ, t, q, E as v1.0 §2. **[v1.0]**
- All prices are converted to integer ticks on the contract's verified grid. Off-grid prices raise an error and are never rounded. **[v1.0 §15, v1.1 I5]**
- Timestamps are normalized to UTC. Session definitions for the current strategy use America/New_York via the IANA tz database. DST-nonexistent or ambiguous wall times raise an error. **[v1.1 A2]**
- A 1-minute bar labelled m (vendor `ts_event` = interval start, verified in [VENDOR_VERIFICATION.md](VENDOR_VERIFICATION.md)) covers [m, m+1), and its information is observable at m+1. **[v1.1 A2/A7, IMPL]**

## 2. Calendars (A8, A9, I9)

- Exchange calendars and reference-market calendars are separate, versioned data files. Each lists every scheduled segment explicitly. **[v1.1 I9]**
- A date missing from a calendar raises `CalendarUnavailable`; nothing is inferred. An unverified calendar, or one whose ID differs from the instrument's configured calendar, is refused by the driver (no substitution). **[v1.1 I9, A9]**
- Exchange ("CME") session: one exchange trade date and its scheduled segments. **[v1.0 §3]**
  If a trade date has more than one scheduled segment (an intra-session halt), whether the session is the whole trade date or each segment is **[OPEN N7]**.
- RTH: from the reference open to the earlier of the reference close and the exchange session end. On an early close, RTH ends at the scheduled reference close. **[v1.1 A9]**
- Reference market closed: no RTH liquidity is generated; monitoring of existing liquidity continues. **[v1.1 A8]**
- Pre-window: from the exchange session start to the reference open. **[v1.0 §3, IMPL]**
  On a reference-closed day, whether a pre-window exists (and with what end) is **[OPEN N6]**.
- Scheduled closures (maintenance, weekends, holidays, halts) are the gaps between segments. They are never data gaps. **[v1.0 §13, v1.1 A3]**

## 3. Market data (A2, A3, I7, I8)

- Initial provider: Databento `ohlcv-1m`, CSV or Parquet, individual unadjusted contracts. Prices are accepted as 1e-9 fixed-point integers or exact decimal strings; floats are refused. **[v1.1 A2]**
- Hard errors (no repair): duplicate timestamps, non-minute-aligned timestamps, wrong `rtype`, off-grid prices, inconsistent OHLC, non-positive volume. Out-of-order rows are counted in the load report and then sorted, with content unchanged. Unknown instrument IDs and non-outright instruments (spreads) are excluded and reported. **[v1.1 I8]**
- Every scheduled minute is classified as `OBSERVED`, `VERIFIED_ZERO_TRADE`, `CONFIRMED_MISSING` or `UNCERTAIN`. A missing bar is `UNCERTAIN` unless evidence says otherwise; zero trading is never assumed. There is no minimum-run threshold. **[v1.1 A3]**
- `CONFIRMED_MISSING` and `UNCERTAIN` are both treated as missing data by the engine, and kept distinct in audit records. **[IMPL]**
- Which evidence counts as verifying a zero-trade minute is **[OPEN N12]**.
- The audit (`python -m liqengine.cli audit`) must run before any backtest. **[v1.1 A3, I8]**

## 4. Liquidity identification

| Category | Price | Available (first testable bar opens at) | Rule |
|---|---|---|---|
| Exchange session H/L | max H / min L over the session | session end | [v1.0 §4.2] |
| RTH H/L | over RTH minutes | RTH end | [v1.0 §4.2, v1.1 A8/A9] |
| Pre-window H/L | over pre-window minutes | pre-window end (reference open) | [v1.0 §4.2] |
| 15m swing | strict 3-candle fractal | close of candle i+1 | [v1.0 §4.3, v1.1 A11] |

- The formation timestamp is the open of the first 1-minute bar that printed the extreme. It is recorded for audit only and has no behavioural effect. **[IMPL]**
- **15m candles [v1.1 A11.1/A11.2/I7].**
  - Construction: built from the same 1-minute data, on :00/:15/:30/:45 UTC buckets (these equal ET quarter-hours).
  - Validity: a candle qualifies only if all 15 minutes are scheduled and lie in one uninterrupted segment, none is missing or uncertain, and at least one trade is observed. Verified zero-trade minutes inside a traded candle are allowed.
  - Restarts: an invalid candle, or any scheduled closure, restarts fractal construction.
- **Window extremes over missing or uncertain minutes:** registering them quarantined vs. not registering them is **[OPEN N3]**.

## 5. Same-timestamp ordering (v1.0 §15 requirement) — **[IMPL, ratification requested: N1]**

For bar m, the engine runs these steps in order:
1. Invalidation counters of sweeps confirmed before m.
2. Confirmation of pending sweeps.
3. Penetration against references available at ≤ m, then consumption, the dual-sided check, new events and k = 0 confirmation.
4. Rejection of pending sweeps whose t+3 deadline = m.
5. Finalization of windows closing at m+1, and registration of new levels (available from m+1).
6. Closure rules, if m+1 ends a segment.

**Why the order can't change outcomes:**
- No level can be penetrated between its formation and its availability. A session extreme bounds every bar in its window, and every 1-minute bar in candle i+1 has H ≤ H(i+1) < H(i). So the only ordering question is the boundary instant, and any other order would test a level before it is observable (look-ahead).
- Steps 1–3 touch disjoint state (different events), so their relative order cannot change outcomes.

## 6. Sweep penetration, confirmation, consumption [v1.0 §6–§8; v1.1 A5, I11]

- Penetration: BSL `H ≥ P+δ` (ticks: H ≥ P+1); SSL `L ≤ P−δ`. Confirmation: BSL `C < P`, SSL `C > P` on the bars at clock minutes t…t+3. The first qualifying close sets q, and an equal close doesn't qualify. **[v1.0]**
- First penetration permanently consumes the reference, whatever happens afterwards. **[v1.0 §7]**
- Verified zero-trade minutes use up confirmation-window minutes but can never confirm. **[v1.1 I11]**
- Any scheduled closure terminates an unfinished confirmation (`SCHEDULED_CLOSURE`); consumption stands. **[v1.1 A5]**
- A missing or uncertain minute rejects every pending confirmation (`DATA_GAP`). **[v1.0 §13]**
- Dual-sided: a bar penetrating active BSL and SSL rejects every event that bar initiated, consumes every penetrated level, and is logged as a data-quality event. Events pending from earlier bars still evaluate that bar's close. **[v1.0 §8.1; literal reading]**
  Whether quarantined levels count toward dual-sidedness is **[OPEN N11]**.
- Multiple same-side levels: separate events, evaluated independently. **[v1.0 §8.2]**
- Consolidation: one reference per (direction, price) among unconsumed references, with all source labels kept. A new level at a consumed price is a new reference. **[v1.0 §5]**
  A collision involving a quarantined reference is **[OPEN N10]**.

## 7. Extreme and invalidation [v1.0 §9–§10; v1.1 A6, A16]

- E = max H (BSL) / min L (SSL) over t…q, frozen at confirmation.
- Invalidation: three consecutive closes strictly beyond E, all after q. Any non-qualifying close, including an exact equal, resets the count. Wicks never count.
- Invalidation and expiry are tracked for every confirmed sweep, all day, independent of any trading window and of whether a trade was taken. **[v1.1 A6, A16]**
- Engine-level expiry happens only at a rollover (`ROLLOVER`) or a processing suspension. Expiry at the end of the trading window is a strategy-layer concept (§10). **[v1.1 A6, §12, IMPL]**
- A missing or uncertain minute invalidates every tracked confirmed sweep (`DATA_GAP`). **[v1.0 §13: "invalidate affected pending setups"; every tracked setup is affected, because the missing close is unknown]**
- Counter behaviour across a scheduled closure is **[OPEN N4]**, and across a verified zero-trade minute (which has no close) is **[OPEN N5]**.

## 8. Missing data, quarantine, reinstatement [v1.0 §13; v1.1 A4, I6]

- A missing or uncertain minute quarantines **every** unconsumed reference. During an unobserved minute the price range is unbounded ("never assume an unobserved movement occurred or did not occur"). **[v1.1 A4; scope ratification requested: N14]**
- Quarantined references are ineligible for trading. They are still tested for penetration: an observed penetration consumes them, and the resulting event carries `REFERENCE_QUARANTINED` and can never become eligible. **[v1.1 A4; audit-only events: N15]**
- Recovery order:
  1. Recover the 1-minute data. Recovered bars are loaded as observed data before the run.
  2. Otherwise use verified coarse bars (`CoarseBar`), applied only from their own availability time.
- Coarse evidence establishes only these facts:
  - **Untouched:** the coarse extreme doesn't reach P±δ, which resolves those minutes.
  - **Penetrated in the gap:** the coarse extreme exceeds every observed bar in its window, reaches P±δ, and every missing minute in the window belongs to this reference's quarantine. The reference is consumed (`INFERRED_FROM_COARSE_DATA`), and the event is rejected `PENETRATED_DURING_MISSING_DATA` with no 1-minute sequence reconstructed.
  - Anything else leaves the reference quarantined. **[v1.1 A4 steps 2–5]**
- Reinstatement happens only when every quarantine reason is resolved by evidence and the reference is unconsumed with valid coordinates. It takes effect from the evidence's availability time, and earlier events stay ineligible. **[v1.1 I6]**
- Which coarse sources count as "verified" is **[OPEN N18]**.

## 9. Contract selection and rollover [v1.1 A12, I1–I5, I10]

- At each completed exchange session, the selector compares the verified completed-session volume of the current contract with that of eligible successors.
  - Eligible successor: outright, in the approved cycle, later expiry, listed, not expired, never previously active.
  - It switches only if the best successor's volume strictly exceeds the current one. Ties between successors go to the earliest expiry. Older contracts are never re-selected.
- The switch happens at the open of the next scheduled session. Δ = P_new − P_old, using the closes of both contracts' final scheduled 1-minute candle of the crossover session (e.g. the 16:59 ET bar).
- At the switch:
  - Every unconsumed reference becomes `L + Δ`, with its price history, original prices, labels and status kept.
  - Confirmed setups expire (`ROLLOVER`).
  - Pending confirmations can't exist, because the closure already terminated them.
- I1 (reference price unavailable): the switch proceeds, and every unconsumed reference is quarantined `ROLLOVER_REFERENCE_UNAVAILABLE`.
- I5 (translated price off the new grid): the reference is quarantined `INCOMPATIBLE_GRID`.
- In both I1 and I5 cases the reference's coordinates are marked invalid and it is frozen: no further penetration testing in a coordinate system it isn't in. **[IMPL; ratification requested: N17]**
- I2 (volume unverifiable): retain the current contract and flag the session. If the current contract becomes ineligible with no verified successor, processing is suspended. Resumption is **[OPEN N9]**.
- The initial contract choice is **[OPEN N8]**. The verified-volume source is **[OPEN N13]**.

## 10. Strategy-layer eligibility [v1.0 §11; v1.1 A6, A7, A16] — `eligibility.py`, read-only

The current strategy window is 09:30–11:30 America/New_York, and it is configurable. A confirmed sweep is an eligible setup iff all of these hold:
- it is not dual-sided;
- its reference was not quarantined at penetration;
- the penetration bar opens inside [start, end);
- the confirmation is observable (q+1) strictly before the window end, so a confirmation on the 11:29 bar is **ineligible** (A7 modifies v1.0 §11);
- the exchange is open at q+1.

The setup is active from q+1 until the earliest of invalidation-observable, rollover expiry, or the window end (11:30). Changing the window never changes engine state (tested).

## 11. Outputs [v1.0 §14]

`references.jsonl/csv`, `sweep_events.jsonl/csv`, `lifecycle_log.jsonl`, `data_quality.jsonl` and `provenance.json` (calendar versions, policy settings, evidence source). Penetration, confirmation and invalidation are reported both as bar-open time and as observable time.

---

## Open items requiring authorization

Each item is an explicit `PolicyConfig` field defaulting to `None`. The engine raises when it reaches one and does not guess. Tests exercise every option.

| ID | Question | Options in code |
|---|---|---|
| **N1** | Ratify the §5 ordering (the only look-ahead-free order). | n/a (implemented) |
| **N2** | Dataset start: the run starts at a configured session boundary; minutes without data inside the run are classified normally (uncertain). Ratify. | n/a (implemented) |
| **N3** | Session/RTH/pre-window extreme over a window containing missing/uncertain minutes. | `QUARANTINE` (reinstatable by coarse evidence) / `DO_NOT_REGISTER` |
| **N4** | Invalidation counter across a scheduled closure. | `CONTINUE` / `RESET` |
| **N5** | Invalidation counter across a verified zero-trade minute (no close exists). | `UNCHANGED` / `RESET` |
| **N6** | Pre-window when the reference market is closed. | `DO_NOT_GENERATE` / `GENERATE_TO_NOMINAL_TIME` |
| **N7** | Exchange session when a trade date has an intra-session scheduled halt. | `TRADE_DATE` / `SEGMENT` |
| **N8** | First active contract. | `EXPLICIT` / `HIGHEST_VERIFIED_VOLUME` (first session with all volumes verified; processing starts at the next session) |
| **N9** | Resuming after an I2.4 suspension. No rule exists, so the engine stays suspended for the rest of the run, and tracked setups expire `PROCESSING_SUSPENDED`. | needs a rule |
| **N10** | New level collides (same price/direction) with a quarantined level, or a new quarantined level collides with a verified one. | `SEPARATE` / `MERGE_KEEP_QUARANTINE` / `MERGE_LIFT_QUARANTINE` |
| **N11** | Do quarantined (unconsumed) levels count for dual-sided detection? | `True` / `False` |
| **N12** | **Practical blocker.** Databento prints no bar for a no-trade minute and builds OHLCV from its own trade feed, so its `trades` schema is not independent evidence. Without an authorized evidence source, every missing minute is `UNCERTAIN`, which quarantines the whole book. Candidates: (a) Databento `metadata.get_dataset_condition` = `available` for that day (vendor record of "no known issues"); (b) a second, independent vendor's trade data; (c) none. | needs decision |
| **N13** | Source of "verified completed-session volume". (a) Sum of 1m bar volumes: verifiable only if every minute is observed or verified zero-trade, so back months almost never qualify under N12(c). (b) Databento `statistics` stat_type 6 "cleared volume": exchange-published, includes block trades, and refers to the previous trading date, so it is usable only from its publication time. | (a) implemented; (b) supported via `external_volumes` |
| **N14** | Ratify the quarantine scope: every unconsumed reference on any missing minute. | n/a (implemented) |
| **N15** | Ratify that sweep events on quarantined references are recorded for audit and never eligible. | n/a (implemented) |
| **N16** | Ratify that every tracked confirmed setup is invalidated `DATA_GAP` on a missing minute. | n/a (implemented) |
| **N17** | Ratify freezing (no lifecycle tracking) for references with invalid coordinates after I1/I5. | n/a (implemented) |
| **N18** | Which coarse sources are "verified"? Databento `ohlcv-1h` is built from the same trade feed, so it can't fill a feed gap. Databento `statistics` session high/low (stat_type 4/5) are venue-published. | needs decision |
| **N19** | Databento buckets OHLCV by `ts_recv` (capture-server receive time), not the exchange matching time. A trade near a minute boundary may land in the adjacent bar. Accept, or build bars from `trades` using exchange `ts_event`? | needs decision |
| **N20** | Amendment option letters don't match my earlier A-option list for A3, A4, A6, A8, A9 and I-items. I implemented the amendment **text**, not the letters. Please confirm. | n/a |
| **N21** | MNQ contract specs could not be fetched from cmegroup.com (the site refuses automated requests). Tick size per contract will come from Databento definitions `min_price_increment` and be cross-checked against the instrument file. A human must verify against CME. | manual |
