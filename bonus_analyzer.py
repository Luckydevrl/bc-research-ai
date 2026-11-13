"""Bonus / promotion research (Module H).

A bonus is valued only when its terms are complete, and bonus credits are never treated
as withdrawable cash unless OFFICIAL terms explicitly say so.
"""
from __future__ import annotations

import json
from decimal import Decimal

from database.db import Database, new_id, now_iso
from models.schemas import PromotionTerms


def evaluate_bonus(p: PromotionTerms) -> dict:
    missing = [n for n, v in (("bonus_amount", p.bonus_amount), ("wagering_multiple", p.wagering_multiple),
                              ("wagering_base", p.wagering_base), ("house_edge", p.house_edge)) if v is None]
    if missing:
        return {"status": "INSUFFICIENT_TERMS", "missing": missing, "value": None,
                "note": "Terms are incomplete, so no mathematical value is computed."}
    if not p.withdrawable_explicit:
        return {"status": "NOT_VALUED_WITHDRAWABILITY_UNKNOWN", "value": None,
                "note": "Official terms do not explicitly state that bonus winnings are withdrawable; "
                        "the credit is treated as NON-withdrawable."}
    base = p.bonus_amount if p.wagering_base == "bonus" else p.bonus_amount + p.deposit_amount
    required_turnover = base * p.wagering_multiple
    expected_cost = required_turnover * Decimal(str(p.house_edge))
    value = p.bonus_amount - expected_cost
    return {"status": "VALUED", "required_turnover": str(required_turnover.quantize(Decimal("0.01"))),
            "expected_wagering_cost": str(expected_cost.quantize(Decimal("0.01"))),
            "expected_value": str(value.quantize(Decimal("0.01"))),
            "positive_expected_value": value > 0,
            "note": ("Expected-value approximation: bonus minus house edge x required turnover. Ignores variance, "
                     "max-bet limits, game weighting and the chance of busting before completing wagering; "
                     "real outcomes differ.")}


def save_promotion(db: Database, p: PromotionTerms) -> str:
    pid = new_id()
    db.execute(
        "INSERT INTO promotions (id, name, source_url, eligibility, wagering_multiple, wagering_base, expiry,"
        " withdrawal_restrictions, terms, bonus_amount, deposit_amount, withdrawable_explicit, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, p.name, p.source_url, p.eligibility, None if p.wagering_multiple is None else str(p.wagering_multiple),
         p.wagering_base, p.expiry, p.withdrawal_restrictions, p.terms,
         None if p.bonus_amount is None else str(p.bonus_amount), str(p.deposit_amount),
         1 if p.withdrawable_explicit else 0, now_iso()))
    return pid


def load_promotions(path: str) -> list[PromotionTerms]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return [PromotionTerms(**item) for item in raw]
