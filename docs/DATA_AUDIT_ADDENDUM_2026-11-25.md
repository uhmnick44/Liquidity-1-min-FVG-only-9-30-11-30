# Data Audit Addendum — definition, statistics and status data (2026-11-25)

## Inputs
Three Databento batch jobs, GLBX.MDP3, MNQ.FUT parent, 2022-10-01 → 2026-10-21:

| Schema | Job ID | Files |
|---|---|---|
| definition | GLBX-20261125-7H9SQUPXMJ | 98 |
| statistics | GLBX-20261125-W8NBT3G9VB | 98 |
| status | GLBX-20261125-6TU3TPRR49 | 98 |

All files match their manifest SHA-256 and size. Databento's pre-submission quote was ≈ $0.34 total.

## 1. Contract definitions → `config/instruments/MNQ.json`
- **Contracts:** 21 outrights, MNQZ2 → MNQZ7, instrument_class `F`, security_type `FUT`. Listed months are 3/6/9/12 only.
- **Stability:** for every contract, the instrument ID, expiration, activation and tick size are identical across all of its definition records.
- **Tick size:** 0.25 for every outright. Spreads are 0.05 and excluded. This resolves N21/C2 and N25.
- **Expiration:** 09:30 ET on the expiry date. The bars agree: every contract's last bar is 09:29 ET.

## 2. Status → CME calendar `config/calendars/CME_GLBX_MNQ.json` (verified=false until sign-off)
- **Sessions:** 1,020, built from CME's scheduled transitions. All 4,165 scheduled open/close/halt times are identical across every active outright.
- **Reconciliation:** 0 bars print outside the calendar, and 0 print inside a reported halt. Of the front contract's missing minutes, 619 are explained by CME halts and 2 are unexplained (2022-12-11 23:34, 2023-07-03 18:48).
- **N12 evidence corrected.** 2022-12-13 08:31 ET, cited earlier as contradicting Databento's "available" status, was a **CME 2-minute halt**. N12 stays (c), because a day status still says nothing about individual minutes. The specific example now has an exchange explanation.
- **2025-11-28:** CME halt at 21:44:27 ET (reason "surveillance intervention"), scheduled pre-open at 08:00, reopen at 08:30.
- **Special case found:** on 2025-04-02 CME replaced the 18:00 scheduled open with a market-event pre-open and traded from 18:02. The builder treats that as the session open plus a halt until trading began.
- **Friday holidays 2026-06-19 and 2026-07-03:** CME sent "not available for trading" (scheduled) with no CLOSE message. The trade-date assignment is on the checklist.
- The third-party library's 13:00 early closes are wrong; CME's status shows **13:15**.

## 3. NYSE reference calendar `config/calendars/NYSE.json` (DRAFT, verified=false)
Drafted from pandas_market_calendars and exchange_calendars XNYS. They agree on all 1,065 weekdays. Every CME session's trade date has an NYSE entry, with 0 inconsistencies. Four CME sessions fall on NYSE-closed days (Good Fridays 2023/2026, 2025-01-09, 2025-07-04) and get no RTH or pre-window (A8/N6).

## 4. Statistics → rollover volume (N13b) and coarse evidence (N18b)
- **Cleared volume:** 4,828 figures used.
  - Mapping: assigned to the last ended session at receipt. 56 records were excluded because their reference date disagreed (Good Friday periods).
  - Publication lag after session end: 4–9 h normally, 44–55 h over weekends.
  - **Problem:** some first-published figures are **0** despite real trading. The proposed fix is **N32**: reject figures below the 1-minute bar volume. 29 records were rejected this way.
  - **Problem:** far-dated contracts often have **no** published figure. The proposed fix is **N33**: unverified if not published by the end of session D+1.
- **Venue session high/low:** 925,423 records, published as running values. 168 conflict with the bars, and the engine ignores those as inconsistent instead of using them.
- **Rollover replay:** 16 rolls. Each crossover session equals the session where the highest-volume contract changed, and each takes effect two sessions later because of the publication lag.

## 5. Pre-flight engine run (NOT a validation run; calendars unverified)
`tools/preflight_run.py`: full pipeline, spec v1.2 policies, 1,020 sessions, ~3.5 min. No crashes and no unauthorized-policy stops.

| | Without CME halts as notices | With CME halts as notices (N22) |
|---|---|---|
| Liquidity references | 40,920 | 40,913 |
| Still quarantined at end | 190 | **0** |
| Setups invalidated by data gaps | 237 | 163 |
| Missing-data events | 9 | 5 |

Four of the missing-data events fall on the **old** contract in the two sessions after volume has moved, before the published cleared volume confirms the roll (e.g. 2023-12-12, 2024-12-17, 2025-09-16). This is a direct consequence of N13(b)'s publication lag, not a data fault.

These counts are descriptive only. They say nothing about any trading edge.
