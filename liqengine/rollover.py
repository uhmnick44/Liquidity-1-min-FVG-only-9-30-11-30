"""Futures contract selection and rollover (A12, I1-I5, I10).

Decisions use only completed-session information available at the time of
the decision. A decision taken at the close of session D takes effect at the
open of the next scheduled session (A12.2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .calendars import ExchangeSession
from .data import BarStore, MinuteEvidence, MinuteStatus
from .instrument import Contract, InstrumentConfig


@dataclass(frozen=True)
class SessionVolume:
    contract: str
    session_id: str
    volume: int | None        # None => not verifiable
    available_minute: int     # when this figure became known
    source: str


@dataclass
class RolloverDecision:
    decided_minute: int
    crossover_session_id: str
    from_contract: str
    to_contract: str
    delta: Decimal | None         # None => I1 (reference price unavailable)
    p_old: Decimal | None
    p_new: Decimal | None
    reference_minute: int
    flags: list[str] = field(default_factory=list)


def minute_volume_for_session(store: BarStore, evidence: MinuteEvidence | None, contract: str,
                              session: ExchangeSession) -> SessionVolume:
    """Completed-session volume from 1m bars. Verifiable only if every
    scheduled minute is OBSERVED or VERIFIED_ZERO_TRADE (spec I2: never
    estimate)."""
    total = 0
    for m in session.minutes():
        st = store.classify(contract, m, evidence)
        if st is MinuteStatus.OBSERVED:
            total += store.get(contract, m).volume
        elif st is not MinuteStatus.VERIFIED_ZERO_TRADE:
            return SessionVolume(contract, session.session_id, None, session.end, "ohlcv-1m-sum")
    return SessionVolume(contract, session.session_id, total, session.end, "ohlcv-1m-sum")


def traded_in(store: BarStore, symbol: str, session: ExchangeSession) -> bool:
    b = store.bars.get(symbol, {})
    return any(m in b for m in session.minutes())


def last_common_minute(store: BarStore, a: str, b: str, session: ExchangeSession) -> int | None:
    ba, bb = store.bars.get(a, {}), store.bars.get(b, {})
    for seg_start, seg_end in reversed(session.segments):
        for m in range(seg_end - 1, seg_start - 1, -1):
            if m in ba and m in bb:
                return m
    return None


class ContractSelector:
    def __init__(self, instrument: InstrumentConfig, current: str):
        if not instrument.has_expirations:
            raise ValueError("instruments without expirations bypass rollover logic")
        self.instrument = instrument
        self.current = current
        self.replaced: list[str] = []
        self.uncertain: list[dict] = []

    def _eligible_successors(self, at_minute: int) -> list[Contract]:
        cur = self.instrument.contract(self.current)
        out = []
        for c in self.instrument.contracts:
            if (c.is_outright and c.in_expiration_cycle and c.expiration > cur.expiration
                    and c.expiration > at_minute and c.symbol not in self.replaced
                    and c.symbol != self.current
                    and (c.activation is None or c.activation <= at_minute)):
                out.append(c)
        return sorted(out, key=lambda c: (c.expiration, c.symbol))

    def successors_for_session(self, session: ExchangeSession, at_minute: int, store: BarStore) -> list[Contract]:
        """N34 (approved 2026-11-25): a successor with zero observed trades (no 1m bar) in the evaluated
        session is not eligible for that session's decision."""
        return [c for c in self._eligible_successors(at_minute) if traded_in(store, c.symbol, session)]

    def current_eligible_at(self, minute: int) -> bool:
        return self.instrument.contract(self.current).expiration > minute

    def evaluate(self, session: ExchangeSession, volumes: dict[str, SessionVolume],
                 store: BarStore, evidence: MinuteEvidence | None, decided_minute: int) -> RolloverDecision | None:
        """Evaluate the crossover rule for completed session `session`.

        `volumes` must only contain figures with available_minute <= decided_minute.
        """
        for v in volumes.values():
            if v.available_minute > decided_minute:
                raise ValueError("volume figure not yet available (look-ahead)")
        cur_v = volumes.get(self.current)
        succ = self.successors_for_session(session, decided_minute, store)
        if not succ:
            return None
        if cur_v is None or cur_v.volume is None:
            self.uncertain.append({"session_id": session.session_id, "reason": "CURRENT_VOLUME_UNVERIFIED"})
            return None
        unverified = [c.symbol for c in succ if volumes.get(c.symbol) is None or volumes[c.symbol].volume is None]
        if unverified:
            # I3: suspend the rollover decision when eligibility cannot be verified
            self.uncertain.append({"session_id": session.session_id, "reason": "SUCCESSOR_VOLUME_UNVERIFIED",
                                   "contracts": unverified})
            return None
        best_vol = max(volumes[c.symbol].volume for c in succ)
        best = [c for c in succ if volumes[c.symbol].volume == best_vol]
        chosen = min(best, key=lambda c: (c.expiration, c.symbol))   # I10
        if not best_vol > cur_v.volume:
            return None
        # A12.3 as amended by N35 (approved 2026-11-25): closes of the latest 1-minute candle of the crossover
        # session in which BOTH contracts printed a bar (synchronized). Equals the final scheduled candle
        # whenever both traded in it.
        ref_m = last_common_minute(store, self.current, chosen.symbol, session)
        b_old = None if ref_m is None else store.get(self.current, ref_m)
        b_new = None if ref_m is None else store.get(chosen.symbol, ref_m)
        if ref_m is None:
            ref_m = session.end - 1
        flags = []
        if len(best) > 1:
            flags.append("EQUAL_VOLUME_TIE_EARLIEST_EXPIRY")
        cur = self.instrument.contract(self.current)
        if ref_m != session.end - 1 and b_old is not None:
            flags.append("REFERENCE_MINUTE_BEFORE_FINAL_CANDLE")
        if b_old is None or b_new is None:
            flags.append("REFERENCE_PRICE_UNAVAILABLE")
            return RolloverDecision(decided_minute, session.session_id, self.current, chosen.symbol,
                                    None, None, None, ref_m, flags)
        p_old = cur.grid.to_price(b_old.close)
        p_new = chosen.grid.to_price(b_new.close)
        return RolloverDecision(decided_minute, session.session_id, self.current, chosen.symbol,
                                p_new - p_old, p_old, p_new, ref_m, flags)

    def apply(self, decision: RolloverDecision):
        self.replaced.append(self.current)
        self.current = decision.to_contract
