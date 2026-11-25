"""Pipeline orchestration: validated data -> contract selection -> engine.

Layers (spec Part III) are kept separate. This module wires them together
and adds no liquidity behaviour of its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .calendars import ExchangeCalendar, ReferenceCalendar, plan_session
from .data import BarStore, MinuteEvidence
from .engine import LiquidityEngine
from .instrument import InstrumentConfig
from .models import CoarseBar
from .policies import InitialContract, PolicyConfig
from .rollover import ContractSelector, RolloverDecision, SessionVolume, minute_volume_for_session, traded_in


class UnverifiedConfigurationError(RuntimeError):
    pass


def _select_by_volume(instrument, volumes_for, session, decided, selector, store=None):
    """N9(b): after a suspension, pick the highest verified completed-session
    volume among eligible successors (I3: outright, in cycle, later expiry than
    every replaced/current contract, listed, unexpired). Tie -> earliest expiry
    (I10). Returns None unless every candidate's volume is verified."""
    floor = max(instrument.contract(c).expiration for c in selector.replaced + [selector.current])
    cands = [c for c in instrument.contracts
             if c.is_outright and c.in_expiration_cycle and c.expiration > floor and c.expiration > decided
             and (c.activation is None or c.activation <= session.start)
             and c.symbol not in selector.replaced and c.symbol != selector.current
             and (store is None or traded_in(store, c.symbol, session))]   # N34
    if not cands:
        return None
    vols = volumes_for(session, [c.symbol for c in cands], decided)
    if not all(vols.get(c.symbol) is not None and vols[c.symbol].volume is not None for c in cands):
        return None
    best = max(vols[c.symbol].volume for c in cands)
    if best <= 0:
        return None
    return min((c for c in cands if vols[c.symbol].volume == best), key=lambda c: (c.expiration, c.symbol)).symbol


@dataclass
class RunResult:
    engine: LiquidityEngine | None
    decisions: list[RolloverDecision] = field(default_factory=list)
    selection_log: list[dict] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)


def _check_verified(instrument, exchange, reference, allow_unverified_for_tests: bool):
    problems = []
    if not instrument.verified:
        problems.append(f"instrument {instrument.root} not verified")
    if not exchange.verified:
        problems.append(f"exchange calendar {exchange.calendar_id} v{exchange.version} not verified")
    if reference is not None and not reference.verified:
        problems.append(f"reference calendar {reference.calendar_id} v{reference.version} not verified")
    if instrument.exchange_calendar_id != exchange.calendar_id:
        problems.append("exchange calendar id does not match instrument configuration (I9: no substitution)")
    if (reference is None) != (instrument.reference_calendar_id is None) or (
            reference is not None and reference.calendar_id != instrument.reference_calendar_id):
        problems.append("reference calendar does not match instrument configuration (I9: no substitution)")
    if problems and not allow_unverified_for_tests:
        raise UnverifiedConfigurationError("; ".join(problems))
    return problems


