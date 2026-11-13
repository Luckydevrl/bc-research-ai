"""Mathematical game models.

IMPORTANT: these are *generic, assumed* models built from standard published-style
formulas (e.g. payout = (1 - edge) / win_probability). The house edge is an
ASSUMPTION supplied by configuration. They are NOT verified BC.GAME parameters and
must never be presented as official data.

Every model is a discrete distribution of payout multipliers for one unit staked:
    outcomes = ((multiplier_0, prob_0), (multiplier_1, prob_1), ...)
Net result per unit stake = multiplier - 1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import comb
from typing import Any

import numpy as np

ORIGIN = "ASSUMED_MODEL"


class UnsupportedGame(ValueError):
    """No mathematical model is available for this game (insufficient data)."""


@dataclass(frozen=True)
class GameModel:
    game: str
    category: str
    outcomes: tuple[tuple[float, float], ...]
    params: dict = field(default_factory=dict)
    edge: float = 0.01
    description: str = ""
    origin: str = ORIGIN

    def __post_init__(self):
        total = sum(p for _, p in self.outcomes)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"probabilities sum to {total}, expected 1")
        if any(p < 0 for _, p in self.outcomes) or any(m < 0 for m, _ in self.outcomes):
            raise ValueError("negative probability or multiplier")

    @property
    def mults(self) -> np.ndarray:
        return np.array([m for m, _ in self.outcomes], dtype=float)

    @property
    def probs(self) -> np.ndarray:
        return np.array([p for _, p in self.outcomes], dtype=float)

    @property
    def rtp(self) -> float:
        return float(np.dot(self.mults, self.probs))

    @property
    def key(self) -> str:
        parts = ",".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.game.lower()}[{parts}]@edge={self.edge:g}"


DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "Crash": {"cashout": 2.0},
    "Limbo": {"target": 3.0},
    "Mines": {"tiles": 25, "mines": 3, "reveals": 3},
    "Plinko": {"rows": 8, "risk": "medium"},
    "Dice": {"win_chance": 49.5},
}
CATEGORIES = {"Crash": "Crash", "Limbo": "Instant", "Mines": "Grid", "Plinko": "Pachinko", "Dice": "Dice"}
SUPPORTED_GAMES = tuple(DEFAULT_PARAMS)
_RISK = {"low": 0.4, "medium": 1.2, "high": 4.0}


def _check_edge(edge: float) -> float:
    if not (0.0 <= edge < 0.5):
        raise ValueError("house edge must be in [0, 0.5)")
    return float(edge)


def _two_outcome(win_prob: float, edge: float) -> tuple[tuple[float, float], ...]:
    if not (0 < win_prob < 1):
        raise ValueError("win probability must be in (0, 1)")
    payout = (1.0 - edge) / win_prob
    return ((payout, win_prob), (0.0, 1.0 - win_prob))


def _canonical(game: str) -> str:
    for g in SUPPORTED_GAMES:
        if g.lower() == str(game).strip().lower():
            return g
    raise UnsupportedGame(f"No mathematical model for {game!r}: insufficient data")


def build_model(game: str, params: dict | None = None, edge: float = 0.01) -> GameModel:
    name = _canonical(game)
    edge = _check_edge(edge)
    p = {**DEFAULT_PARAMS[name], **(params or {})}
    if name == "Crash":
        c = float(p["cashout"])
        if not (1.01 <= c <= 1000):
            raise ValueError("cashout must be between 1.01 and 1000")
        outs = _two_outcome((1 - edge) / c, edge)
        desc = f"Cash out at {c}x; P(crash point >= {c}) = (1-edge)/{c}"
    elif name == "Limbo":
        t = float(p["target"])
        if not (1.01 <= t <= 1000):
            raise ValueError("target must be between 1.01 and 1000")
        outs = _two_outcome((1 - edge) / t, edge)
        desc = f"Target multiplier {t}x; P(result >= {t}) = (1-edge)/{t}"
    elif name == "Dice":
        wc = float(p["win_chance"])
        if not (0.01 <= wc <= 98):
            raise ValueError("win_chance must be between 0.01 and 98 (percent)")
        outs = _two_outcome(wc / 100.0, edge)
        desc = f"Win chance {wc}%"
    elif name == "Mines":
        tiles, mines, reveals = int(p["tiles"]), int(p["mines"]), int(p["reveals"])
        if not (2 <= tiles <= 100 and 1 <= mines < tiles and 1 <= reveals <= tiles - mines):
            raise ValueError("invalid Mines parameters")
        win_prob = comb(tiles - mines, reveals) / comb(tiles, reveals)
        outs = _two_outcome(win_prob, edge)
        p = {"tiles": tiles, "mines": mines, "reveals": reveals}
        desc = f"{tiles} tiles, {mines} mines, cash out after {reveals} safe reveals"
    else:  # Plinko
        rows = int(p["rows"])
        risk = str(p["risk"]).lower()
        if not (4 <= rows <= 20) or risk not in _RISK:
            raise ValueError("invalid Plinko parameters")
        probs = np.array([comb(rows, k) for k in range(rows + 1)], dtype=float) / 2 ** rows
        d = np.abs(np.arange(rows + 1) - rows / 2) / (rows / 2)
        raw = 0.3 + _RISK[risk] * d ** 2 * 5.0
        scale = (1.0 - edge) / float(np.dot(probs, raw))
        outs = tuple((float(m * scale), float(pr)) for m, pr in zip(raw, probs))
        p = {"rows": rows, "risk": risk}
        desc = f"Binomial {rows}-row board, '{risk}' risk; generic payout SHAPE scaled to the assumed RTP"
    return GameModel(game=name, category=CATEGORIES[name], outcomes=tuple(outs), params=p, edge=edge,
                     description=desc + " (assumed model, not official BC.GAME data)")


def default_models(edge: float = 0.01) -> list[GameModel]:
    return [build_model(g, None, edge) for g in SUPPORTED_GAMES]
