"""Behaviour that specification v1.1 does NOT determine.

Every field defaults to None (= not authorized). The engine raises
UnauthorizedPolicyError the first time it reaches a situation that needs an
unset policy, instead of silently choosing. Each item is documented in
docs/SPEC_v1.1.md, section "Open items requiring authorization".
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum


class UnauthorizedPolicyError(RuntimeError):
    def __init__(self, item: str, field: str, context: str):
        super().__init__(
            f"{item}: behaviour not authorized by spec v1.1 (policy '{field}' unset). "
            f"Context: {context}. See docs/SPEC_v1.1.md open items."
        )
        self.item, self.field, self.context = item, field, context


class IncompleteWindowLiquidity(Enum):
    """N3: session/RTH/pre-window extreme computed over a window containing
    missing or uncertain minutes."""
    QUARANTINE = "QUARANTINE"          # register, quarantined (reinstatable under I6)
    DO_NOT_REGISTER = "DO_NOT_REGISTER"


class CounterAcrossClosure(Enum):
    """N4: invalidation close-counter across a scheduled closure."""
    CONTINUE = "CONTINUE"
    RESET = "RESET"


class CounterAcrossZeroTrade(Enum):
    """N5: invalidation close-counter across a verified zero-trade minute
    (no close exists)."""
    UNCHANGED = "UNCHANGED"
    RESET = "RESET"


class PreWindowWhenReferenceClosed(Enum):
    """N6: pre-window liquidity on a session whose reference market is closed."""
    DO_NOT_GENERATE = "DO_NOT_GENERATE"
    GENERATE_TO_NOMINAL_TIME = "GENERATE_TO_NOMINAL_TIME"


class ExchangeSessionUnit(Enum):
    """N7: what an exchange ("CME") session is when a trade date contains a
    scheduled intra-session halt."""
    TRADE_DATE = "TRADE_DATE"
    SEGMENT = "SEGMENT"


class InitialContract(Enum):
    """N8: how the first active contract is chosen."""
    EXPLICIT = "EXPLICIT"
    HIGHEST_VERIFIED_VOLUME = "HIGHEST_VERIFIED_VOLUME"


class QuarantineCollision(Enum):
    """N10: a newly, independently formed level has the same price and
    direction as an existing quarantined level."""
    SEPARATE = "SEPARATE"
    MERGE_KEEP_QUARANTINE = "MERGE_KEEP_QUARANTINE"
    MERGE_LIFT_QUARANTINE = "MERGE_LIFT_QUARANTINE"


@dataclass(frozen=True)
class PolicyConfig:
    incomplete_window_liquidity: IncompleteWindowLiquidity | None = None
    counter_across_closure: CounterAcrossClosure | None = None
    counter_across_zero_trade: CounterAcrossZeroTrade | None = None
    pre_window_when_reference_closed: PreWindowWhenReferenceClosed | None = None
    exchange_session_unit: ExchangeSessionUnit | None = None
    initial_contract: InitialContract | None = None
    quarantine_collision: QuarantineCollision | None = None
    # N11: do quarantined (but unconsumed) levels count toward dual-sided detection?
    dual_sided_includes_quarantined: bool | None = None

    _ITEMS = {
        "incomplete_window_liquidity": "N3",
        "counter_across_closure": "N4",
        "counter_across_zero_trade": "N5",
        "pre_window_when_reference_closed": "N6",
        "exchange_session_unit": "N7",
        "initial_contract": "N8",
        "quarantine_collision": "N10",
        "dual_sided_includes_quarantined": "N11",
    }

    def require(self, name: str, context: str):
        value = getattr(self, name)
        if value is None:
            raise UnauthorizedPolicyError(self._ITEMS[name], name, context)
        return value

    def as_dict(self) -> dict:
        out = {}
        for f in fields(self):
            v = getattr(self, f.name)
            out[f.name] = v.value if isinstance(v, Enum) else v
        return out


# Spec v1.2: all open items resolved with the recommendations in docs/OPEN_ITEMS.md,
# approved by the trader on 2026-11-24. Use this for production runs.
APPROVED_V12 = PolicyConfig(
    incomplete_window_liquidity=IncompleteWindowLiquidity.QUARANTINE,      # N3
    counter_across_closure=CounterAcrossClosure.RESET,                    # N4
    counter_across_zero_trade=CounterAcrossZeroTrade.RESET,               # N5
    pre_window_when_reference_closed=PreWindowWhenReferenceClosed.DO_NOT_GENERATE,  # N6
    exchange_session_unit=ExchangeSessionUnit.TRADE_DATE,                 # N7
    initial_contract=InitialContract.HIGHEST_VERIFIED_VOLUME,             # N8
    quarantine_collision=QuarantineCollision.SEPARATE,                    # N10
    dual_sided_includes_quarantined=True,                                 # N11
)
