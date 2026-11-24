# Validation Report — liquidity engine v0.1.0 against spec v1.1

Date: 2026-11-24. Environment: Python 3.13.18, pytest 9.1.1, pyarrow 26.0.1, Linux.

## Result actually observed

```
$ python3 -m pytest tests -q
105 passed in 1.56s   (incl. DBN round-trip on a real MNQH3 file via LIQ_DBN_FIXTURE;
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
| Every unset policy raises instead of guessing | N3, N4, N5, N6, N7, N11 guard tests |

## Limitations (not validated)

1. **Dataset audited (step 7), but the engine has not been run on it.** See DATA_AUDIT_REPORT.md. An engine run is blocked by the missing contract definitions, unverified calendars and open N-items.
2. **No verified calendars exist yet.** The driver refuses to run on real data until verified CME and NYSE calendar files are supplied (I9).
3. **MNQ contract metadata is unverified.** CME's site blocks automated access, so it needs manual verification or Databento definitions.
4. **Open items N1–N21** in `SPEC_v1.1.md` are unresolved. A real run will stop at the first one it hits. In particular, without an N12 decision every vendor-omitted minute is `UNCERTAIN`, which quarantines all active liquidity.
5. The tests are hand-built boundary cases. They show the code matches the written definitions. They say nothing about the edge of any strategy, and no performance testing has been done (spec Part VI).
