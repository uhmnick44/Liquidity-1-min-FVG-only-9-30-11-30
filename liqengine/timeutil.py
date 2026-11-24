"""Time is represented internally as integer UTC minutes since the Unix epoch.

A 1-minute bar labelled with minute m covers [m, m+1) and its information
becomes observable at m+1 (its close).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NS_PER_MINUTE = 60_000_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def dt_to_minute(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("naive datetime refused; timestamps must be timezone-aware")
    delta = dt.astimezone(timezone.utc) - EPOCH
    secs = delta.days * 86400 + delta.seconds
    if secs % 60 or delta.microseconds:
        raise ValueError(f"{dt.isoformat()} is not minute-aligned")
    return secs // 60


def minute_to_dt(m: int) -> datetime:
    return EPOCH + timedelta(minutes=m)


def minute_to_iso(m: int | None) -> str | None:
    return None if m is None else minute_to_dt(m).strftime("%Y-%m-%dT%H:%M:%SZ")


def ns_to_minute(ns: int) -> int:
    if ns % NS_PER_MINUTE:
        raise ValueError(f"timestamp {ns} ns is not minute-aligned")
    return ns // NS_PER_MINUTE


def parse_utc(s: str) -> datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp {s!r} has no timezone")
    return dt


def local_to_minute(d: date, t: time, tz: str) -> int:
    """Convert a local wall-clock time on date d in tz to a UTC minute (DST-aware)."""
    zi = ZoneInfo(tz)
    local = datetime.combine(d, t, tzinfo=zi)
    # Reject nonexistent/ambiguous wall times rather than guessing.
    roundtrip = local.astimezone(timezone.utc).astimezone(zi)
    if roundtrip.replace(tzinfo=None) != local.replace(tzinfo=None):
        raise ValueError(f"{d} {t} does not exist in {tz} (DST gap)")
    other = datetime.combine(d, t, tzinfo=zi).replace(fold=1)
    if other.utcoffset() != local.utcoffset():
        raise ValueError(f"{d} {t} is ambiguous in {tz} (DST overlap)")
    return dt_to_minute(local)


def minute_local(m: int, tz: str) -> datetime:
    return minute_to_dt(m).astimezone(ZoneInfo(tz))
