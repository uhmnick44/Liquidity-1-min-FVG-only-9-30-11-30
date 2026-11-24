"""Versioned, explicitly verified calendars (I9).

Calendars are data, not code: every scheduled trading segment and every
reference-market day is listed explicitly. Nothing is inferred. A date that
is not listed raises CalendarUnavailable, and an unverified calendar is
refused by the driver.
"""
from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path

from .timeutil import dt_to_minute, local_to_minute, parse_utc


class CalendarUnavailable(LookupError):
    pass


class CalendarError(ValueError):
    pass


@dataclass(frozen=True)
class ExchangeSession:
    session_id: str                      # exchange trade date, ISO yyyy-mm-dd
    segments: tuple[tuple[int, int], ...]  # [open, close) UTC minutes, ascending

    @property
    def start(self) -> int:
        return self.segments[0][0]

    @property
    def end(self) -> int:
        return self.segments[-1][1]

    @property
    def trade_date(self) -> date:
        return date.fromisoformat(self.session_id)

    def minutes(self):
        for a, b in self.segments:
            yield from range(a, b)

    def is_scheduled(self, m: int) -> bool:
        return any(a <= m < b for a, b in self.segments)

    def segment_of(self, m: int) -> int | None:
        for i, (a, b) in enumerate(self.segments):
            if a <= m < b:
                return i
        return None


@dataclass
class ExchangeCalendar:
    calendar_id: str
    version: str
    verified: bool
    source: str
    sessions: list[ExchangeSession]
    verified_by: str | None = None
    _starts: list[int] = field(default_factory=list, repr=False)

    def __post_init__(self):
        self.sessions = sorted(self.sessions, key=lambda s: s.start)
        prev_end = None
        ids = set()
        for s in self.sessions:
            if s.session_id in ids:
                raise CalendarError(f"duplicate session {s.session_id}")
            ids.add(s.session_id)
            if not s.segments:
                raise CalendarError(f"session {s.session_id} has no segments")
            for a, b in s.segments:
                if not a < b:
                    raise CalendarError(f"empty/inverted segment in {s.session_id}")
            for (a1, b1), (a2, b2) in zip(s.segments, s.segments[1:]):
                if not b1 < a2:
                    raise CalendarError(f"overlapping/adjacent segments in {s.session_id}; merge them")
            if prev_end is not None and s.start < prev_end:
                raise CalendarError(f"session {s.session_id} overlaps previous session")
            prev_end = s.end
        self._starts = [s.start for s in self.sessions]

    def session_index(self, session_id: str) -> int:
        for i, s in enumerate(self.sessions):
            if s.session_id == session_id:
                return i
        raise CalendarUnavailable(f"{self.calendar_id}: session {session_id} not in calendar")

    def session_at(self, m: int) -> ExchangeSession | None:
        i = bisect_right(self._starts, m) - 1
        if i >= 0 and self.sessions[i].is_scheduled(m):
            return self.sessions[i]
        return None

    def covers(self, m: int) -> bool:
        return bool(self.sessions) and self.sessions[0].start <= m < self.sessions[-1].end

    @classmethod
    def from_dict(cls, d: dict) -> "ExchangeCalendar":
        sessions = []
        for s in d["sessions"]:
            segs = tuple(
                (dt_to_minute(parse_utc(a)), dt_to_minute(parse_utc(b))) for a, b in s["segments"]
            )
            sessions.append(ExchangeSession(s["session_id"], segs))
        return cls(
            calendar_id=d["calendar_id"], version=d["version"], verified=bool(d["verified"]),
            source=d.get("source", ""), verified_by=d.get("verified_by"), sessions=sessions,
        )

    @classmethod
    def load(cls, path: str | Path) -> "ExchangeCalendar":
        return cls.from_dict(json.loads(Path(path).read_text()))


@dataclass(frozen=True)
class ReferenceDay:
    day: date
    open: time | None   # None => reference market closed
    close: time | None

    @property
    def closed(self) -> bool:
        return self.open is None


@dataclass
class ReferenceCalendar:
    """Reference-market (e.g. NYSE cash) calendar, separate from the exchange calendar."""
    calendar_id: str
    version: str
    verified: bool
    source: str
    tz: str
    days: dict[date, ReferenceDay]
    verified_by: str | None = None

    def day(self, d: date) -> ReferenceDay:
        if d not in self.days:
            raise CalendarUnavailable(f"{self.calendar_id}: no entry for {d.isoformat()}")
        return self.days[d]

    def window_minutes(self, d: date) -> tuple[int, int] | None:
        rd = self.day(d)
        if rd.closed:
            return None
        return local_to_minute(d, rd.open, self.tz), local_to_minute(d, rd.close, self.tz)

    @classmethod
    def from_dict(cls, d: dict) -> "ReferenceCalendar":
        days = {}
        for e in d["days"]:
            dd = date.fromisoformat(e["date"])
            if e.get("closed"):
                days[dd] = ReferenceDay(dd, None, None)
            else:
                days[dd] = ReferenceDay(dd, time.fromisoformat(e["open"]), time.fromisoformat(e["close"]))
        return cls(
            calendar_id=d["calendar_id"], version=d["version"], verified=bool(d["verified"]),
            source=d.get("source", ""), tz=d["tz"], days=days, verified_by=d.get("verified_by"),
        )

    @classmethod
    def load(cls, path: str | Path) -> "ReferenceCalendar":
        return cls.from_dict(json.loads(Path(path).read_text()))


@dataclass(frozen=True)
class SessionPlan:
    """Everything the engine needs to know about one exchange session."""
    session: ExchangeSession
    rth: tuple[int, int] | None           # [start, end) UTC minutes
    pre_window: tuple[int, int] | None
    reference_closed: bool
    flags: tuple[str, ...] = ()


def plan_session(
    session: ExchangeSession,
    reference: ReferenceCalendar | None,
    pre_window_policy,  # zero-arg callable -> policy value; only called when reference closed
    nominal_pre_window_end: time | None,
    reference_tz: str | None,
) -> SessionPlan:
    """Derive RTH and pre-window bounds for one exchange session.

    - RTH (A8/A9): reference open .. min(reference close, exchange session end).
    - Pre-window: exchange session start .. reference open.
    - Reference closed (A8): no RTH; pre-window per policy N6.
    pre_window_policy() is only called when the reference is closed, so an
    unset N6 policy only matters on such sessions.
    """
    if reference is None:
        return SessionPlan(session, None, None, reference_closed=False, flags=("NO_REFERENCE_MARKET",))
    d = session.trade_date
    win = reference.window_minutes(d)  # raises CalendarUnavailable if missing
    flags: list[str] = []
    if win is None:
        pre = None
        if pre_window_policy() == "GENERATE_TO_NOMINAL_TIME":
            if nominal_pre_window_end is None or reference_tz is None:
                raise CalendarError("nominal pre-window end not configured")
            end = local_to_minute(d, nominal_pre_window_end, reference_tz)
            pre = (session.start, end)
            flags.append("PRE_WINDOW_NOMINAL_END")
        return SessionPlan(session, None, pre, reference_closed=True, flags=tuple(flags) + ("REFERENCE_CLOSED",))
    r_open, r_close = win
    if not (session.start <= r_open < session.end):
        raise CalendarError(
            f"session {session.session_id}: reference open outside exchange session; calendars inconsistent"
        )
    end = min(r_close, session.end)
    if end != r_close:
        flags.append("RTH_TRUNCATED_BY_EXCHANGE_CLOSE")
    return SessionPlan(session, (r_open, end), (session.start, r_open), reference_closed=False, flags=tuple(flags))
