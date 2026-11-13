"""Background research jobs.

Free hosting reality: there is no always-on scheduler. Jobs run (a) in a background
thread when the app starts/opens if the last daily summary is older than 24h, and
(b) on demand via ``run_all`` / the manual trigger. The dashboard never waits for them.
"""
from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime, timedelta, timezone

import pandas as pd

from config.settings import Settings, load_settings
from database.db import Database, new_id, now_iso
from models.schemas import MonteCarloConfig, StrategyConfig
from research import ai_analyst, game_discovery
from research.monte_carlo import run_daily_target
from research.probability_engine import profile
from research.rtp_engine import compare_with_published, empirical_rtp, implied_rtp_from_multipliers
from research.statistical_analysis import analyze_outcomes
from research.strategy_engine import compare_across_seeds
from simulation.games import SUPPORTED_GAMES, build_model, default_models

_lock = threading.Lock()
_thread: threading.Thread | None = None

# (game family for implied-RTP: result distribution is a crash-point style multiplier)
CRASH_STYLE = {"crash", "limbo"}


def _ctx(settings: Settings | None) -> Settings:
    return settings or load_settings()


def _save_report(db: Database, kind: str, title: str, body: str, data: dict) -> None:
    db.execute("INSERT INTO research_reports (id, kind, title, body, data_json, created_at) VALUES (?,?,?,?,?,?)",
               (new_id(), kind, title, body, json.dumps(data, default=str), now_iso()))


def _latest_report(db: Database, kind: str) -> dict | None:
    r = db.query_one("SELECT * FROM research_reports WHERE kind=? ORDER BY created_at DESC LIMIT 1", (kind,))
    return r


# ---------------------------------------------------------------- jobs
def job_catalogue_refresh(db, settings, quick):
    n = game_discovery.seed_catalogue(db, settings.assumed_house_edge)
    return f"catalogue ensured ({n} new games, all UNVERIFIED unless a source was recorded)"


def job_source_verification(db, settings, quick):
    r = game_discovery.check_sources(db)
    return f"sources checked: {r}"


def _datasets_by_game(db: Database):
    return db.query("SELECT game, source_type, COUNT(*) AS n FROM historical_rounds GROUP BY game, source_type")


def _load_rounds(db: Database, game: str, source_type: str) -> pd.DataFrame:
    rows = db.query("SELECT ts, round_id, multiplier, bet, payout FROM historical_rounds WHERE game=? AND source_type=?"
                    " ORDER BY ts", (game, source_type))
    return pd.DataFrame(rows, columns=["ts", "round_id", "multiplier", "bet", "payout"])


def job_historical_analysis(db, settings, quick):
    done = 0
    for grp in _datasets_by_game(db):
        df = _load_rounds(db, grp["game"], grp["source_type"])
        res = analyze_outcomes(df)
        _save_report(db, f"stats:{grp['game']}:{grp['source_type']}", f"Pattern research - {grp['game']} ({grp['source_type']})",
                     res.get("conclusion", "insufficient data"), res)
        done += 1
    return f"analysed {done} dataset group(s)"


def job_rtp_calculation(db, settings, quick):
    done = 0
    for grp in _datasets_by_game(db):
        game, st = grp["game"], grp["source_type"]
        df = _load_rounds(db, game, st)
        g = db.query_one("SELECT published_rtp, confidence FROM games WHERE name=?", (game,))
        pub = float(g["published_rtp"]) if g and g["published_rtp"] else None
        bets = pd.to_numeric(df["bet"], errors="coerce")
        pays = pd.to_numeric(df["payout"], errors="coerce")
        if bets.notna().sum() and pays.notna().sum():
            emp = empirical_rtp(bets.dropna(), pays[bets.notna()].fillna(0.0))
        elif game.lower() in CRASH_STYLE:
            emp = implied_rtp_from_multipliers(pd.to_numeric(df["multiplier"], errors="coerce").dropna())
        else:
            emp = {"status": "INSUFFICIENT_DATA", "n": len(df), "note": "no bet/payout columns and no known result structure"}
        cmp_ = compare_with_published(pub, emp)
        data = {"empirical": emp, "comparison": cmp_, "published_confidence": g["confidence"] if g else "UNVERIFIED",
                "data_origin": st}
        _save_report(db, f"rtp:{game}:{st}", f"RTP check - {game} ({st})", cmp_["status"], data)
        done += 1
    return f"RTP comparison for {done} dataset group(s)"


def job_probability_profiles(db, settings, quick):
    profs = {}
    for g in game_discovery.catalogue(db):
        profs[g["name"]] = (profile(build_model(g["name"], None, settings.assumed_house_edge)).as_dict()
                            if g["name"] in SUPPORTED_GAMES else
                            {"game": g["name"], "status": "INSUFFICIENT_DATA", "reason": "no verified rules / model"})
    _save_report(db, "profiles", "Probability profiles", "assumed-model probability profiles", profs)
    return f"profiled {len(profs)} games"


def job_strategy_comparison(db, settings, quick):
    n = 600 if quick else 3000
    seeds = [11, 22, 33] if quick else [11, 22, 33, 44, 55]
    count = 0
    for model in default_models(settings.assumed_house_edge):
        for kind in ("flat", "percent", "conservative", "progressive"):
            cfg = StrategyConfig(kind=kind, bankroll=1000, base_stake=10 if kind != "progressive" else 5,
                                 stop_loss=300, take_profit=200, max_rounds=200 if quick else 500)
            res = compare_across_seeds(model, cfg, seeds, n)
            db.execute("INSERT INTO strategy_results (id, game, strategy, config_json, n_sessions, seeds_json, result_json,"
                       " created_at) VALUES (?,?,?,?,?,?,?,?)",
                       (new_id(), model.game, kind, json.dumps(cfg.model_dump()), n, json.dumps(seeds),
                        json.dumps({**res, "game": model.game}), now_iso()))
            count += 1
    return f"{count} strategy comparisons x {len(seeds)} seeds"


