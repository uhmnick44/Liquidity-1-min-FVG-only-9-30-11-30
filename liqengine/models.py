"""State records exposed by the liquidity engine (spec §14).

Liquidity-reference state and sweep-event state are separate objects (§15).
Snapshots handed to the strategy layer are plain dicts (read-only copies).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from .timeutil import minute_to_iso


class Direction(Enum):
    BSL = "BSL"
    SSL = "SSL"


class SourceLabel(Enum):
    EXCHANGE_SESSION = "EXCHANGE_SESSION"   # CME session (category 1)
    RTH = "RTH"                             # category 2
    PRE_WINDOW = "PRE_WINDOW"               # category 3
    SWING_15M = "SWING_15M"                 # category 4


class SweepStatus(Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class RejectReason(Enum):
    NO_CONFIRMATION_WITHIN_WINDOW = "NO_CONFIRMATION_WITHIN_WINDOW"
    DUAL_SIDED = "DUAL_SIDED"
    SCHEDULED_CLOSURE = "SCHEDULED_CLOSURE"               # A5
    UNSCHEDULED_HALT = "UNSCHEDULED_HALT"                 # N22(b), notice-verified halts only
    DATA_GAP = "DATA_GAP"                                 # §13
    PENETRATED_DURING_MISSING_DATA = "PENETRATED_DURING_MISSING_DATA"  # A4 (coarse evidence)
    PROCESSING_SUSPENDED = "PROCESSING_SUSPENDED"          # I2.4


class InvalidationReason(Enum):
    THREE_CLOSES_BEYOND_EXTREME = "THREE_CLOSES_BEYOND_EXTREME"  # §10
    DATA_GAP = "DATA_GAP"                                         # §13


class ExpiryReason(Enum):
    ROLLOVER = "ROLLOVER"                       # §12
    PROCESSING_SUSPENDED = "PROCESSING_SUSPENDED"


class ConsumptionKind(Enum):
    OBSERVED_PENETRATION = "OBSERVED_PENETRATION"
    INFERRED_FROM_COARSE_DATA = "INFERRED_FROM_COARSE_DATA"


@dataclass
class SourceRecord:
    label: SourceLabel
    origin_id: str              # session id / segment id / 15m candle start
    formation_minute: int       # open time of first 1m bar printing the extreme
    availability_minute: int    # first minute at which a bar may test the level
    contract: str
    original_price: Decimal
    flags: list[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "label": self.label.value, "origin_id": self.origin_id,
            "formation_ts": minute_to_iso(self.formation_minute),
            "availability_ts": minute_to_iso(self.availability_minute),
            "contract": self.contract, "original_price": str(self.original_price),
            "flags": list(self.flags),
        }


@dataclass
class QuarantineReason:
    reason_id: str
    kind: str                         # DATA_GAP / INCOMPLETE_SOURCE_WINDOW / ROLLOVER_REFERENCE_UNAVAILABLE / INCOMPATIBLE_GRID / COLLISION
    since_minute: int
    contract: str | None
    unresolved_minutes: set[int] | None   # None => not resolvable by price evidence
    resolved_minute: int | None = None
    disproven: bool = False
    all_minutes: set[int] = field(default_factory=set)  # every minute ever attached to this reason

    @property
    def resolved(self) -> bool:
        return self.resolved_minute is not None


@dataclass
class LiquidityReference:
    ref_id: str
    direction: Direction
    instrument: str
    contract: str                     # contract whose coordinates `price` is in
    price: Decimal
    ticks: int
    sources: list[SourceRecord]
    consumed: bool = False
    consumed_minute: int | None = None
    consumption_kind: ConsumptionKind | None = None
    consuming_event_id: str | None = None
    quarantined: bool = False
    quarantine_reasons: dict[str, QuarantineReason] = field(default_factory=dict)
    coordinates_valid: bool = True
    price_history: list[tuple[int, str, Decimal]] = field(default_factory=list)
    reinstatements: list[int] = field(default_factory=list)
    dq_flags: list[str] = field(default_factory=list)

    @property
    def testable(self) -> bool:
        return not self.consumed and self.coordinates_valid

    @property
    def eligible(self) -> bool:
        return not self.consumed and not self.quarantined and self.coordinates_valid

    def price_in(self, contract: str) -> Decimal | None:
        for _, c, p in reversed(self.price_history):
            if c == contract:
                return p
        return None

    def to_dict(self):
        return {
            "ref_id": self.ref_id, "direction": self.direction.value, "instrument": self.instrument,
            "contract": self.contract, "price": str(self.price),
            "source_labels": sorted({s.label.value for s in self.sources}),
            "sources": [s.to_dict() for s in self.sources],
            "formation_ts": minute_to_iso(min(s.formation_minute for s in self.sources)),
            "availability_ts": minute_to_iso(min(s.availability_minute for s in self.sources)),
            "consumed": self.consumed, "consumed_ts": minute_to_iso(self.consumed_minute),
            "consumption_kind": self.consumption_kind.value if self.consumption_kind else None,
            "consuming_event_id": self.consuming_event_id,
            "quarantined": self.quarantined,
            "quarantine_reasons": [
                {"reason_id": q.reason_id, "kind": q.kind, "since": minute_to_iso(q.since_minute),
                 "resolved_ts": minute_to_iso(q.resolved_minute), "disproven": q.disproven,
                 "unresolved_minutes": None if q.unresolved_minutes is None else len(q.unresolved_minutes)}
                for q in self.quarantine_reasons.values()
            ],
            "coordinates_valid": self.coordinates_valid,
            "price_history": [(minute_to_iso(m), c, str(p)) for m, c, p in self.price_history],
            "reinstated_ts": [minute_to_iso(m) for m in self.reinstatements],
            "dq_flags": list(self.dq_flags),
        }


@dataclass
class SweepEvent:
    event_id: str
    ref_id: str
    direction: Direction
    instrument: str
    contract: str
    level_price: Decimal
    level_ticks: int
    source_labels: tuple[str, ...]
    penetration_minute: int | None      # bar open time t
    deadline_minute: int | None         # t + 3
    status: SweepStatus = SweepStatus.PENDING
    reject_reason: RejectReason | None = None
    rejected_minute: int | None = None  # bar open of the minute at whose close rejection happened
    confirmation_minute: int | None = None  # q (bar open); observable at q + 1
    running_extreme: int | None = None
    extreme_ticks: int | None = None    # frozen E
    extreme_price: Decimal | None = None
    invalidation_count: int = 0
    invalidated: bool = False
    invalidation_reason: InvalidationReason | None = None
    invalidation_minute: int | None = None  # bar open of third qualifying close
    expired: bool = False
    expiry_reason: ExpiryReason | None = None
    expiry_minute: int | None = None    # effective time (minute boundary)
    reference_quarantined_at_penetration: bool = False
    dual_sided: bool = False
    dq_flags: list[str] = field(default_factory=list)

    @property
    def tracking(self) -> bool:
        return self.status is SweepStatus.CONFIRMED and not self.invalidated and not self.expired

    def to_dict(self):
        obs = lambda m: minute_to_iso(None if m is None else m + 1)  # noqa: E731
        return {
            "event_id": self.event_id, "ref_id": self.ref_id, "direction": self.direction.value,
            "instrument": self.instrument, "contract": self.contract,
            "level_price": str(self.level_price), "source_labels": list(self.source_labels),
            "penetration_ts": minute_to_iso(self.penetration_minute),
            "penetration_observable_ts": obs(self.penetration_minute),
            "status": self.status.value,
            "reject_reason": self.reject_reason.value if self.reject_reason else None,
            "rejected_ts": minute_to_iso(self.rejected_minute),
            "confirmation_ts": minute_to_iso(self.confirmation_minute),
            "confirmation_observable_ts": obs(self.confirmation_minute),
            "sweep_extreme": None if self.extreme_price is None else str(self.extreme_price),
            "invalidation_count": self.invalidation_count,
            "invalidated": self.invalidated,
            "invalidation_reason": self.invalidation_reason.value if self.invalidation_reason else None,
            "invalidation_ts": minute_to_iso(self.invalidation_minute),
            "invalidation_observable_ts": obs(self.invalidation_minute),
            "expired": self.expired,
            "expiry_reason": self.expiry_reason.value if self.expiry_reason else None,
            "expiry_ts": minute_to_iso(self.expiry_minute),
            "reference_quarantined_at_penetration": self.reference_quarantined_at_penetration,
            "dual_sided": self.dual_sided, "dq_flags": list(self.dq_flags),
        }


@dataclass(frozen=True)
class CoarseBar:
    """Verified coarser-resolution observation (A4 step 2, N18). Prices are
    actual prints on `contract`. Usable only from `available_minute` onward.

    high/low may be None when the source states only one side (e.g. a venue
    session-high statistic). [start_minute, end_minute) is the span of minutes
    the extreme is guaranteed to bound. attribution_end_minute (>= end_minute)
    is the span within which the extreme is guaranteed to have printed. A
    running statistic stamped mid-minute m bounds minutes < m and printed by
    minute m, so end = m and attribution_end = m + 1.
    """
    contract: str
    start_minute: int
    end_minute: int      # exclusive
    high: Decimal | None
    low: Decimal | None
    available_minute: int
    source: str
    attribution_end_minute: int | None = None

    @property
    def attribution_end(self) -> int:
        return self.end_minute if self.attribution_end_minute is None else self.attribution_end_minute
