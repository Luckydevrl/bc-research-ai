"""Monte Carlo study of the daily virtual target (default INR 1,000).

Distinguishes three different things that are easy to confuse:
  1. the target being reached in *a* simulated day      -> p_target
  2. positive expected value                              -> sign of E[P/L]
  3. being statistically sustainable                      -> needs (2) AND repeatability over many days

Theory used as a cross-check: for any strategy whose stake is decided before each round
(no peeking) and a bounded number of rounds, E[P/L] = -edge * E[total amount staked].
Take-profit / stop-loss / staking patterns change the *shape* of outcomes, not the sign.
"""
from __future__ import annotations

import numpy as np
from scipy import stats

from models.schemas import MonteCarloConfig, StrategyConfig
from research.strategy_engine import STOP_BUST, STOP_TARGET, simulate, summarize
from simulation.games import GameModel


def _strategy_for(cfg: MonteCarloConfig, budget: float, fraction: float) -> StrategyConfig:
    stake = min(budget, max(0.01, round(budget * fraction, 2)))
    return StrategyConfig(
        kind=cfg.strategy, bankroll=budget, base_stake=stake, pct=fraction, take_profit=cfg.target,
        stop_loss=(budget * cfg.stop_loss_fraction) if cfg.stop_loss_fraction else None,
        max_rounds=cfg.rounds_per_day)


def run_daily_target(model: GameModel, cfg: MonteCarloConfig | None = None) -> dict:
    cfg = cfg or MonteCarloConfig()
    rng = np.random.default_rng(cfg.seed + 99)
    rows = []
    run_no = 0
    for budget in cfg.budgets:
        for frac in cfg.stake_fractions:
            scfg = _strategy_for(cfg, budget, frac)
            batch = simulate(model, scfg, cfg.n_sessions, seed=cfg.seed + run_no)
            run_no += 1
            s = summarize(batch, model)
            p_t = float((batch.reason == STOP_TARGET).mean())
            p_bust = float((batch.reason == STOP_BUST).mean())
            draws = rng.choice(batch.pnl, size=(min(10_000, 20 * cfg.n_sessions), cfg.days), replace=True)
            totals = draws.sum(axis=1)
            se = s["std_pnl"] / np.sqrt(cfg.n_sessions)
            consistent = bool(abs(s["mean_pnl"] - s["theoretical_ev"]) <= 3.5 * se + 0.05) if se > 0 else True
            rows.append({
                "budget": budget, "stake_fraction": frac, "stake": scfg.base_stake,
                "p_target": p_t, "p_lose_budget": p_bust, "p_stop_loss": s["p_stop_loss"],
                "expected_daily_pnl": s["mean_pnl"], "expected_daily_pnl_ci95": s["ci95_mean"],
                "theoretical_daily_ev": s["theoretical_ev"], "simulation_consistent_with_theory": consistent,
                "median_daily_pnl": s["median_pnl"], "worst_daily_pnl": s["worst_pnl"], "p5_daily_pnl": s["p5_pnl"],
                "mean_max_drawdown": s["mean_max_drawdown"], "p95_max_drawdown": s["p95_max_drawdown"],
                "mean_rounds": s["mean_rounds"],
                "p_target_every_day": p_t ** cfg.days,
                "p_target_at_least_half_of_days": float(stats.binom.sf(cfg.days // 2 - 1, cfg.days, p_t)),
                "expected_target_days": p_t * cfg.days,
                "p_lose_budget_at_least_once": 1 - (1 - p_bust) ** cfg.days,
                "total_pnl_median": float(np.median(totals)), "total_pnl_p5": float(np.percentile(totals, 5)),
                "p_total_ge_days_x_target": float((totals >= cfg.days * cfg.target).mean()),
                "p_total_negative": float((totals < 0).mean()),
            })

    if model.edge > 0:
        ev_verdict = "NEGATIVE_EV"
    elif model.edge == 0:
        ev_verdict = "ZERO_EV"
    else:
        ev_verdict = "POSITIVE_EV_IN_THIS_MODEL"
    positive = ev_verdict == "POSITIVE_EV_IN_THIS_MODEL"
    sustainable = bool(positive and any(r["expected_daily_pnl_ci95"][0] > 0 and r["p_target_every_day"] > 0.5 for r in rows))
    hit_half = [r for r in rows if r["p_target"] >= 0.5]
    required = min(hit_half, key=lambda r: r["budget"]) if hit_half else None
    return {
        "game": model.game, "model": model.key, "model_origin": model.origin, "assumed_house_edge": model.edge,
        "target": cfg.target, "days": cfg.days, "n_sessions": cfg.n_sessions, "rounds_per_day": cfg.rounds_per_day,
        "strategy": cfg.strategy, "rows": rows,
        "required_capital_for_50pct_single_day_target": None if required is None else
            {"budget": required["budget"], "stake_fraction": required["stake_fraction"]},
        "ev_verdict": ev_verdict, "ev_basis": "E[P/L] = -edge x E[turnover] for any pre-committed staking rule",
        "all_rows_consistent_with_theory": all(r["simulation_consistent_with_theory"] for r in rows),
        "statistically_sustainable": sustainable,
        "note": ("Reaching the target in some simulated days is NOT the same as positive expected value or "
                 "sustainability. Simulated money is not withdrawable money."),
    }
