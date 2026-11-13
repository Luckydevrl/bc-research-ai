"""Pydantic models for validated inputs."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

StrategyKind = Literal["flat", "percent", "conservative", "progressive"]
SOURCE_TYPES = ("USER_IMPORTED", "PUBLIC", "SYNTHETIC_DEMO")


class StrategyConfig(BaseModel):
    kind: StrategyKind = "flat"
    bankroll: float = Field(gt=0, le=1e12)
    base_stake: float = Field(default=1.0, gt=0, le=1e12)
    pct: float = Field(default=0.01, gt=0, le=1)              # 'percent' strategy
    progression_factor: float = Field(default=2.0, ge=1.0, le=10.0)
    max_progression_steps: int = Field(default=5, ge=0, le=20)
    stop_loss: Optional[float] = Field(default=None, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)
    max_rounds: int = Field(default=100, ge=1, le=100_000)
    min_stake: float = Field(default=0.01, gt=0)

    @model_validator(mode="after")
    def _check(self) -> "StrategyConfig":
        if self.kind != "percent" and self.base_stake > self.bankroll:
            raise ValueError("base_stake cannot exceed bankroll")
        return self


class MonteCarloConfig(BaseModel):
    target: float = Field(default=1000.0, gt=0)
    budgets: list[float] = Field(default_factory=lambda: [1000.0, 5000.0, 10000.0, 50000.0])
    stake_fractions: list[float] = Field(default_factory=lambda: [0.01, 0.05, 0.2])  # flat stake = budget * fraction
    stop_loss_fraction: Optional[float] = Field(default=None, gt=0, le=1)
    rounds_per_day: int = Field(default=500, ge=1, le=10_000)
    n_sessions: int = Field(default=3000, ge=100, le=100_000)
    days: int = Field(default=30, ge=1, le=365)
    seed: int = 12345
    strategy: StrategyKind = "flat"

    @field_validator("budgets")
    @classmethod
    def _budgets(cls, v: list[float]) -> list[float]:
        if not v or any(b <= 0 for b in v):
            raise ValueError("budgets must be positive")
        return v

    @field_validator("stake_fractions")
    @classmethod
    def _fractions(cls, v: list[float]) -> list[float]:
        if not v or any(not (0 < f <= 1) for f in v):
            raise ValueError("stake fractions must be in (0, 1]")
        return v


def _blank_to_none(v):
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v


class HistoricalRecord(BaseModel):
    game: str = Field(min_length=1, max_length=64)
    round_id: str = Field(min_length=1, max_length=128)
    timestamp: datetime
    result: Optional[str] = Field(default=None, max_length=256)
    multiplier: Optional[float] = None
    bet: Optional[Decimal] = None
    payout: Optional[Decimal] = None

    @field_validator("game", "round_id", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("result", "multiplier", "bet", "payout", mode="before")
    @classmethod
    def _blank(cls, v):
        return _blank_to_none(v)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _ts(cls, v):
        if isinstance(v, datetime):
            dt = v
        else:
            s = str(v).strip()
            if not s:
                raise ValueError("timestamp missing")
            try:
                num = float(s)
                if not math.isfinite(num) or num <= 0:
                    raise ValueError("bad epoch")
                dt = datetime.fromtimestamp(num / 1000.0 if num > 1e11 else num, tz=timezone.utc)
            except ValueError:
                try:
                    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValueError(f"unparseable timestamp: {s!r}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @field_validator("multiplier")
    @classmethod
    def _mult(cls, v):
        if v is not None and (not math.isfinite(v) or v < 0):
            raise ValueError("multiplier must be finite and >= 0")
        return v

    @field_validator("bet", "payout")
    @classmethod
    def _money(cls, v):
        if v is not None and (not v.is_finite() or v < 0):
            raise ValueError("amounts must be finite and >= 0")
        return v

    @model_validator(mode="after")
    def _has_outcome(self) -> "HistoricalRecord":
        has_money = self.bet is not None and self.payout is not None
        if self.multiplier is None and self.result is None and not has_money:
            raise ValueError("record needs multiplier, result, or bet+payout")
        return self


class PromotionTerms(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_url: Optional[str] = None
    eligibility: Optional[str] = None
    wagering_multiple: Optional[Decimal] = None
    wagering_base: Optional[Literal["bonus", "bonus_plus_deposit"]] = None
    expiry: Optional[str] = None
    withdrawal_restrictions: Optional[str] = None
    terms: Optional[str] = None
    bonus_amount: Optional[Decimal] = None
    deposit_amount: Decimal = Decimal("0")
    withdrawable_explicit: bool = False   # True only if OFFICIAL terms say bonus winnings are withdrawable
    house_edge: Optional[float] = Field(default=None, ge=0, lt=1)  # of the game it would be wagered on

    @field_validator("wagering_multiple", "bonus_amount", "deposit_amount", mode="before")
    @classmethod
    def _dec(cls, v):
        v = _blank_to_none(v)
        if v is None:
            return None
        try:
            return Decimal(str(v))
        except InvalidOperation as exc:
            raise ValueError("invalid number") from exc
