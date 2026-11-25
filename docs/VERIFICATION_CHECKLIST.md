# Verification Checklist — sign-off needed before the validation run

Everything below was derived from exchange data (CME status messages via Databento) or, for NYSE, from two
independent calendar libraries that agree on every day. You check it against official sources in your own
browser:
- **CME:** cmegroup.com → Trading Hours / Holiday Calendar notices.
- **NYSE:** nyse.com → Markets → Holidays & Trading Hours, plus NYSE press releases for past years and 2025-01-09.

Reply with `OK` per section (or list the line that is wrong). The calendar files are marked `verified: true`
only after that.

All times are America/New_York (ET).

---

## Section 1 — CME early closes (13:15 ET)
- [ ] 2022-11-25 · 2023-07-03 · 2023-11-24 · 2024-07-03 · 2024-11-29 · 2024-12-24 · 2025-07-03 · 2025-11-28 · 2025-12-24

The bar data agrees: the last bar is 13:14 on each date. The third-party library's 13:00 was wrong.

## Section 2 — CME holiday halts inside one trade date (trading 13:00 → 18:00 halted, same trade date)
- [ ] 2022-11-24 (Thanksgiving, trade date 11-25) · 2023-01-16 · 2023-02-20 · 2023-05-29 · 2023-06-19 · 2023-07-04 ·
  2023-09-04 · 2023-11-23 (Thanksgiving, trade date 11-24) · 2024-01-15 · 2024-02-19 · 2024-05-27 · 2024-06-19 · 2024-07-04 ·
  2024-09-02 · 2024-11-28 (Thanksgiving) · 2025-01-20 · 2025-02-17 · 2025-05-26 · 2025-06-19 · 2025-09-01 ·
  2025-11-27 (Thanksgiving) · 2026-01-19 · 2026-02-16 · 2026-05-25 · 2026-09-07

In each case the halted day's trading belongs to the **next** trade date. For example, Mon 2023-01-16 13:00 belongs to trade date Tue 2023-01-17.

## Section 3 — CME Friday holidays (13:00 ET stop)
- [ ] 2025-07-04: CME sent a scheduled **CLOSE** at 13:00, so it is its own trade date.
- [ ] 2026-06-19 and 2026-07-03: CME sent "not available for trading" at 13:00 but **no CLOSE**. I therefore assigned those Friday mornings to the following Monday's trade date (2026-06-22, 2026-07-06). Please confirm which trade date CME assigned to them.

## Section 4 — CME special closures
- [ ] Good Friday 2023-04-07: close 09:15
- [ ] Good Friday 2026-04-03: close 09:15
- [ ] Good Friday 2024-03-29 and 2025-04-18: no CME session at all
- [ ] 2025-01-09 (national day of mourning): close 09:30

## Section 5 — CME unscheduled halts (from CME halt/pause messages)

| Halt (ET) | Whole minutes without trading |
|---|---|
| 2022-12-13 08:30:01 → 08:32 (CPI) | 08:31 |
| 2025-04-02 16:30 → 16:32 | 16:31 |
| 2025-04-02 18:00 → 18:02 (exchange replaced the scheduled open) | 18:00, 18:01 |
| 2025-11-27 21:44:27 → 2025-11-28 08:00, then scheduled pre-open to 08:30 | 21:45 → 08:29 |

- [ ] Halts above match CME notices / your records
- [ ] **Decision (N22):** may CME's own halt messages count as the "official exchange notice"?
  - **Yes:** these minutes are treated as closures, so liquidity is not quarantined.
  - **No:** they stay missing data, and all active liquidity is quarantined at each one.

  Recommendation: **Yes.**

## Section 6 — NYSE reference calendar (RTH)
Closed:
- [ ] 2022-11-24 · 2022-12-26 · 2023-01-02 · 2023-01-16 · 2023-02-20 · 2023-04-07 · 2023-05-29 · 2023-06-19 · 2023-07-04 ·
  2023-09-04 · 2023-11-23 · 2023-12-25 · 2024-01-01 · 2024-01-15 · 2024-02-19 · 2024-03-29 · 2024-05-27 · 2024-06-19 ·
  2024-07-04 · 2024-09-02 · 2024-11-28 · 2024-12-25 · 2025-01-01 · 2025-01-09 · 2025-01-20 · 2025-02-17 · 2025-04-18 ·
  2025-05-26 · 2025-06-19 · 2025-07-04 · 2025-09-01 · 2025-11-27 · 2025-12-25 · 2026-01-01 · 2026-01-19 · 2026-02-16 ·
  2026-04-03 · 2026-05-25 · 2026-06-19 · 2026-07-03 · 2026-09-07

Early close 13:00:
- [ ] 2022-11-25 · 2023-07-03 · 2023-11-24 · 2024-07-03 · 2024-11-29 · 2024-12-24 · 2025-07-03 · 2025-11-28 · 2025-12-24

Every other weekday is 09:30–16:00.

## Section 7 — Two new rule questions found while wiring the statistics data

- [ ] **N32: reject impossible cleared-volume figures.**
  - **Problem:** on 3 trade dates (2025-06-05, 2025-07-04, 2025-08-25) Databento's first cleared-volume figure is **0** although the contracts traded (up to 1.6M contracts).
  - **Proposed rule:** a figure below the volume visible in the 1-minute bars is rejected. Cleared volume includes block trades, so it can only be equal or larger. The first figure that passes is used, from the time it was published.
  - Recommendation: **Yes.**
- [ ] **N33: publication deadline.**
  - **Problem:** cleared volume for session D is published during trade date D+1, and some far-dated contracts get no figure at all.
  - **Proposed rule:** if any required figure is still missing at the end of session D+1, the rollover decision for session D is unverified (I2: keep the current contract).
  - Recommendation: **Yes.**

## For information (no action)
- **Remaining unexplained missing minutes:** 2022-12-11 23:34 and 2023-07-03 18:48 are the only front-contract missing minutes not explained by a CME halt. Both are thin overnight minutes. They remain `UNCERTAIN` (N12) and quarantine the active book until venue session-high/low data resolves them.
- **Rollover replay:** 16 rolls. Each happens on the session where the highest-volume contract changed, and takes effect two sessions later (publication lag of cleared volume).
