# Validation Report — liquidity engine against spec v1.2

Date: 2026-11-24. Environment: Python 3.13.18, pytest 9.1.1, pyarrow 26.0.1, Linux.

## Result actually observed

```
$ python3 -m pytest tests -q
116 tests: 115 passed, 1 skipped (spec v1.2 run; the skip is the DBN round-trip, which passes when LIQ_DBN_FIXTURE points to a real file)
$ (earlier v1.1 run) 105 passed in 1.56s   (incl. DBN round-trip on a real MNQH3 file via LIQ_DBN_FIXTURE;
                       without the fixture: 104 passed, 1 skipped)
```

All tests use **synthetic** data. No real market data has been processed (see Limitations).

## Coverage of required validation (spec v1.0 §15, v1.1 Part IV)

| Requirement | Tests |
|---|---|
| Penetration boundary (H = P vs H = P + δ; SSL mirror) | `test_sweeps::test_bsl_high_equal…`, `…one_tick_above…`, `…ssl_low_equal…` |
| Same-candle confirmation; close = P rejected; k = 3 accepted; k = 4 rejected | `test_sweeps` (4 tests) |
| Extreme over t..q, frozen after q | `test_confirmation_on_k3…`, `test_extreme_frozen…`, `test_ssl_confirmation_and_extreme` |
| Consumption permanent, no reactivation, revisit creates nothing, new level at consumed price | `test_consumption_is_permanent…`, `test_new_independent_level…`, `test_ssl_invalidation_and_no_reactivation` |
| Duplicate-level consolidation with labels | `test_consolidation_same_price…` |
| Invalidation: third close, equal close resets, wick ignored, q excluded | 4 tests in `test_sweeps` |
| Opposing sweeps coexist; multiple same-side levels independent | `test_opposing…`, `test_multiple_same_direction…` |
| Dual-sided sweeps | `test_dual_sided_rejects_both…`, `test_dual_sided_bar_still_confirms_earlier…` |
| Same-timestamp ordering / availability | `test_reference_not_testable_before_availability`, fractal/session availability tests |
| Closure terminates confirmation (A5) | `test_scheduled_closure_terminates_pending_confirmation` |
| Zero-trade advances window, can't confirm (I11) | `test_zero_trade_minutes_advance…`, `…then_confirmation_inside_window`, parametrized `Z` test |
| Missing data: reject / invalidate / quarantine all (§13, A4) | `test_missing_minute_rejects_pending…` (U and M) |
| Quarantined penetration flagged; never eligible after reinstatement | `test_quarantined_level_penetration…`, `test_events_during_quarantine_never…` |
| Coarse evidence: untouched → reinstated at availability; proven penetration; ambiguous; no early use; consumed ≠ reinstatable | 5 tests in `test_lifecycle_v11` |
| 15m fractal: BSL/SSL, equal highs, closure, shortened candle, gap candle, zero-trade minutes, all-zero candle, registration at third close | 8 tests in `test_windows` |
| Session / RTH / pre-window registration at close; developing extremes ineligible | 2 tests in `test_windows` |
| DST, early close, reference closed, missing calendar entry, DST-nonexistent time, overlapping sessions | 6 tests in `test_windows` |
| Rollover: strict crossover, tie → earliest expiry, forward-only, unverified volume, no look-ahead volume, ±Δ, missing reference price | 7 selector tests |
| Translation ±Δ with history preserved; consumed not translated; setups expire; no artificial sweep; I1 freeze; I5 off-grid quarantine | 5 engine tests |
| End-to-end: switch at next session open; initial-contract selection; suspension; unverified/mismatched calendar refused | 4 driver tests |
| Eligibility: 09:29 vs 09:30 penetration; 11:28 vs 11:29 confirmation (A7); invalidation ends setup; outside-window consumption | 5 tests |
| Strategy independence (engine state identical under any window) | `test_engine_state_independent_of_strategy_window` |
| Determinism (byte-identical outputs) | `test_determinism_identical_inputs_identical_outputs` |
| Data: fixed-point/pretty prices, ISO timestamps, duplicates, off-grid, misaligned ts, rtype, OHLC, out-of-order, unknown ids, spreads, Parquet | 9 tests |
| Minute classification + audit counts, run lengths, bars outside schedule | `test_minute_classification_and_audit` |
| v1.2: notice-verified halt as closure, halt counter reset, halt/bar conflict, N9 resume with quarantine+freeze, one-sided venue high, mid-minute attribution, cleared-volume mapping/causality/revisions/deletes/ts_ref mismatch, session high/low → coarse bars, APPROVED_V12 complete | `tests/test_v12.py` (11) |
| Every unset policy raises instead of guessing | N3, N4, N5, N6, N7, N11 guard tests |

## Limitations (not validated)

