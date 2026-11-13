"""Vectorised strategy simulator (virtual money only).

One engine drives everything: single recorded dashboard sessions (N=1),
multi-seed strategy comparison and the Monte Carlo daily-target study.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from models.schemas import StrategyConfig
from research.probability_engine import profile
from simulation.games import GameModel

STOP_NONE, STOP_TARGET, STOP_LOSS, STOP_BUST, STOP_ROUNDS = 0, 1, 2, 3, 4
STOP_NAMES = {0: "none", 1: "take_profit", 2: "stop_loss", 3: "bust", 4: "max_rounds"}


@dataclass
class SimulationBatch:
    pnl: np.ndarray
    rounds: np.ndarray
    wins: np.ndarray
    losses: np.ndarray
    max_drawdown: np.ndarray
    longest_losing_streak: np.ndarray
    staked: np.ndarray
    reason: np.ndarray
    log: list = field(default_factory=list)  # (stake, multiplier, pnl) when record=True and N==1


def _stakes(cfg: StrategyConfig, bank: np.ndarray, step: np.ndarray) -> np.ndarray:
    if cfg.kind == "flat":
        s = np.full_like(bank, cfg.base_stake)
    elif cfg.kind == "percent":
        s = bank * cfg.pct
    elif cfg.kind == "conservative":
        s = np.minimum(cfg.base_stake, 0.01 * bank)
    else:  # progressive: raise stake after each loss (capped), reset after a win
        s = cfg.base_stake * cfg.progression_factor ** step
    return np.round(np.minimum(s, bank), 2)


def simulate(model: GameModel, cfg: StrategyConfig, n_sessions: int = 1, seed: int | None = None,
             record: bool = False) -> SimulationBatch:
    rng = np.random.default_rng(seed)
    n = int(n_sessions)
    mults = model.mults
    cdf = np.cumsum(model.probs)
    cdf[-1] = 1.0
    k = len(mults)

    bank = np.full(n, float(cfg.bankroll))
    cum = np.zeros(n)
    peak = np.zeros(n)
    maxdd = np.zeros(n)
    streak = np.zeros(n, dtype=np.int64)
    maxstreak = np.zeros(n, dtype=np.int64)
    wins = np.zeros(n, dtype=np.int64)
    losses = np.zeros(n, dtype=np.int64)
    rounds = np.zeros(n, dtype=np.int64)
    staked = np.zeros(n)
    step = np.zeros(n, dtype=np.int64)
    active = np.ones(n, dtype=bool)
    reason = np.zeros(n, dtype=np.int64)
    log: list = []

    for _ in range(cfg.max_rounds):
        if not active.any():
            break
        stake = _stakes(cfg, bank, step)
        too_small = active & (stake < cfg.min_stake)
        if too_small.any():
            reason[too_small] = STOP_BUST
            active &= ~too_small
            if not active.any():
                break
        u = rng.random(n)
        idx = np.minimum(np.searchsorted(cdf, u, side="right"), k - 1)
        mult = mults[idx]
        pnl = np.where(active, np.round(stake * (mult - 1.0), 2), 0.0)
        stake_a = np.where(active, stake, 0.0)
        bank = np.round(bank + pnl, 2)
        cum = np.round(cum + pnl, 2)
        staked += stake_a
        rounds += active
        won = active & (pnl > 0)
        lost = active & (pnl < 0)
        wins += won
        losses += lost
        streak = np.where(lost, streak + 1, np.where(active, 0, streak))
        maxstreak = np.maximum(maxstreak, streak)
        peak = np.maximum(peak, cum)
        maxdd = np.maximum(maxdd, peak - cum)
        step = np.where(lost, np.minimum(step + 1, cfg.max_progression_steps), np.where(active, 0, step))
        if record and n == 1:
            log.append((float(stake[0]), float(mult[0]), float(pnl[0])))
        if cfg.take_profit is not None:
            hit = active & (cum >= cfg.take_profit)
            reason[hit] = STOP_TARGET
            active &= ~hit
        if cfg.stop_loss is not None:
            sl = active & (cum <= -cfg.stop_loss)
            reason[sl] = STOP_LOSS
            active &= ~sl
        bust = active & (bank < cfg.min_stake)
        reason[bust] = STOP_BUST
        active &= ~bust

    reason[active] = STOP_ROUNDS
    return SimulationBatch(cum, rounds, wins, losses, maxdd, maxstreak, staked, reason, log)


def summarize(batch: SimulationBatch, model: GameModel) -> dict:
    n = len(batch.pnl)
    pnl = batch.pnl
    mean = float(pnl.mean())
    sd = float(pnl.std(ddof=1)) if n > 1 else 0.0
    se = sd / np.sqrt(n) if n > 1 else 0.0
    total_rounds = int(batch.rounds.sum())
    prof = profile(model)
    return {
        "n_sessions": n,
        "mean_pnl": mean,
        "median_pnl": float(np.median(pnl)),
        "std_pnl": sd,
        "ci95_mean": [mean - 1.96 * se, mean + 1.96 * se],
        "worst_pnl": float(pnl.min()),
        "p5_pnl": float(np.percentile(pnl, 5)),
        "best_pnl": float(pnl.max()),
        "win_rate": float(batch.wins.sum() / total_rounds) if total_rounds else 0.0,
        "loss_rate": float(batch.losses.sum() / total_rounds) if total_rounds else 0.0,
        "mean_max_drawdown": float(batch.max_drawdown.mean()),
        "p95_max_drawdown": float(np.percentile(batch.max_drawdown, 95)),
        "longest_losing_streak": int(batch.longest_losing_streak.max()),
        "mean_longest_losing_streak": float(batch.longest_losing_streak.mean()),
        "risk_of_ruin": float((batch.reason == STOP_BUST).mean()),
        "p_take_profit": float((batch.reason == STOP_TARGET).mean()),
        "p_stop_loss": float((batch.reason == STOP_LOSS).mean()),
        "mean_rounds": float(batch.rounds.mean()),
        "total_rounds": total_rounds,
        "mean_total_staked": float(batch.staked.mean()),
        "theoretical_ev": float(batch.staked.mean() * prof.ev),  # E[pnl] = -edge * E[total staked]
    }


def run_strategy(model: GameModel, cfg: StrategyConfig, n_sessions: int = 1000, seed: int = 1) -> dict:
    return summarize(simulate(model, cfg, n_sessions, seed), model)


def compare_across_seeds(model: GameModel, cfg: StrategyConfig, seeds: list[int], n_sessions: int = 1000) -> dict:
    """Run one strategy under several seeds. A strategy is only called 'robustly positive'
    if the pooled 95% CI of mean P/L is above zero AND every seed agrees."""
    per_seed = [run_strategy(model, cfg, n_sessions, s) for s in seeds]
    means = np.array([r["mean_pnl"] for r in per_seed])
    pooled = float(means.mean())
    # pooled SE from the per-session std (independent seeds -> total sessions = len(seeds)*n)
    sd = float(np.mean([r["std_pnl"] for r in per_seed]))
    se = sd / np.sqrt(len(seeds) * n_sessions)
    ci = [pooled - 1.96 * se, pooled + 1.96 * se]
    all_positive = bool((means > 0).all())
    if ci[0] > 0 and all_positive:
        verdict = "POSITIVE_EV_IN_THIS_MODEL"
    elif ci[1] < 0:
        verdict = "NEGATIVE_EV"
    else:
        verdict = "INCONCLUSIVE"
    return {
        "strategy": cfg.kind,
        "seeds": seeds,
        "n_sessions_per_seed": n_sessions,
        "mean_pnl_by_seed": [float(m) for m in means],
        "pooled_mean_pnl": pooled,
        "pooled_ci95": ci,
        "profit_in_some_seeds_only": bool((means > 0).any() and not all_positive),
        "verdict": verdict,
        "summary_seed0": per_seed[0],
    }
