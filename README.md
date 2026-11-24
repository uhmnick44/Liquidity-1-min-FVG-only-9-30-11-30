# Mathematical Liquidity Engine

A deterministic engine that identifies, tracks, confirms, consumes, invalidates and quarantines
liquidity sweeps. It contains no strategy logic: no entries, stops, targets or sizing.

- Spec: [`docs/SPEC_v1.1.md`](docs/SPEC_v1.1.md) (consolidated), with the frozen original in [`docs/SPEC.md`](docs/SPEC.md)
- Vendor verification: [`docs/VENDOR_VERIFICATION.md`](docs/VENDOR_VERIFICATION.md)
- Validation status: [`docs/VALIDATION_REPORT.md`](docs/VALIDATION_REPORT.md)

```
pip install -e .[test,parquet]
python -m pytest
python -m liqengine.cli audit --instrument cfg/instrument.json --calendar cfg/exchange.json data/*.csv
```

The driver refuses unverified instrument and calendar files. Behaviour the spec doesn't determine
raises `UnauthorizedPolicyError` until you set the corresponding `PolicyConfig` field (see the open
items N1–N21).

Liquidity formed before the first session of the dataset cannot be identified, and the engine makes
no attempt to do so.