def run(
    instrument: InstrumentConfig,
    exchange: ExchangeCalendar,
    reference: ReferenceCalendar | None,
    store: BarStore,
    policies: PolicyConfig,
    start_session_id: str,
    end_session_id: str,
    evidence: MinuteEvidence | None = None,
    coarse: list[CoarseBar] | None = None,
    initial_contract: str | None = None,
    external_volumes: dict[tuple[str, str], SessionVolume] | None = None,
    _allow_unverified_for_tests: bool = False,
) -> RunResult:
    problems = _check_verified(instrument, exchange, reference, _allow_unverified_for_tests)
    i0, i1 = exchange.session_index(start_session_id), exchange.session_index(end_session_id)
    sessions = exchange.sessions[i0:i1 + 1]
    coarse = sorted(coarse or [], key=lambda c: (c.available_minute, c.contract, c.start_minute))
    result = RunResult(None, provenance={
        "instrument": instrument.root, "exchange_calendar": f"{exchange.calendar_id}@{exchange.version}",
        "reference_calendar": None if reference is None else f"{reference.calendar_id}@{reference.version}",
        "policies": policies.as_dict(), "start": start_session_id, "end": end_session_id,
        "evidence_source": None if evidence is None else evidence.source,
        "configuration_problems": problems,
    })

    def volumes_for(session, symbols, decided):
        out = {}
        for sym in symbols:
            if external_volumes is not None:
                v = external_volumes.get((sym, session.session_id))
                if v is not None and v.available_minute <= decided:
                    out[sym] = v
            else:
                out[sym] = minute_volume_for_session(store, evidence, sym, session)
        return out

    # ---------------- initial contract (N8)
    k = 0
    if instrument.has_expirations:
        mode = policies.require("initial_contract", "choosing the first active contract")
        if mode is InitialContract.EXPLICIT:
            if initial_contract is None:
                raise ValueError("initial_contract required for policy EXPLICIT")
            current = initial_contract
        else:
            current = None
            while k < len(sessions) and current is None:
                s = sessions[k]
                # Figures for session k must be available when the decision is taken. With
                # same-session sources (1m sums) that is s.end; with published figures it is
                # the end of the next session at the latest (N33). Processing starts at the
                # session after the decision.
                if external_volumes is None:
                    decided, start_k = s.end, k + 1
                else:
                    if k + 1 >= len(sessions):
                        break
                    decided, start_k = sessions[k + 1].end, k + 2
                cands = [c for c in instrument.contracts if c.is_outright and c.in_expiration_cycle
                         and c.expiration > decided and (c.activation is None or c.activation <= s.start)
                         and traded_in(store, c.symbol, s)]   # N34
                vols = volumes_for(s, [c.symbol for c in cands], decided)
                if cands and all(vols.get(c.symbol) and vols[c.symbol].volume is not None for c in cands):
                    best = max(vols[c.symbol].volume for c in cands)
                    current = min((c for c in cands if vols[c.symbol].volume == best),
                                  key=lambda c: (c.expiration, c.symbol)).symbol
                    result.selection_log.append({"session_id": s.session_id, "initial_contract": current,
                                                 "decided_minute": decided})
                    k = start_k
                    break
                result.selection_log.append({"session_id": s.session_id, "initial_contract": None,
                                             "reason": "VOLUME_UNVERIFIED"})
                k += 1
            if current is None:
                return result
        selector = ContractSelector(instrument, current)
        grid = instrument.contract(current).grid
    else:
        selector = None
        current = instrument.contracts[0].symbol if instrument.contracts else instrument.root
        from .ticks import TickGrid
        grid = TickGrid(instrument.tick_size)

    engine = LiquidityEngine(instrument, policies, current, grid)
    result.engine = engine
    pending: RolloverDecision | None = None
    unevaluated: list = []
    ci = 0

    def n6():
        return policies.require("pre_window_when_reference_closed", "reference market closed").value

    resume_to = None

    def decide(now, just_ended, pending):
        """Evaluate queued crossover sessions with figures available by `now`
        (A12.2: the switch takes effect at the next session open after confirmation).
        N33: a crossover session D whose figures are still incomplete when the
        session after D (D+1) has ended is unverified and dropped (I2: retain)."""
        while unevaluated and pending is None:
            cs = unevaluated[0]
            syms = [selector.current] + [c.symbol for c in selector.successors_for_session(cs, now, store)]
            vols = volumes_for(cs, syms, now)
            if external_volumes is not None and len(vols) < len(syms):
                if just_ended is not None and just_ended is not cs:
                    unevaluated.pop(0)
                    selector.uncertain.append({"session_id": cs.session_id, "reason": "VOLUME_NOT_PUBLISHED_BY_END_OF_NEXT_SESSION",
                                               "missing": sorted(set(syms) - set(vols))})
                    continue
                break  # figures not yet available; evaluate later (no look-ahead)
            unevaluated.pop(0)
            d = selector.evaluate(cs, vols, store, evidence, now)
            if d is not None:
                pending = d
                unevaluated.clear()
        return pending

    for s in sessions[k:]:
        if selector is not None and pending is None and unevaluated and not engine.suspended:
            pending = decide(s.start, None, pending)   # figures published during the closure
        if engine.suspended and resume_to is not None:
            new = instrument.contract(resume_to)
            # coarse evidence that arrived while suspended is not applied (engine was not processing)
            while ci < len(coarse) and coarse[ci].available_minute <= s.start:
                ci += 1
            engine.resume(s.start, new.symbol, new.grid, {"reason": "N9_RESUME"})
            selector.replaced.append(selector.current)
            selector.current = new.symbol
            unevaluated.clear()
            resume_to = None
        if pending is not None:
            new = instrument.contract(pending.to_contract)
            engine.rollover(s.start, new.symbol, new.grid, pending.delta,
                            {"crossover_session": pending.crossover_session_id, "flags": pending.flags})
            selector.apply(pending)
            result.decisions.append(pending)
            pending = None
        if selector is not None and not engine.suspended and not selector.current_eligible_at(s.start):
            engine.suspend(s.start, f"current contract {selector.current} ineligible and no verified successor")
        if engine.suspended:
            if resume_to is None:
                # N9(b): while suspended, evaluate each completed session (decision at its end,
                # effective at the next session open)
                pick = _select_by_volume(instrument, volumes_for, s, s.end, selector, store)
                result.selection_log.append({"session_id": s.session_id, "suspended": True, "resume_pick": pick})
                if pick is not None:
                    resume_to = pick
            continue
        plan = plan_session(s, reference, n6, instrument.nominal_pre_window_end,
                            reference.tz if reference else None)
        engine.begin_session(plan)
        for m in s.minutes():
            while ci < len(coarse) and coarse[ci].available_minute <= m:
                engine.apply_coarse_evidence(coarse[ci])
                ci += 1
            status = store.classify(engine.contract, m, evidence)
            bar = store.get(engine.contract, m)
            engine.process_minute(m, status, bar)
        if selector is not None:
            unevaluated.append(s)
            pending = decide(s.end, s, pending)
    result.selection_log.extend(selector.uncertain if selector else [])
    return result