def job_monte_carlo(db, settings, quick):
    cfg = MonteCarloConfig(n_sessions=1500 if quick else 5000,
                           budgets=[1000.0, 5000.0, 25000.0] if quick else [1000.0, 5000.0, 10000.0, 25000.0, 50000.0, 100000.0],
                           rounds_per_day=300 if quick else 500)
    for model in default_models(settings.assumed_house_edge):
        res = run_daily_target(model, cfg)
        db.execute("INSERT INTO monte_carlo_results (id, game, config_json, result_json, created_at) VALUES (?,?,?,?,?)",
                   (new_id(), model.game, cfg.model_dump_json(), json.dumps(res), now_iso()))
    return f"Monte Carlo daily-target study for {len(SUPPORTED_GAMES)} games ({cfg.n_sessions} sessions each)"


def _latest_per(db: Database, table: str, key_col: str) -> list[dict]:
    rows = db.query(f"SELECT * FROM {table} ORDER BY created_at DESC")  # noqa: S608 (fixed names)
    seen, out = set(), []
    for r in rows:
        k = r[key_col] if table == "monte_carlo_results" else (r["game"], r["strategy"])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def job_daily_summary(db, settings, quick):
    prof = _latest_report(db, "profiles")
    profiles = list(json.loads(prof["data_json"]).values()) if prof else []
    mcs = [json.loads(r["result_json"]) for r in _latest_per(db, "monte_carlo_results", "game")]
    strats = [json.loads(r["result_json"]) for r in _latest_per(db, "strategy_results", "game")]
    strats = [{**s, "game": s.get("game")} for s in strats]
    stats_reports, rtp_reports = [], []
    for r in db.query("SELECT kind, data_json, created_at FROM research_reports WHERE kind LIKE 'stats:%' OR kind LIKE 'rtp:%'"
                      " ORDER BY created_at DESC"):
        label = r["kind"].split(":", 1)[1].replace(":", " / ")
        item = {"label": label, "data": json.loads(r["data_json"])}
        target = stats_reports if r["kind"].startswith("stats:") else rtp_reports
        if label not in {x["label"] for x in target}:
            target.append(item)
    report = ai_analyst.build_daily_report(
        profiles, mcs, strats, game_discovery.catalogue(db), stats_reports, rtp_reports,
        game_discovery.detect_inconsistent_rtp(db))
    _save_report(db, "daily", report["title"], report["body"], {"verdicts": report["verdicts"]})
    return "daily summary written"


JOBS = [
    ("catalogue_refresh", job_catalogue_refresh),
    ("source_verification", job_source_verification),
    ("historical_analysis", job_historical_analysis),
    ("rtp_calculation", job_rtp_calculation),
    ("probability_profiles", job_probability_profiles),
    ("strategy_comparison", job_strategy_comparison),
    ("monte_carlo", job_monte_carlo),
    ("daily_summary", job_daily_summary),
]


def run_job(db: Database, name: str, fn, settings: Settings | None = None, quick: bool = True) -> str:
    settings = _ctx(settings)
    jid = new_id()
    db.execute("INSERT INTO research_jobs (id, job_type, status, started_at) VALUES (?,?,?,?)",
               (jid, name, "RUNNING", now_iso()))
    try:
        detail = fn(db, settings, quick)
        db.execute("UPDATE research_jobs SET status='SUCCESS', detail=?, finished_at=? WHERE id=?", (detail, now_iso(), jid))
        return "SUCCESS"
    except Exception as exc:  # noqa: BLE001  one failing job must not stop the others
        db.execute("UPDATE research_jobs SET status='FAILED', error=?, finished_at=? WHERE id=?",
                   (f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"[:1500], now_iso(), jid))
        return "FAILED"


def run_all(db: Database, settings: Settings | None = None, quick: bool = True) -> dict[str, str]:
    return {name: run_job(db, name, fn, settings, quick) for name, fn in JOBS}


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def start_background(db: Database, settings: Settings | None = None, quick: bool = True) -> bool:
    """Start research in a daemon thread. Returns False if one is already running."""
    global _thread
    with _lock:
        if is_running():
            return False
        _thread = threading.Thread(target=run_all, args=(db, settings, quick), name="bcra-research", daemon=True)
        _thread.start()
        return True


def maybe_run_daily(db: Database, settings: Settings | None = None, max_age_hours: int = 24) -> bool:
    last = db.query_one("SELECT finished_at FROM research_jobs WHERE job_type='daily_summary' AND status='SUCCESS'"
                        " ORDER BY finished_at DESC LIMIT 1")
    if last and last["finished_at"]:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(last["finished_at"])
        if age < timedelta(hours=max_age_hours):
            return False
    return start_background(db, settings, quick=True)


def latest_daily_report(db: Database) -> dict | None:
    return _latest_report(db, "daily")


def recent_jobs(db: Database, limit: int = 10) -> list[dict]:
    return db.query("SELECT job_type, status, started_at, finished_at, detail, error FROM research_jobs "
                    "ORDER BY started_at DESC LIMIT ?", (limit,))
