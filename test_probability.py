import math

import pytest

from research.probability_engine import analyze_game, profile
from simulation.games import SUPPORTED_GAMES, UnsupportedGame, build_model


@pytest.mark.parametrize("game", SUPPORTED_GAMES)
@pytest.mark.parametrize("edge", [0.0, 0.01, 0.05])
def test_rtp_and_house_edge_match_assumed_edge(game, edge):
    m = build_model(game, None, edge)
    p = profile(m)
    assert math.isclose(sum(pr for _, pr in m.outcomes), 1.0, abs_tol=1e-12)
    assert math.isclose(p.rtp, 1 - edge, abs_tol=1e-9)
    assert math.isclose(p.house_edge, edge, abs_tol=1e-9)
    assert math.isclose(p.ev_per_unit, -edge, abs_tol=1e-9)
    assert math.isclose(p.p_win + p.p_lose + p.p_push, 1.0, abs_tol=1e-9)


def test_two_outcome_formulas():
    m = build_model("Dice", {"win_chance": 50}, 0.02)
    p = profile(m)
    payout = 0.98 / 0.5
    assert math.isclose(p.p_win, 0.5) and math.isclose(p.max_multiplier, payout)
    assert math.isclose(p.variance, 0.5 * 0.5 * payout ** 2, rel_tol=1e-9)   # p(1-p)m^2
    assert math.isclose(p.std, math.sqrt(p.variance))
    assert math.isclose(p.breakeven_win_prob, 1 / payout)


def test_mines_combinatorics():
    m = build_model("Mines", {"tiles": 25, "mines": 3, "reveals": 3}, 0.01)
    assert math.isclose(profile(m).p_win, math.comb(22, 3) / math.comb(25, 3))


def test_plinko_symmetric_binomial():
    m = build_model("Plinko", {"rows": 8, "risk": "high"}, 0.01)
    assert len(m.outcomes) == 9
    ps = [p for _, p in m.outcomes]
    assert math.isclose(ps[0], 1 / 256) and ps == ps[::-1]


def test_unknown_game_is_insufficient_data_not_invented():
    with pytest.raises(UnsupportedGame):
        build_model("Hilo")
    r = analyze_game("Hilo")
    assert r["status"] == "INSUFFICIENT_DATA" and "ev_per_unit" not in r


@pytest.mark.parametrize("bad", [{"cashout": 0.5}, {"cashout": 5000}])
def test_invalid_params(bad):
    with pytest.raises(ValueError):
        build_model("Crash", bad)


def test_invalid_edge():
    with pytest.raises(ValueError):
        build_model("Crash", None, 0.9)
