"""Dataset integrity audit (A3, I8). Run before any backtest.

Reports, per contract: scheduled minutes, observed bars, and missing
scheduled minutes split into verified zero-trade / confirmed missing /
uncertain. It also reports run lengths, the distribution of missing minutes
by local hour and by session, and bars printed outside scheduled hours (a
calendar/data conflict). Nothing is repaired.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from .calendars import ExchangeCalendar
from .data import BarStore, LoadReport, MinuteEvidence, MinuteStatus
from .timeutil import minute_local, minute_to_iso


def audit(store: BarStore, exchange: ExchangeCalendar, evidence: MinuteEvidence | None,
          contracts: list[str] | None = None, local_tz: str = "America/New_York",
          load_reports: list[LoadReport] | None = None) -> dict:
    contracts = contracts or store.contracts()
    out = {"exchange_calendar": f"{exchange.calendar_id}@{exchange.version}",
           "calendar_verified": exchange.verified,
           "evidence_source": None if evidence is None else evidence.source, "contracts": {}}
    if load_reports:
        out["load"] = [{
            "rows": r.rows, "out_of_order_rows": r.out_of_order,
            "unknown_instrument_ids": {str(k): v for k, v in r.unknown_instrument_ids.items()},
            "rejected_non_outright": r.rejected_non_outright, "per_contract_rows": r.per_contract_rows,
        } for r in load_reports]
    for c in contracts:
        bars = store.bars.get(c, {})
        if not bars:
            out["contracts"][c] = {"bars": 0}
            continue
        first, last = min(bars), max(bars)
        counts = Counter()
        by_hour = Counter()
        by_session = defaultdict(Counter)
        runs = Counter()
        run_len = 0
        for s in exchange.sessions:
            if s.end <= first or s.start > last:
                continue
            for a, b in s.segments:
                run_len = 0
                for m in range(a, b):  # full scheduled segments of every session the data touches
                    st = store.classify(c, m, evidence)
                    counts[st.value] += 1
                    if st is MinuteStatus.OBSERVED:
                        if run_len:
                            runs[run_len] += 1
                        run_len = 0
                        continue
                    by_session[s.session_id][st.value] += 1
                    by_hour[minute_local(m, local_tz).hour] += 1
                    run_len += 1
                if run_len:
                    runs[run_len] += 1
        outside = [m for m in bars if exchange.session_at(m) is None]
        out["contracts"][c] = {
            "bars": len(bars), "first_bar": minute_to_iso(first), "last_bar": minute_to_iso(last),
            "scheduled_minute_status": dict(counts),
            "non_observed_run_lengths": {str(k): v for k, v in sorted(runs.items())},
            "non_observed_by_local_hour": {str(k): v for k, v in sorted(by_hour.items())},
            "sessions_with_non_observed": len(by_session),
            "non_observed_by_session": {k: dict(v) for k, v in sorted(by_session.items())},
            "bars_outside_scheduled_hours": len(outside),
            "bars_outside_scheduled_hours_sample": [minute_to_iso(m) for m in sorted(outside)[:20]],
        }
    return out


def audit_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True)
