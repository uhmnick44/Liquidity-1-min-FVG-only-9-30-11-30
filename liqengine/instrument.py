"""Instrument and contract configuration (A2, I3, I5, I9).

No defaults are shared between instruments. Each instrument file states its
own tick size, calendars, expiration cycle and contract list, together with
the source each fact was verified against.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal
from pathlib import Path

from .ticks import TickGrid, to_decimal
from .timeutil import dt_to_minute, parse_utc


@dataclass(frozen=True)
class Contract:
    symbol: str                   # e.g. "MNQH5"
    instrument_id: int | None     # vendor id (Databento instrument_id)
    expiration: int               # last eligible trade time, UTC minute
    tick_size: Decimal
    in_expiration_cycle: bool     # I3: belongs to the approved cycle
    is_outright: bool = True      # spreads are never eligible
    activation: int | None = None  # listing time (UTC minute); not eligible before it

    @property
    def grid(self) -> TickGrid:
        return TickGrid(self.tick_size)


@dataclass
class InstrumentConfig:
    root: str
    has_expirations: bool
    tick_size: Decimal                       # instrument-level grid (non-expiring or default check)
    exchange_calendar_id: str
    reference_calendar_id: str | None        # None => no RTH/pre-window categories
    contracts: list[Contract] = field(default_factory=list)
    nominal_pre_window_end: time | None = None  # only used if policy N6 = GENERATE_TO_NOMINAL_TIME
    verified: bool = False
    sources: dict = field(default_factory=dict)

    def __post_init__(self):
        syms = [c.symbol for c in self.contracts]
        if len(set(syms)) != len(syms):
            raise ValueError("duplicate contract symbols")
        if self.has_expirations and not self.contracts:
            raise ValueError("expiring instrument requires a contract list")

    def contract(self, symbol: str) -> Contract:
        for c in self.contracts:
            if c.symbol == symbol:
                return c
        raise KeyError(symbol)

    def contract_by_instrument_id(self, iid: int) -> Contract | None:
        for c in self.contracts:
            if c.instrument_id == iid:
                return c
        return None

    @classmethod
    def from_dict(cls, d: dict) -> "InstrumentConfig":
        contracts = [
            Contract(
                symbol=c["symbol"],
                instrument_id=c.get("instrument_id"),
                expiration=dt_to_minute(parse_utc(c["expiration"])),
                tick_size=to_decimal(c["tick_size"]),
                in_expiration_cycle=bool(c["in_expiration_cycle"]),
                is_outright=bool(c.get("is_outright", True)),
                activation=dt_to_minute(parse_utc(c["activation"])) if c.get("activation") else None,
            )
            for c in d.get("contracts", [])
        ]
        npe = d.get("nominal_pre_window_end")
        return cls(
            root=d["root"], has_expirations=bool(d["has_expirations"]),
            tick_size=to_decimal(d["tick_size"]),
            exchange_calendar_id=d["exchange_calendar_id"],
            reference_calendar_id=d.get("reference_calendar_id"),
            contracts=contracts,
            nominal_pre_window_end=time.fromisoformat(npe) if npe else None,
            verified=bool(d.get("verified", False)), sources=d.get("sources", {}),
        )

    @classmethod
    def load(cls, path: str | Path) -> "InstrumentConfig":
        return cls.from_dict(json.loads(Path(path).read_text()))
