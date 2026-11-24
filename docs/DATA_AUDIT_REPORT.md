# Dataset Audit — Databento GLBX.MDP3 MNQ ohlcv-1m (spec Part VI step 7)

Run: 2026-11-24, `python tools/audit_databento_batch.py docs/audit/audit_2026-11-24.json <batchA> <batchB>`.
The full machine-readable output is in [`audit/audit_2026-11-24.json`](audit/audit_2026-11-24.json). No liquidity engine run or backtest was performed.

## Inputs

| Batch | Query range (UTC) | Parent symbol | Files | Manifest SHA-256 / size |
|---|---|---|---|---|
| GLBX-20261021-SUW6SDRRS7 | 2022-10-01 → 2025-10-02 | MNQ.FUT | 46 | all match |
| GLBX-20261021-5QBR9J5GST | 2025-10-01 → 2026-10-21 | MNQ.FUT | 24 | all match |

Both batches are `ohlcv-1m`, DBN with zstd, raw fixed-point prices (`pretty_px=false`), and `split_symbols=true` (one symbol per file).

## 1. Record-level integrity — PASSED

Every outright record passed every check:
- the instrument ID matches the DBN header's symbol mapping on the record's UTC date;
- prices sit on the 0.25 grid;
- low ≤ open, close ≤ high;
- volume > 0;
- timestamps are minute-aligned;
- there are no duplicates or out-of-order rows within a file.

**Cross-batch overlap.** The batches overlap on 2025-10-01. 2,025 overlapping records (MNQZ5 1,380, MNQH6 621, MNQM6 23, MNQZ6 1) were **identical** in both files, and the loader keeps one copy. A conflicting duplicate would have aborted the load.

**Contracts.** 20 outrights, MNQZ2 → MNQU7. Last bars of expiring contracts are always 09:29 ET on the expiry date, which is consistent with a 09:30 ET last trade. That is an observation from the data, not a verified rule.

**Spreads.** 41 calendar-spread files were excluded (alongside 25 outright files covering 20 contracts), with symbols containing `-`. Their prices are **not** on the 0.25 grid (e.g. 104.55), which confirms that spreads must never be mixed with outrights.

The `-` naming convention is used only to *exclude* spreads. Outright status still has to be confirmed from the Databento definition schema.

## 2. Missing scheduled minutes (front contract)

The "front" here is the highest observed volume in each session. That's a description only, not the A12 rule. The calendar is a **candidate**, not verified (see §4).

| Metric | Value |
|---|---|
| Scheduled minutes (candidate calendar) | 1,433,670 |
| Missing minutes | **651** (0.045%) |
| Missing inside 09:30–11:30 ET | **0** of 125,280 |
| Sessions with any missing minute | 6 of 1,046 |
| Missing while another MNQ outright printed | 1 |

**The six cases:**

| Session | Contract | Missing (ET) | Context | Databento condition |
|---|---|---|---|---|
| 2022-12-12 | MNQH3 | 12-11 23:34 (1 min) | thin overnight (vol 7 before, 10 after); MNQZ2 printed that minute | available |
| 2022-12-13 | MNQH3 | 08:31 (1 min) | 1,650 contracts the minute before, 4,966 after (CPI release minute) | **available** |
| 2023-07-04 | MNQU3 | 07-03 18:48 (1 min) | thin (8 / 9) | available |
| 2025-04-02 | MNQM5 | 16:31 (1 min) | 882 before, 6,257 after | available |
| 2025-04-03 | MNQM5 | 04-02 18:00–18:01 (2 min) | session open, no prints | available |
| 2025-11-28 | MNQZ5 | 11-27 21:45 → 11-28 08:29 (**645 min**) | no MNQ contract printed at all | **degraded** |

**What this means for the open items:**
- **N12.** Databento's "available" day status **cannot** stand in for proof of a zero-trade minute. 2022-12-13 08:31 ET is very unlikely to have been a no-trade minute, yet the day is "available". The other short cases could be exchange pauses or feed loss, and the data can't tell which. Under the approved rules all of them stay `UNCERTAIN`.
- **Practical impact.** Five short events plus one long one in four years. Each event quarantines the active book (A4), so the important question is reinstatement: which coarse source counts as verified (N18).
- **New N22: unscheduled exchange halts.** The 2025-11-28 window looks like an exchange-wide halt, because every contract went quiet. I have **not** verified that against CME notices. The spec distinguishes scheduled closures from missing data, and an *unscheduled* halt is neither, so it needs a ruling. Until then, the current behaviour treats it as missing data.

## 3. Volume verifiability for rollover (N13 + I3)

- In all 16 sessions where the highest-volume contract changed, both the outgoing and incoming contracts were **fully observed**. The only exception is 2022-12-12, where MNQH3 was missing 1 minute.
- **The blocker:** I3 also requires the volume of *every* eligible successor to be verified. Deferred contracts trade thinly, and the second contract was fully observed in only **50 of 1,046** sessions. With the 1-minute-sum volume source, almost every rollover decision would be suspended, and the engine would stop at the first expiry (I2.4).

N13 needs a decision:
- **(b)** Use exchange cleared volume (Databento `statistics`, stat_type 6), which requires downloading that schema; or
- **(c)** Restrict "eligible successors" to contracts with verifiable data — a rule change only you can authorize.

## 4. Candidate calendar check — candidate REJECTED for production use

The candidate is `pandas_market_calendars` 5.6.1 `CME_Equity`, a third-party library. The data contradicts it:

- **Early-close days** (2022-11-25, 2023-07-03, 2023-11-24, 2024-07-03, 2024-11-29, 2024-12-24, 2025-07-03, 2025-11-28, 2025-12-24): the front contract prints **15 minutes past** the library's 13:00 ET close, through the 13:14 bar. That suggests a 13:15 ET close, which I have not verified.
- **2025-01-08 / 2025-01-09**: 360 and 570 front-contract bars fall in a period the library treats as closed. The NYSE was closed on 2025-01-09; the exact CME schedule that day needs an official notice.

A verified calendar has to be built from CME holiday notices. These discrepancy dates are the checklist.

## 5. Not yet available (needed before an engine run)

1. **Contract definitions:** expiration timestamps, `min_price_increment`, outright vs spread, activation. These come from the Databento `definition` schema, which isn't in these batches. Without them, rollover eligibility (I3) can't be evaluated.
2. **Verified CME exchange calendar and NYSE reference calendar** for 2022-10 → 2026-10.
3. **Coarse/volume evidence**, depending on your N13/N18 decisions (Databento `statistics` for session high/low and cleared volume).
4. **Decisions on N1–N22.**
5. **Liquidity before the first session cannot be identified.** The first session is 2022-10-02 18:00 ET (MNQZ2); levels from before then are unknown (spec §5 disclosure).
