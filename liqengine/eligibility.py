"""Strategy-layer trading-window eligibility (A6, A7, A16, §11).

This module only READS engine outputs. It never changes liquidity state, and
the engine never imports it. Entries, stops, targets and sizing are out of
scope.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from .calendars import ExchangeCalendar
from .models import SweepEvent, SweepStatus
from .timeutil import local_to_minute, minute_local, minute_to_iso


@dataclass(frozen=True)
class TradingWindow:
    start: time
    end: time
    tz: str

    def bounds_for(self, minute: int) -> tuple[int, int]:
        d = minute_local(minute, self.tz).date()
        return local_to_minute(d, self.start, self.tz), local_to_minute(d, self.end, self.tz)


CURRENT_STRATEGY_WINDOW = TradingWindow(time(9, 30), time(11, 30), "America/New_York")


@dataclass(frozen=True)
class SetupView:
    event_id: str
    eligible: bool
    reason: str
    active_from: int | None = None     # first minute an entry decision may use it
    active_until: int | None = None    # exclusive
    end_reason: str | None = None

    def to_dict(self):
        return {"event_id": self.event_id, "eligible": self.eligible, "reason": self.reason,
                "active_from": minute_to_iso(self.active_from), "active_until": minute_to_iso(self.active_until),
                "end_reason": self.end_reason}


def evaluate_setup(e: SweepEvent, window: TradingWindow, exchange: ExchangeCalendar) -> SetupView:
    if e.status is not SweepStatus.CONFIRMED:
        return SetupView(e.event_id, False, f"NOT_CONFIRMED:{e.status.value}")
    if e.dual_sided:
        return SetupView(e.event_id, False, "DUAL_SIDED")
    if e.reference_quarantined_at_penetration:
        return SetupView(e.event_id, False, "REFERENCE_QUARANTINED")
    w0, w1 = window.bounds_for(e.penetration_minute)
    if not (w0 <= e.penetration_minute < w1):
        return SetupView(e.event_id, False, "PENETRATION_OUTSIDE_WINDOW")
    observable = e.confirmation_minute + 1
    if not observable < w1:
        return SetupView(e.event_id, False, "CONFIRMATION_OBSERVABLE_AT_OR_AFTER_WINDOW_END")
    if exchange.session_at(observable) is None:
        return SetupView(e.event_id, False, "EXCHANGE_CLOSED_AT_CONFIRMATION")
    ends = [(w1, "WINDOW_EXPIRED")]
    if e.invalidated:
        ends.append((e.invalidation_minute + 1, f"INVALIDATED:{e.invalidation_reason.value}"))
    if e.expired:
        ends.append((e.expiry_minute, f"EXPIRED:{e.expiry_reason.value}"))
    until, why = min(ends)
    return SetupView(e.event_id, True, "ELIGIBLE", observable, until, why)
