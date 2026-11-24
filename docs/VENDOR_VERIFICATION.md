# Vendor and Exchange Verification Log (spec Part VI step 6)

Retrieved 2026-11-24 from official Databento documentation (HTTP 200). Quotes are verbatim.

| Fact | Quote | Source |
|---|---|---|
| OHLCV timestamp = interval start | "The `ts_event` timestamp marks the start of each interval." / "The inclusive start time of the bar aggregation period based on the ts_recv from trade messages" | https://databento.com/docs/schemas/ohlcv |
| No bar when no trade | "If no trade occurs within the interval, no record is printed." | same |
| Built from trades | "aggregated from trades at 1-second, 1-minute, 1-hour, or 1-day intervals" | same |
| Price encoding | "every 1 unit corresponds to 1e-9" | same |
| CSV price scaling | `pretty_px` "If prices should be formatted to the correct scale (using the fixed-precision scalar 1e-9). Only applicable for 'csv' or 'json' encodings." | https://databento.com/docs/api-reference-historical/basics/encodings |
| Daily bars are UTC-dated | "Our ohlcv-1d schema is based on UTC dates." | https://databento.com/docs/schemas/ohlcv |
| Vendor bars vs official | "this may differ from official settlement prices and volumes published by the venue, which could include block trades…" | same |
| Cleared volume | stat_type 6: "The number of contracts cleared for an instrument on the previous trading date." | https://databento.com/docs/schemas/statistics |
| Venue session high/low | stat_type 5 "The highest trade price of an instrument during the trading session."; stat_type 4 likewise for low | same |
| Contract expiration field | `expiration`: "The last eligible trade time … May only have date precision depending on the publisher." | https://databento.com/docs/schemas/instrument-definitions |
| Tick size field | `min_price_increment`: "The minimum constant tick for the instrument in units of 1e-9" | same |
| Futures vs spreads | `security_type`: "The type of the instrument, e.g. FUT for future or future spread."; `instrument_class` documented | same |
| Dataset condition | values include "available : the data is available with no known issues" and "degraded : the data is available, but there may be missing data or other correctness issues" | https://databento.com/docs/api-reference-historical/metadata/metadata-get-dataset-condition |

## Implications implemented
- Bars are labelled by open time; observable at open + 1 minute (`data.py`, `eligibility.py`).
- A missing minute is never assumed to be zero-trade (`MinuteStatus.UNCERTAIN` by default).
- Prices parsed exactly from fixed-point integers or decimal strings; floats refused.

## Not verified
- **CME MNQ contract specifications** (tick size, multiplier, cycle, hours, last trade rule): cmegroup.com refused automated requests (HTTP 403 from the site). Must be checked by a human; per-contract tick size should be taken from Databento definitions and cross-checked.
- **CME/NYSE holiday and early-close schedules**: not yet assembled. Calendars must be built from official notices and marked `verified: true` by a human. www.nyse.com is reachable from this environment if you want me to draft the reference calendar from it.
- **Vendor documentation is not proof of a dataset's integrity** (I8): the audit must be run on the actual files.