1. **Dataset audited (step 7), but the engine has not been run on it.** See DATA_AUDIT_REPORT.md. An engine run is blocked by the missing contract definitions, unverified calendars and open N-items.
2. **No verified calendars exist yet.** The driver refuses to run on real data until verified CME and NYSE calendar files are supplied (I9).
3. **MNQ contract metadata is unverified.** CME's site blocks automated access, so it needs manual verification or Databento definitions.
4. **Open items N1–N21** in `SPEC_v1.1.md` are unresolved. A real run will stop at the first one it hits. In particular, without an N12 decision every vendor-omitted minute is `UNCERTAIN`, which quarantines all active liquidity.
5. The tests are hand-built boundary cases. They show the code matches the written definitions. They say nothing about the edge of any strategy, and no performance testing has been done (spec Part VI).


---

# Real-data validation — 2026-11-25 (spec v1.2, all approvals incl. N22/N32/N33)

**Data:** Databento GLBX.MDP3 MNQ.FUT (ohlcv-1m, definition, statistics, status), 2022-10-01 → 2026-10-21.
**Configuration:** `config/instruments/MNQ.json`, `config/calendars/CME_GLBX_MNQ.json` and `config/calendars/NYSE.json`, all
verified and signed off by the trader. Halt evidence: `config/evidence/MNQ_status_halts.json` (N22). Policies:
`APPROVED_V12`. No driver overrides.

Command: `python tools/run_engine.py OUT bars_a,bars_b statistics --status-halts config/evidence/MNQ_status_halts.json`

## 1. Run completed

| | |
|---|---|
| Sessions processed | 1,022 (first decision 2022-10-14 per N8/N33) |
| Rollovers | 16, all on the session where the highest-volume contract changed; effective 2 sessions later (published cleared volume, N13b) |
| Suspensions / unauthorized-policy stops | 0 / 0 |
| Liquidity references | 40,914 (SWING_15M 40,490 · EXCHANGE_SESSION 2,022 · RTH 2,010 · PRE_WINDOW 2,010 source labels) |
| Sweep events | 40,613: 28,745 confirmed, 11,868 rejected (11,545 no confirmation, 300 dual-sided, 14 scheduled closure, 9 CME halt) |
| Invalidations | 28,161 three-close, 163 data gap |
| Expired at rollover | 404 |
| Levels still quarantined at end | 0 (481 reinstated by venue session-high/low evidence) |
| Missing-data events | 5 (2 thin overnight minutes; 3 on the old contract during the roll-confirmation lag) |
| Run time | ~100 s engine, ~35 s loading |

## 2. Determinism — PASSED
Two independent runs produced **byte-identical** outputs (SHA-256):
```
references.jsonl     2d136db67e19aa94df85a62cd93f32fb0eefcdc71140857f2bce272d26b9517d
sweep_events.jsonl   a75389073397eedef9d7c05cd3f79ddc1d8020b88bd5fbb690d27f702a7127d2
lifecycle_log.jsonl  501c84304297e41ede00355c951ec39c074684e0ddf99989ae70351f23c728c4
data_quality.jsonl   027fdc612797fb5f9e747e97c30c0b6c456ce27467eae8ddd7dd1d6d64acd2c0
```
CSV exports and provenance were identical as well.

## 3. Independent re-derivation from raw bars — PASSED (0 mismatches)
`tools/validate_run.py` does not use the engine's code. It recomputes from the DBN bars, the calendars and the written rules.

| Check | Checked | Mismatches |
|---|---|---|
| A Penetration H ≥ P+tick / L ≤ P−tick | 40,613 | 0 |
| B No test before availability (no look-ahead) | 40,613 | 0 |
| C Confirmation in clock minutes t..t+3, same segment, strict close; correct rejection reason | 40,313 | 0 |
| D Frozen extreme E = max H / min L over t..q | 28,745 | 0 |
| E Three-close invalidation at the first triple after q (resets per N4/N5/N22) | 28,161 | 0 |
| F One consuming event per consumed level | 40,613 | 0 |
| G Consumption = first penetration since availability / rollover translation | 40,613 | 0 |
| H 15m fractal swings (strict, complete uninterrupted candles) | 40,490 | 0 |
| I Session / RTH / pre-window extremes (complete windows) | 6,026 | 0 |

The 16 windows recomputed but not registered as complete are exactly the 16 windows with missing minutes. The engine registered those as quarantined (N3).

Full outputs: `docs/validation/run_summary_2026-11-25.json`, `docs/validation/independent_validation_2026-11-25.json`,
`docs/validation/provenance_2026-11-25.json`.

## 4. Not covered by the real-data checker (covered by unit tests only)
Quarantine and coarse-evidence reinstatement, N9 resume, I1/I5 rollover failures, dual-sided policy with quarantined
levels, and zero-trade handling. None of the cases in the second group (N9 resume, I1/I5, quarantined dual-sided,
zero-trade) occurred in this dataset. The strategy-layer eligibility (09:30–11:30) is unit-tested and was not exercised here.

## 5. Status
The liquidity engine has **passed validation** on this dataset. Per the spec (Part VI), no strategy, entry, stop,
target or profitability testing has been done. Those need your strategy specification.

The event counts above are descriptive engine output. They are **not evidence of any trading edge**.
