import numpy as np
import pandas as pd
import pytest

from research.rtp_engine import compare_with_published, empirical_rtp, implied_rtp_from_multipliers, normalize_rtp
from research.statistical_analysis import analyze_outcomes, longest_runs, runs_test


def test_empirical_rtp_exact_on_known_data():
    bets = [10.0] * 1000
    pays = [0.0] * 500 + [19.8] * 500
    r = empirical_rtp(bets, pays)
    assert r["status"] == "OK" and r["rtp"] == pytest.approx(0.99)
    assert r["ci95"][0] < 0.99 < r["ci95"][1]


def test_empirical_rtp_ci_covers_truth_on_simulated_data():
    rng = np.random.default_rng(1)
    hits = 0
    for _ in range(40):
        win = rng.random(5000) < 0.495
        r = empirical_rtp(np.ones(5000), np.where(win, 2.0, 0.0))
        hits += r["ci95"][0] <= 0.99 <= r["ci95"][1]
    assert hits >= 34       # ~95% coverage, loose bound to avoid flakiness (seeded anyway)


def test_insufficient_sample_sizes():
    assert empirical_rtp([1] * 10, [1] * 10)["status"] == "INSUFFICIENT_DATA"
    assert implied_rtp_from_multipliers([2.0] * 10)["status"] == "INSUFFICIENT_DATA"
    assert empirical_rtp([], [])["status"] == "INSUFFICIENT_DATA"
    assert analyze_outcomes(pd.DataFrame({"multiplier": [1.0, 2.0]}))["status"] == "INSUFFICIENT_DATA"
    assert analyze_outcomes(pd.DataFrame())["status"] == "INSUFFICIENT_DATA"
    assert analyze_outcomes(None)["status"] == "INSUFFICIENT_DATA"


def test_implied_rtp_recovers_crash_distribution():
    rng = np.random.default_rng(3)
    u = rng.random(200_000)
    x = np.maximum(0.99 / (1 - u), 1.0)
    r = implied_rtp_from_multipliers(x)
    for row in r["by_threshold"]:
        assert row["ci95"][0] <= 0.99 <= row["ci95"][1]
    assert compare_with_published(0.99, r)["status"] == "CONSISTENT"
    assert compare_with_published(0.90, r)["status"] == "DISCREPANCY"


def test_compare_edge_cases():
    assert compare_with_published(None, {"status": "OK"})["status"] == "NO_PUBLISHED_RTP"
    assert compare_with_published(0.99, {"status": "INSUFFICIENT_DATA", "n": 3})["status"] == "INSUFFICIENT_DATA"
    assert normalize_rtp(99) == pytest.approx(0.99)
    with pytest.raises(ValueError):
        normalize_rtp(-1)


def _df(x):
    return pd.DataFrame({"multiplier": x, "round_id": [str(i) for i in range(len(x))],
                         "ts": pd.date_range("2024-01-01", periods=len(x), freq="20s", tz="UTC")})


def test_random_data_not_flagged_as_pattern():
    rng = np.random.default_rng(11)
    x = np.maximum(0.99 / (1 - rng.random(5000)), 1.0)
    r = analyze_outcomes(_df(x))
    assert r["status"] == "OK" and "No statistically significant" in r["conclusion"]
    assert "NONE" in r["predictive_claim"]
    assert r["streaks"]["longest_false"] >= 1 and r["describe"]["variance"] > 0


def test_alternating_data_is_flagged_as_anomaly_not_predictor():
    x = np.tile([1.1, 3.0], 1500)
    r = analyze_outcomes(_df(x))
    assert "runs_test" in r["conclusion"] and "not evidence" in r["conclusion"]


def test_distribution_shift_detected():
    rng = np.random.default_rng(5)
    a = np.maximum(0.99 / (1 - rng.random(2000)), 1.0)
    b = np.maximum(0.80 / (1 - rng.random(2000)), 1.0)
    r = analyze_outcomes(_df(np.concatenate([a, b])))
    assert "ks_first_vs_second_half" in r["conclusion"]


def test_runs_and_streak_helpers():
    assert longest_runs(np.array([1, 1, 0, 0, 0, 1], dtype=bool)) == {"longest_true": 2, "longest_false": 3}
    assert runs_test(np.ones(10, dtype=bool))["p_value"] is None


def test_anomaly_detection_gaps_and_missing():
    x = np.maximum(0.99 / (1 - np.random.default_rng(2).random(400)), 1.0)
    d = _df(x)
    d.loc[200:, "ts"] = d.loc[200:, "ts"] + pd.Timedelta(days=30)
    d.loc[0, "multiplier"] = np.nan
    r = analyze_outcomes(d)
    assert r["missing_values"] == 1 and any("gaps" in a for a in r["anomalies"])
