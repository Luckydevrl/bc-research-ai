"""Exact probability metrics from a game's discrete payout distribution."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from simulation.games import GameModel, UnsupportedGame, build_model


@dataclass(frozen=True)
class ProbabilityProfile:
    game: str
    status: str
    p_win: float
    p_lose: float
    p_push: float
    rtp: float
    ev_per_unit: float
    house_edge: float
    variance: float
    std: float
    volatility: str
    breakeven_win_prob: float | None
    max_multiplier: float
    origin: str

    @property
    def ev(self) -> float:
        return self.ev_per_unit

    def as_dict(self) -> dict:
        return asdict(self)


def volatility_label(std: float) -> str:
    """Per-unit-stake standard deviation buckets (descriptive only)."""
    if std < 1.0:
        return "low"
    if std < 3.0:
        return "medium"
    if std < 10.0:
        return "high"
    return "very high"


def profile(model: GameModel) -> ProbabilityProfile:
    m, p = model.mults, model.probs
    net = m - 1.0
    ev = float(np.dot(p, net))
    var = float(np.dot(p, (net - ev) ** 2))
    std = var ** 0.5
    p_win = float(p[net > 1e-12].sum())
    p_lose = float(p[net < -1e-12].sum())
    be = None
    wins = net > 1e-12
    if wins.any() and p_win > 0:
        mean_win_mult = float(np.dot(p[wins], m[wins]) / p_win)
        be = 1.0 / mean_win_mult if len(m) == 2 else None  # exact only for two-outcome games
    return ProbabilityProfile(
        game=model.game, status="OK", p_win=p_win, p_lose=p_lose, p_push=max(0.0, 1 - p_win - p_lose),
        rtp=float(np.dot(p, m)), ev_per_unit=ev, house_edge=-ev, variance=var, std=std,
        volatility=volatility_label(std), breakeven_win_prob=be, max_multiplier=float(m.max()), origin=model.origin)


def analyze_game(game: str, params: dict | None = None, edge: float = 0.01) -> dict:
    """Profile a game, or report INSUFFICIENT_DATA when no rules/model exist (never fabricate)."""
    try:
        return profile(build_model(game, params, edge)).as_dict()
    except UnsupportedGame as exc:
        return {"game": game, "status": "INSUFFICIENT_DATA", "reason": str(exc)}


def risk_of_ruin_and_drawdown(model: GameModel, bankroll: float, stake: float, rounds: int,
                              n_sessions: int = 2000, seed: int = 7) -> dict:
    """Simulation-based estimates (flat staking). Labelled as simulated, not analytic."""
    from models.schemas import StrategyConfig
    from research.strategy_engine import STOP_BUST, simulate

    cfg = StrategyConfig(kind="flat", bankroll=bankroll, base_stake=stake, max_rounds=rounds)
    b = simulate(model, cfg, n_sessions, seed)
    return {
        "method": "monte_carlo",
        "risk_of_ruin": float((b.reason == STOP_BUST).mean()),
        "expected_max_drawdown": float(b.max_drawdown.mean()),
        "p95_max_drawdown": float(np.percentile(b.max_drawdown, 95)),
        "n_sessions": n_sessions,
        "rounds": rounds,
    }
