"""BC Research AI - Streamlit entry point.

Main page: ONLY Available Balance, AI Balance and Game-wise P/L (all virtual).
Controls live in the (collapsed) sidebar; all research runs in the background.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402

from config.settings import Settings, load_settings  # noqa: E402
from database.db import Database, DatabaseError, get_database  # noqa: E402
from frontend.controls import render_sidebar  # noqa: E402
from frontend.dashboard import build_view_model, render_dashboard_html  # noqa: E402
from jobs import runner  # noqa: E402
from services.accounting import Accounting  # noqa: E402
from services.ratelimit import RateLimiter  # noqa: E402


@dataclass
class Services:
    settings: Settings
    db: Database
    acct: Accounting
    limiter: RateLimiter


@st.cache_resource(show_spinner=False)
def boot() -> Services:
    settings = load_settings()
    db = get_database(settings)                      # migrations applied here; SQLite fallback if needed
    acct = Accounting(db)
    acct.ensure_wallet(settings.default_budget)      # demo mode: works with no credentials
    acct.recover_stale_sessions()
    runner.start_background(db, settings, quick=True) if _needs_research(db) else None
    return Services(settings, db, acct, RateLimiter(settings.sims_per_minute, 60))


def _needs_research(db: Database) -> bool:
    return db.query_one("SELECT 1 AS x FROM research_jobs WHERE job_type='daily_summary' AND status='SUCCESS'") is None


def main() -> None:
    st.set_page_config(page_title="BC Research AI", page_icon="📊", layout="centered",
                       initial_sidebar_state="collapsed")
    try:
        svc = boot()
        runner.maybe_run_daily(svc.db, svc.settings)  # no-op unless the last summary is >24h old
        if render_sidebar(svc):
            st.rerun()
        st.markdown(render_dashboard_html(build_view_model(svc.acct)), unsafe_allow_html=True)
    except DatabaseError as exc:
        st.error(f"The database is unavailable: {exc}. Check BCRA_DB_PATH / DATABASE_URL and reload.")
    except Exception as exc:  # noqa: BLE001  never show a raw traceback on the dashboard
        st.error(f"Something went wrong: {type(exc).__name__}. Please reload the page.")


main()
