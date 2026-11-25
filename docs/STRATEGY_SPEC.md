# Strategy Specification — DRAFT v0.1 (being defined with the trader)

Strategy layer on top of the validated liquidity engine (spec v1.2). It reads engine outputs only, and never
changes liquidity state (A16). **No code exists for it yet.** Code is written after every section below is
approved.

Status legend: **[APPROVED]** trader decision · **[OPEN]** not yet defined.

## 1. Setup selection — [APPROVED 2026-11-25]
1.1 **Qualifying sweeps:** any sweep event that is `CONFIRMED` and passes the trading-window eligibility layer
    (09:30–11:30 ET: penetration bar opens in the window, confirmation observable before 11:30, reference not
    quarantined, not dual-sided).
1.2 **Direction:** BSL sweep → **short**. SSL sweep → **long**.
1.3 **"Last sweep":** the qualifying sweep with the **latest confirmation** (confirmation candle closed most
    recently) is the active setup.
1.4 **Replacement:** a newer qualifying sweep **replaces** any older setup that has not been entered yet. The older
    setup is cancelled.
1.5 **Opposite directions:** the same replacement applies regardless of direction. For example, a later SSL sweep
    (long) cancels an earlier BSL setup (short) that is waiting for entry.
1.6 **Same-candle ties:** sweeps confirmed on the same candle and the same side form **one setup**. They share one
    confirmation candle and one frozen extreme E, and all their levels are recorded on it.
1.7 **In a position:** while a trade is open, new sweeps are ignored. Setup selection resumes after the position
    is closed.

## 2. 1-minute FVG — [REMOVED 2026-11-25]
The trader confirmed the FVG plays no part in this strategy's entry. (The repository name predates this.)

## 3. Entry trigger — [APPROVED 2026-11-25]
3.1 **Trigger:** enter when the active setup's sweep is confirmed, i.e. at the close of the confirmation candle q
    (a BSL setup closes back below the level → short; an SSL setup closes back above the level → long).
3.2 **Fill model (backtest):** a market order fills at the **open of the next 1-minute candle (q+1)**, plus
    **1 tick slippage** against the trade. Sensitivity is also reported at 0 and 2 ticks. Entry must occur before
    11:30 ET (A7).
3.3 **Late confirmations:** confirmation on any candle k = 0..3 of the engine's window is valid. Results are
    reported by k.
3.4 **Distance filter:** none in the base strategy. The entry-to-level distance is recorded on every trade, for a
    possible separately tested variation.
## 4. Stop loss — [OPEN]
## 5. Take profit / exit — [OPEN]
## 6. Risk, sizing, limits, costs — [OPEN]
