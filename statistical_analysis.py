"""Statistical research on historical outcome data.

Purpose: describe the data and detect DATA ANOMALIES. A statistically significant
departure from randomness is reported as an anomaly to investigate (data quality,
duplicated/edited rows, non-independent source) - never as a way to predict rounds.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats

MIN_SAMPLE = 200
ALPHA = 0.05
N_TESTS = 3  # runs test, Ljung-Box, KS split -> Bonferroni


def longest_runs(flags: np.ndarray) -> dict:
    best = {True: 0, False: 0}
    cur, val = 0, None
    for f in flags:
        f = bool(f)
        if f == val:
            cur += 1
        else:
            val, cur = f, 1
        best[val] = max(best[val], cur)
    return {"longest_true": best[True], "longest_false": best[False]}


def expected_longest_run(n: int, p: float) -> float:
    if not (0 < p < 1) or n <= 0:
        return float("nan")
    return math.log(n * (1 - p)) / math.log(1 / p)


def runs_test(flags: np.ndarray) -> dict:
    b = np.asarray(flags, dtype=bool)
    n = len(b)
    n1 = int(b.sum())
    n2 = n - n1
    if n1 == 0 or n2 == 0 or n < 3:
        return {"p_value": None, "note": "all observations on one side of the threshold"}
    runs = 1 + int((b[1:] != b[:-1]).sum())
    mu = 2 * n1 * n2 / n + 1
    var = 2 * n1 * n2 * (2 * n1 * n2 - n) / (n * n * (n - 1))
    z = (runs - mu) / math.sqrt(var) if var > 0 else 0.0
    return {"runs": runs, "expected_runs": mu, "z": z, "p_value": float(2 * stats.norm.sf(abs(z)))}


def autocorrelation(x: np.ndarray, max_lag: int = 10) -> dict:
    x = np.asarray(x, dtype=float)
    n = len(x)
    xc = x - x.mean()
    denom = float((xc ** 2).sum())
    if denom == 0:
        return {"acf": [], "ljung_box_p": None, "bound95": None}
    lags = min(max_lag, n // 4)
    acf = [float((xc[:-k] * xc[k:]).sum() / denom) for k in range(1, lags + 1)]
    q = n * (n + 2) * sum(r * r / (n - k) for k, r in enumerate(acf, start=1))
    return {"acf": acf, "bound95": 1.96 / math.sqrt(n), "ljung_box_q": q,
            "ljung_box_p": float(stats.chi2.sf(q, lags))}


def analyze_outcomes(df: pd.DataFrame, threshold: float = 2.0, min_n: int = MIN_SAMPLE) -> dict:
    """df needs a 'multiplier' column; optional 'ts' and 'round_id'."""
    if df is None or len(df) == 0 or "multiplier" not in df:
        return {"status": "INSUFFICIENT_DATA", "n": 0, "min_required": min_n}
    d = df.copy()
    if "ts" in d:
        d = d.sort_values("ts", kind="stable")
    x = pd.to_numeric(d["multiplier"], errors="coerce")
    missing = int(x.isna().sum())
    x = x.dropna().to_numpy(dtype=float)
    n = len(x)
    if n < min_n:
        return {"status": "INSUFFICIENT_DATA", "n": n, "min_required": min_n, "missing_values": missing}

    bins = [0, 1, 1.5, 2, 3, 5, 10, 100, np.inf]
    counts, _ = np.histogram(x, bins=bins)
    labels = ["[0,1)", "[1,1.5)", "[1.5,2)", "[2,3)", "[3,5)", "[5,10)", "[10,100)", "100+"]
    flags = x >= threshold
    p_hat = float(flags.mean())
    runs = longest_runs(flags)
    lx = np.log1p(x)
    q1, q3 = np.percentile(lx, [25, 75])
    iqr = q3 - q1
    outliers = int(((lx > q3 + 3 * iqr) | (lx < q1 - 3 * iqr)).sum()) if iqr > 0 else 0
    half = n // 2
    ks = stats.ks_2samp(x[:half], x[half:])
    rt = runs_test(flags)
    ac = autocorrelation(lx)

    anomalies = []
    if "round_id" in d and d["round_id"].duplicated().any():
        anomalies.append("duplicate round ids")
    if "ts" in d and len(d) > 2:
        t = pd.to_datetime(d["ts"], utc=True, errors="coerce")
        if t.isna().any():
            anomalies.append("unparseable timestamps")
        else:
            gaps = t.diff().dt.total_seconds().dropna()
            med = gaps.median()
            if med > 0 and (gaps > 50 * med).any():
                anomalies.append("large gaps in timestamps (possible missing rounds)")
    if (x < 0).any():
        anomalies.append("negative multipliers")

    pvals = {"runs_test": rt.get("p_value"), "ljung_box": ac.get("ljung_box_p"), "ks_first_vs_second_half": float(ks.pvalue)}
    alpha_adj = ALPHA / N_TESTS
    significant = [k for k, v in pvals.items() if v is not None and v < alpha_adj]
    if significant:
        conclusion = ("Statistically significant deviation in: " + ", ".join(significant) +
                      ". Treat this as a possible DATA-QUALITY anomaly (non-independent, edited or truncated "
                      "data) to investigate. It is not evidence that future rounds can be predicted.")
    else:
        conclusion = ("No statistically significant deviation from independence/stationarity after multiple-test "
                      "correction. Observed streaks and fluctuations are consistent with randomness.")
    return {
        "status": "OK", "n": n, "missing_values": missing, "threshold": threshold,
        "describe": {"mean": float(x.mean()), "median": float(np.median(x)), "std": float(x.std(ddof=1)),
                     "variance": float(x.var(ddof=1)), "min": float(x.min()), "max": float(x.max()),
                     "p95": float(np.percentile(x, 95)), "p99": float(np.percentile(x, 99))},
        "distribution": {lab: int(c) for lab, c in zip(labels, counts)},
        "p_hat_at_or_above_threshold": p_hat,
        "streaks": {**runs, "expected_longest_true": expected_longest_run(n, p_hat),
                    "expected_longest_false": expected_longest_run(n, 1 - p_hat)},
        "outliers_log_iqr_x3": outliers,
        "runs_test": rt, "autocorrelation": ac,
        "ks_first_vs_second_half": {"statistic": float(ks.statistic), "p_value": float(ks.pvalue)},
        "p_values": pvals, "alpha_bonferroni": alpha_adj,
        "anomalies": anomalies, "conclusion": conclusion,
        "predictive_claim": "NONE - past outcomes are not used to predict future rounds",
    }
