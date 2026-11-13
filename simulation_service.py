"""Run one recorded virtual simulation session and settle it through the ledger."""
from __future__ import annotations

import secrets
from decimal import Decimal

from models.schemas import StrategyConfig
from research.strategy_engine import STOP_NAMES, simulate
from services.accounting import Accounting, D
from services.ratelimit import RateLimiter
from simulation.games import build_model


def run_simulation(acct: Accounting, *, game: str, rounds: int = 100, strategy: str = "flat",
                   stake: Decimal | float | None = None, bankroll: Decimal | float | None = None,
                   seed: int | None = None, params: dict | None = None, edge: float = 0.01,
                   max_rounds: int = 1000, limiter: RateLimiter | None = None,
                   stop_loss: float | None = None, take_profit: float | None = None) -> dict:
    if limiter is not None:
        limiter.check("simulate")
    if not (1 <= int(rounds) <= max_rounds):
        raise ValueError(f"rounds must be between 1 and {max_rounds}")
    model = build_model(game, params, edge)
    snap = acct.snapshot()
    bank = D(bankroll) if bankroll is not None else snap.allocatable
    stk = D(stake) if stake is not None else max(Decimal("0.01"), D(bank * Decimal("0.01")))
    seed = int(seed) if seed is not None else secrets.randbits(31)
    cfg = StrategyConfig(kind=strategy, bankroll=float(bank), base_stake=float(min(stk, bank)),
                         max_rounds=int(rounds), stop_loss=stop_loss, take_profit=take_profit)
    sid = acct.open_session(game=model.game, model_key=model.key, strategy=strategy,
                            config=cfg.model_dump(), seed=seed, bankroll=bank)
    try:
        batch = simulate(model, cfg, 1, seed, record=True)
        pnl = sum((D(p) for _, _, p in batch.log), Decimal("0.00"))
        result = acct.close_session(
            sid, pnl=pnl, rounds=int(batch.rounds[0]), wins=int(batch.wins[0]), losses=int(batch.losses[0]),
            max_drawdown=float(batch.max_drawdown[0]), longest_losing_streak=int(batch.longest_losing_streak[0]),
            stop_reason=STOP_NAMES[int(batch.reason[0])], rounds_log=batch.log)
    except Exception:
        acct.void_session(sid, "simulation failed")
        raise
    result.update({"seed": seed, "strategy": strategy, "bankroll": bank, "stake": stk,
                   "model": model.key, "data_origin": "SIMULATED (virtual, assumed model)"})
    return result
