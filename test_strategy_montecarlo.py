import numpy as np
import pytest

from models.schemas import MonteCarloConfig, StrategyConfig
from research.monte_carlo import run_daily_target
from research.strategy_engine import STOP_BUST, STOP_ROUNDS, STOP_TARGET, compare_across_seeds, run_strategy, simulate
from simulation.games import build_model


def cfg(**kw):
    base = dict(kind="flat", bankroll=1000, base_stake=10, max_rounds=200)
    base.update(kw)
    return StrategyConfig(**base)


def test_reproducible_with_seed():
    m = build_model("Crash")
    a = simulate(m, cfg(), 200, seed=5)
    b = simulate(m, cfg(), 200, seed=5)
    c = simulate(m, cfg(), 200, seed=6)
    assert np.array_equal(a.pnl, b.pnl) and not np.array_equal(a.pnl, c.pnl)


@pytest.mark.parametrize("kind", ["flat", "percent", "conservative", "progressive"])
@pytest.mark.parametrize("game", ["Crash", "Mines", "Plinko", "Limbo"])
def test_engine_ev_matches_theory(kind, game):
    """E[P/L] = -edge * E[turnover]: the engine must neither create nor destroy edge."""
    m = build_model(game, None, 0.05)
    s = run_strategy(m, cfg(kind=kind, base_stake=20 if kind != "progressive" else 5, stop_loss=300, take_profit=200),
                     n_sessions=6000, seed=42)
    se = s["std_pnl"] / np.sqrt(s["n_sessions"])
    assert abs(s["mean_pnl"] - s["theoretical_ev"]) < 4 * se + 0.05
    assert s["theoretical_ev"] < 0


def test_zero_edge_gives_zero_ev_and_negative_edge_detected():
    s0 = run_strategy(build_model("Dice", None, 0.0), cfg(), 8000, 1)
    assert s0["ci95_mean"][0] - 1 < 0 < s0["ci95_mean"][1] + 1
    from simulation.games import GameModel
    fav = GameModel(game="Dice", category="Dice", outcomes=((2.2, 0.5), (0.0, 0.5)), edge=-0.10)  # hand-built, test only
    sp = run_strategy(fav, cfg(), 8000, 1)
    assert sp["ci95_mean"][0] > 0          # engine can detect a (modelled) player-favourable game
    with pytest.raises(ValueError):        # ...but the app's model builder refuses to create one
        build_model("Dice", None, -0.10)


def test_take_profit_stop_loss_and_bust_and_rounds():
    m = build_model("Dice", {"win_chance": 50}, 0.0)
    b = simulate(m, cfg(take_profit=50, stop_loss=50, max_rounds=5000), 500, 3)
    assert set(np.unique(b.reason)) <= {STOP_TARGET, 2}
    assert (b.pnl[b.reason == STOP_TARGET] >= 50).all() and (b.pnl[b.reason == 2] <= -50).all()
    b2 = simulate(m, cfg(bankroll=20, base_stake=10, max_rounds=10_000), 300, 3)
    busted = b2.reason == STOP_BUST
    assert busted.mean() > 0.95 and (b2.pnl[busted] == -20).all()
    b3 = simulate(m, cfg(max_rounds=3), 50, 3)
    assert (b3.reason == STOP_ROUNDS).all() and (b3.rounds == 3).all()


def test_never_loses_more_than_bankroll_even_progressive():
    m = build_model("Crash", {"cashout": 10}, 0.01)
    b = simulate(m, cfg(kind="progressive", base_stake=50, max_progression_steps=10, max_rounds=500), 2000, 8)
    assert (b.pnl >= -1000).all()


def test_record_log_matches_totals():
    b = simulate(build_model("Plinko"), cfg(max_rounds=60), 1, 4, record=True)
    assert len(b.log) == b.rounds[0] == 60
    assert round(sum(p for _, _, p in b.log), 2) == round(float(b.pnl[0]), 2)


def test_streak_and_drawdown_metrics():
    b = simulate(build_model("Crash"), cfg(max_rounds=300), 500, 2)
    assert (b.longest_losing_streak <= b.losses).all() and (b.max_drawdown >= 0).all()
    assert (b.wins + b.losses <= b.rounds).all()


def test_single_profitable_run_is_not_a_recommendation():
    m = build_model("Crash", None, 0.01)
    res = compare_across_seeds(m, cfg(kind="progressive", base_stake=5, take_profit=100, stop_loss=1000, max_rounds=100),
                               [1, 2, 3, 4], 400)
    assert res["verdict"] != "POSITIVE_EV_IN_THIS_MODEL"


def test_monte_carlo_daily_target_semantics():
    mc = run_daily_target(build_model("Dice", None, 0.01),
                          MonteCarloConfig(budgets=[1000, 10000], stake_fractions=[0.05, 0.2], n_sessions=1500,
                                           rounds_per_day=300, seed=7))
    assert mc["ev_verdict"] == "NEGATIVE_EV" and mc["statistically_sustainable"] is False
    assert mc["all_rows_consistent_with_theory"]
    for r in mc["rows"]:
        assert 0 <= r["p_target"] <= 1 and r["worst_daily_pnl"] <= r["median_daily_pnl"]
        assert r["p_target_every_day"] == pytest.approx(r["p_target"] ** 30)
        assert r["expected_daily_pnl"] < 50      # target reached often != positive expected value
    big = [r for r in mc["rows"] if r["budget"] == 10000 and r["stake_fraction"] == 0.05][0]
    assert big["p_target"] > 0.5 and big["expected_daily_pnl"] < 0      # the classic illusion, quantified


def test_monte_carlo_not_sustainable_with_zero_edge():
    mc = run_daily_target(build_model("Dice", None, 0.0), MonteCarloConfig(budgets=[1000], stake_fractions=[0.1],
                                                                           n_sessions=1000, rounds_per_day=200))
    assert mc["ev_verdict"] == "ZERO_EV" and not mc["statistically_sustainable"]


def test_config_validation():
    with pytest.raises(ValueError):
        MonteCarloConfig(budgets=[])
    with pytest.raises(ValueError):
        StrategyConfig(bankroll=100, base_stake=500)
    with pytest.raises(ValueError):
        StrategyConfig(bankroll=-1)
