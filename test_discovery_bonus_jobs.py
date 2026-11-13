import json
from decimal import Decimal
from pathlib import Path

import pytest

from jobs import runner
from models.schemas import PromotionTerms
from research import ai_analyst, game_discovery
from research.bonus_analyzer import evaluate_bonus, load_promotions, save_promotion
from services.importer import import_bytes


def test_catalogue_starts_unverified_with_no_invented_data(db):
    game_discovery.seed_catalogue(db)
    games = game_discovery.catalogue(db)
    assert games and all(g["confidence"] == "UNVERIFIED" and g["published_rtp"] is None and g["source_url"] is None for g in games)
    assert game_discovery.seed_catalogue(db) == 0          # idempotent


def test_official_requires_official_domain(db):
    game_discovery.seed_catalogue(db)
    with pytest.raises(ValueError):
        game_discovery.record_source(db, "Crash", "https://evil.example.com/rtp", 0.99, "OFFICIAL")
    with pytest.raises(ValueError):
        game_discovery.record_source(db, "Crash", "http://bc.game/rtp", 0.99, "OFFICIAL")   # not https
    r = game_discovery.record_source(db, "Crash", "https://bc.game/rtp-page", 99, "OFFICIAL")
    assert r["confidence"] == "VERIFIED_OFFICIAL"
    g = [x for x in game_discovery.catalogue(db) if x["name"] == "Crash"][0]
    assert float(g["published_rtp"]) == pytest.approx(0.99) and g["verified_on"]
    game_discovery.record_source(db, "Crash", "https://blog.example.com/x", 0.97, "USER_CLAIM")
    assert [x for x in game_discovery.catalogue(db) if x["name"] == "Crash"][0]["confidence"] == "VERIFIED_OFFICIAL"  # no downgrade


def test_inconsistent_rtp_detected(db):
    game_discovery.seed_catalogue(db)
    game_discovery.record_source(db, "Mines", "https://bc.game/a", 0.99, "OFFICIAL")
    game_discovery.record_source(db, "Mines", "https://forum.example/b", 0.96, "USER_CLAIM")
    inc = game_discovery.detect_inconsistent_rtp(db)
    assert inc and inc[0]["game"] == "Mines"
    assert any("INCONSISTENT" in l for l in ai_analyst.data_findings(game_discovery.catalogue(db), [], [], inc))


def test_inaccessible_source_is_recorded_not_bypassed(db):
    game_discovery.seed_catalogue(db)
    game_discovery.record_source(db, "Crash", "https://bc.game/x", None, "OFFICIAL")
    calls = []
    res = game_discovery.check_sources(db, fetcher=lambda u: calls.append(u) or 403)
    assert res == {"checked": 1, "ok": 0, "inaccessible": 1} and len(calls) == 1       # no retry
    row = db.query_one("SELECT status, http_status, note FROM game_sources")
    assert row["status"] == "INACCESSIBLE" and row["http_status"] == 403 and "bypassed" in row["note"]

    def boom(u):
        raise ConnectionError("blocked")
    assert game_discovery.check_sources(db, fetcher=boom)["inaccessible"] == 1


def test_bonus_requires_complete_terms_and_explicit_withdrawability():
    assert evaluate_bonus(PromotionTerms(name="x"))["status"] == "INSUFFICIENT_TERMS"
    base = dict(name="x", bonus_amount="100", wagering_multiple="30", wagering_base="bonus", house_edge=0.01)
    assert evaluate_bonus(PromotionTerms(**base))["status"] == "NOT_VALUED_WITHDRAWABILITY_UNKNOWN"
    v = evaluate_bonus(PromotionTerms(**base, withdrawable_explicit=True))
    assert v["status"] == "VALUED" and Decimal(v["expected_value"]) == Decimal("70.00")     # 100 - 3000*1%
    neg = evaluate_bonus(PromotionTerms(**{**base, "house_edge": 0.05}, withdrawable_explicit=True))
    assert Decimal(neg["expected_value"]) == Decimal("-50.00") and neg["positive_expected_value"] is False


def test_demo_promotions_load_and_save(db):
    p = Path(__file__).resolve().parent.parent / "sample_data" / "demo_promotions.json"
    promos = load_promotions(str(p))
    assert "DEMO ONLY" in promos[0].name
    save_promotion(db, promos[0])
    assert db.query_one("SELECT COUNT(*) n FROM promotions")["n"] == 1


def test_run_all_jobs_and_daily_report(db):
    p = Path(__file__).resolve().parent.parent / "sample_data" / "demo_synthetic_crash_rounds.csv"
    import_bytes(db, p.read_bytes(), p.name, "SYNTHETIC_DEMO")
    res = runner.run_all(db, quick=True)
    assert set(res.values()) == {"SUCCESS"}, db.query("SELECT job_type, error FROM research_jobs WHERE status='FAILED'")
    rep = runner.latest_daily_report(db)
    body = rep["body"].lower()
    assert "negative expected value" in body and "synthetic_demo" in body
    assert "insufficient data" in body                                  # Hilo/Keno/Wheel have no rules
    for phrase in ai_analyst.FORBIDDEN_PROMISES:
        assert phrase not in body
    assert db.query_one("SELECT COUNT(*) n FROM monte_carlo_results")["n"] == 5
    assert db.query_one("SELECT COUNT(*) n FROM strategy_results")["n"] == 20
    assert db.query_one("SELECT COUNT(*) n FROM research_reports WHERE kind LIKE 'rtp:%'")["n"] == 1


def test_failed_job_is_recorded_and_others_continue(db):
    def bad(*a):
        raise RuntimeError("nope")
    runner.JOBS.insert(0, ("bad", bad))
    try:
        res = runner.run_all(db, quick=True)
    finally:
        runner.JOBS.pop(0)
    assert res["bad"] == "FAILED" and res["daily_summary"] == "SUCCESS"
    assert "nope" in db.query_one("SELECT error FROM research_jobs WHERE job_type='bad'")["error"]


def test_maybe_run_daily_skips_when_fresh(db, monkeypatch):
    runner.run_all(db, quick=True)
    started = []
    monkeypatch.setattr(runner, "start_background", lambda *a, **k: started.append(1) or True)
    assert runner.maybe_run_daily(db) is False and not started


def test_analyst_never_invents_without_data(db):
    body = ai_analyst.build_daily_report([], [], [], [], [], [], [])["body"]
    assert "No historical dataset has been imported" in body and "Monte Carlo has not been run" in body
