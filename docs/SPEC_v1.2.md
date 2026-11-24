# Mathematical Liquidity Engine — Specification v1.2 (decision record)

**Authority:** on 2026-11-24 the trader approved every recommendation in [`OPEN_ITEMS.md`](OPEN_ITEMS.md)
("Everything ambiguity will be solved with your recommendation").

v1.2 = [v1.1](SPEC_v1.1.md) + the decisions below. Everything in v1.0/v1.1 not listed here is unchanged.
The code enforces it through `liqengine.policies.APPROVED_V12`; every policy field is set (tested).

## Rule decisions (Section A)

| ID | Decision (now a rule) | Where |
|---|---|---|
| N3 | A window extreme over missing/uncertain minutes is **registered quarantined**, and is reinstatable by verified coarse evidence. | `policies.APPROVED_V12`, `engine._finalize_window` |
| N4 | The invalidation counter **resets** at every scheduled closure, and at every notice-verified halt (N22). | `engine._closure` |
| N5 | The invalidation counter **resets** on a verified zero-trade minute. | `engine._step_zero_trade` |
| N6 | **No pre-window** liquidity on reference-closed days. | `calendars.plan_session` |
| N7 | Exchange session = **trade date**. Intra-session halts don't split it. | `engine.begin_session` |
| N8 | Initial contract = **highest verified completed-session volume** (tie → earliest expiry). Processing starts at the next session. | `driver.run` |
| N9 | **Resume after suspension:** at each completed session while suspended, pick the highest-verified-volume eligible successor (all candidates must be verified; tie → earliest expiry). Processing resumes at the next session open. All pre-existing unconsumed liquidity is quarantined and frozen (`SUSPENSION_NO_ADJUSTMENT`), because no Δ exists. | `driver._select_by_volume`, `engine.resume` |
| N10 | A collision with a quarantined level creates a **separate** reference. The strategy layer must not double-count same-price events. | `engine._register` |
| N11 | Quarantined levels **count** toward dual-sided detection. | `engine._step_penetration` |
| N12 | **No zero-trade evidence source.** Every vendor-omitted minute is `UNCERTAIN`. Databento day condition is not evidence (contradicted by 2022-12-13 08:31 ET). | `data.BarStore.classify` (no evidence supplied) |
| N13 | Rollover volume = **exchange cleared volume** (Databento `statistics` stat_type 6). It is assigned to the most recent session that had ended at `ts_recv`, and is usable from the minute after `ts_recv`. If `ts_ref` is set, it must match. The first-received figure is used; revisions and deletes are reported. | `statistics.convert` → `driver.run(external_volumes=…)` |
| N13b | I3 is **not** amended. | — |
| N18 | Verified coarse evidence = **venue session high/low** (stat_type 5/4), one-sided. A record stamped at minute m inside a session bounds minutes `[start, m)` and printed within `[start, m+1)`. After the session end it bounds the whole session. `ohlcv-1h` is **not** accepted. | `statistics.convert`, `engine.apply_coarse_evidence` |
| N19 | Databento vendor bars (`ts_recv` bucketing) are **accepted**, documented. | `docs/VENDOR_VERIFICATION.md` |
| N22 | An unscheduled halt confirmed by an **official exchange notice** is treated as a closure: it terminates confirmations (`UNSCHEDULED_HALT`), resets counters, restarts fractals, and quarantines nothing. A bar printed inside a "verified" halt is a hard data conflict. Without a notice, the gap is missing data. | `data.MinuteEvidence.verified_halts`, `engine.process_minute` |
| N24 | Symbol-mapping `end_date` is **exclusive**. Verified: the docs' mapping intervals chain end-to-start (d1 = next d0), and symbology `end_date` is documented as "exclusive". | `dbn.check_mapping` |
| N27 | Holiday trade-date assignment comes **only from official CME notices** (calendar data). | calendar files (C-gap) |
| N28 | Every session touching a Databento-degraded UTC date is flagged, as audit metadata only. | `tools/audit_databento_batch.py` |

## Ratified interpretations (Section B)

N1 (per-minute ordering), N2 (run starts at a session boundary), N14 (any missing minute quarantines all
unconsumed levels), N15 (events on quarantined levels are audit-only, never eligible), N16 (a missing minute
invalidates all tracked setups and rejects pending confirmations), N17 (freeze after I1/I5), N20 (amendment
text over option letters), N23 (identical cross-file duplicates kept once), N25 (spreads excluded by `-`
pending definitions), N29 (coarse-evidence attribution rule), N30 (suspension expires setups), N31
(eligibility requires the exchange to be open at q+1). All are implemented as described in OPEN_ITEMS.md §B.

## Still open: verification and data (Section C). Not rule questions, but they block a real run

| ID | Needed | Who |
|---|---|---|
| N21 / C2 | MNQ contract definitions: expirations, tick, outright flag, activation (Databento `definition` schema; CME specs check) | you download; I wire it in |
| N26 | Verified CME exchange calendar 2022-10 → 2026-10 from CME notices. Check-list: the 13:15 vs 13:00 early closes, 2025-01-08/09, the 2025-11-28 event | human verification |
| C1 | Verified NYSE reference calendar | I can draft it from nyse.com for your check |
| C3 | Databento `statistics` schema for MNQ.FUT (cleared volume, session high/low). Required by N13/N18; the field-mapping rules above must be checked against it | you download |
| N22 input | Official CME notice(s) for any unscheduled halt (e.g. 2025-11-28), if one exists | human |
| C4 | Liquidity formed before 2022-10-02 18:00 ET cannot be identified | disclosure |

## New issues found while implementing v1.2

None that change behaviour. One mapping detail is **unverified until statistics data is available**: which
session a cleared-volume or session-high/low record refers to. The rules above follow the documented wording
("previous trading date"; "during the trading session") and are causal. `statistics.convert` reports any
record that doesn't fit (ts_ref mismatch, unmapped, outside calendar) instead of guessing.
