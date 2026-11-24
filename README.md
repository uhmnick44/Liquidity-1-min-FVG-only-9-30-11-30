# Mathematical Liquidity Engine

A deterministic engine that identifies, tracks, confirms, consumes, invalidates and quarantines
liquidity sweeps. It contains no strategy logic: no entries, stops, targets or sizing.

- Spec: [`docs/SPEC_v1.2.md`](docs/SPEC_v1.2.md) (current decisions) on top of [`docs/SPEC_v1.1.md`](docs/SPEC_v1.1.md), with the frozen original in [`docs/SPEC.md`](docs/SPEC.md)
- Vendor verification: [`docs/VENDOR_VERIFICATION.md`](docs/VENDOR_VERIFICATION.md)
- Validation status: [`docs/VALIDATION_REPORT.md`](docs/VALIDATION_REPORT.md)

```
pip install -e .[test,parquet]
python -m pytest
python -m liqengine.cli audit --instrument cfg/instrument.json --calendar cfg/exchange.json data/*.csv
```

The driver refuses unverified instrument and calendar files. Production runs use
`liqengine.policies.APPROVED_V12` (all approved decisions). Any `PolicyConfig` field left unset still
raises `UnauthorizedPolicyError` instead of guessing.

Liquidity formed before the first session of the dataset cannot be identified, and the engine makes
no attempt to do so.
