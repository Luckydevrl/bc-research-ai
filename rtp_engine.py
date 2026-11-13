"""RTP verification helpers.

RTP is a long-run statistical expectation, not a promise about short-term returns,
and nothing here claims that past rounds predict the next round.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

MIN_SAMPLE = 1000
Z95 = 1.959963984540054


def normalize_rtp(value: float | None) -> float | None:
    """Accept 0.99 or 99 (percent) and return a fraction."""
    if value is None:
        return None
    v = float(value)
    if v > 1.5:
        v /= 100.0
    if not (0 < v <= 1.5):
        raise ValueError("RTP out of range")
    return v


def empirical_rtp(bets: Sequence[float], payouts: Sequence[float], min_n: int = MIN_SAMPLE) -> dict:
    """Ratio estimator sum(payout)/sum(bet) with a delta-method 95% CI."""
    b = np.asarray(bets, dtype=float)
    p = np.asarray(payouts, dtype=float)
    ok = np.isfinite(b) & np.isfinite(p) & (b > 0)
    b, p = b[ok], p[ok]
    n = len(b)
    if n < min_n:
        return {"status": "INSUFFICIENT_DATA", "n": n, "min_required": min_n}
    rtp = float(p.sum() / b.sum())
    resid = p - rtp * b
    se = float(math.sqrt(resid.var(ddof=1) / n) / b.mean())
    return {"status": "OK", "n": n, "rtp": rtp, "se": se, "ci95": [rtp - Z95 * se, rtp + Z95 * se],
            "method": "ratio estimator on user-supplied bet/payout columns"}


def implied_rtp_from_multipliers(multipliers: Sequence[float], thresholds: Sequence[float] = (1.5, 2, 3, 5, 10),
                                 min_n: int = MIN_SAMPLE) -> dict:
    """For crash/limbo-style result distributions P(M >= m) = RTP / m, so m * P_hat(M >= m)
    estimates RTP. Only meaningful if the column is the game's *result* multiplier."""
    x = np.asarray(multipliers, dtype=float)
    x = x[np.isfinite(x) & (x >= 0)]
    n = len(x)
    if n < min_n:
        return {"status": "INSUFFICIENT_DATA", "n": n, "min_required": min_n}
    rows = []
    for m in thresholds:
        k = int((x >= m).sum())
        phat = k / n
        # Wilson interval for the proportion, then scale by m
        denom = 1 + Z95 ** 2 / n
        centre = (phat + Z95 ** 2 / (2 * n)) / denom
        half = Z95 * math.sqrt(phat * (1 - phat) / n + Z95 ** 2 / (4 * n * n)) / denom
        rows.append({"threshold": float(m), "count": k, "implied_rtp": m * phat,
                     "ci95": [m * max(0.0, centre - half), m * min(1.0, centre + half)]})
    return {"status": "OK", "n": n, "by_threshold": rows,
            "method": "implied RTP = m * P(result >= m); valid only for crash/limbo-style result distributions"}


def compare_with_published(published_rtp: float | None, empirical: dict) -> dict:
    """Flag a discrepancy only when the published RTP lies outside the empirical 95% CI."""
    pub = normalize_rtp(published_rtp)
    if pub is None:
        return {"status": "NO_PUBLISHED_RTP"}
    if empirical.get("status") != "OK":
        return {"status": "INSUFFICIENT_DATA", "published_rtp": pub, "n": empirical.get("n", 0)}
    if "ci95" in empirical:
        lo, hi = empirical["ci95"]
        flagged = not (lo <= pub <= hi)
        return {"status": "DISCREPANCY" if flagged else "CONSISTENT", "published_rtp": pub,
                "empirical_rtp": empirical["rtp"], "ci95": [lo, hi], "n": empirical["n"]}
    rows = empirical["by_threshold"]
    bad = [r for r in rows if not (r["ci95"][0] <= pub <= r["ci95"][1])]
    # with several thresholds, tolerate chance: flag only if more than 1 of them disagree
    flagged = len(bad) >= 2
    return {"status": "DISCREPANCY" if flagged else "CONSISTENT", "published_rtp": pub,
            "thresholds_outside_ci": [r["threshold"] for r in bad], "n": empirical["n"]}
