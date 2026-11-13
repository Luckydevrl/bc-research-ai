"""Sidebar controls (collapsed by default, so the main dashboard stays minimal).

Everything technical lives here; the main page never shows it.
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

import streamlit as st

from database.db import DatabaseError
from jobs import runner
from research.strategy_engine import STOP_NAMES  # noqa: F401  (kept for debugging displays)
from services.accounting import AccountingError
from services.export import backup_json, export_simulations_csv
from services.importer import ImportFailure, import_bytes
from services.ratelimit import RateLimitExceeded
from services.simulation_service import run_simulation
from simulation.games import SUPPORTED_GAMES

DEMO_CSV = Path(__file__).resolve().parent.parent / "sample_data" / "demo_synthetic_crash_rounds.csv"


def _nonce() -> str:
    return st.session_state.setdefault("nonce", uuid.uuid4().hex)


def _rotate() -> None:
    st.session_state["nonce"] = uuid.uuid4().hex


def _dec(txt: str) -> Decimal | None:
    try:
        return Decimal(txt.strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def render_sidebar(svc) -> bool:
    """Returns True if state changed and the page should rerun."""
    changed = False
    sb = st.sidebar
    sb.header("Controls")
    sb.caption("All amounts are VIRTUAL research money, never a BC.GAME wallet.")

    # --- budget
    sb.subheader("Virtual budget")
    amount_txt = sb.text_input("Amount (₹)", value="500", key="budget_amount")
    c1, c2 = sb.columns(2)
    if c1.button("Add", key="btn_add"):
        amt = _dec(amount_txt)
        try:
            if amt is None:
                raise AccountingError("Enter a valid number")
            svc.acct.add_budget(amt, key=f"ui-add:{_nonce()}", note="manual (UI)")
            _rotate()
            changed = True
        except AccountingError as exc:
            sb.error(str(exc))
    if c2.button("Reduce", key="btn_reduce"):
        amt = _dec(amount_txt)
        try:
            if amt is None:
                raise AccountingError("Enter a valid number")
            svc.acct.reduce_budget(amt, key=f"ui-red:{_nonce()}", note="manual (UI)")
            _rotate()
            changed = True
        except AccountingError as exc:
            sb.error(str(exc))

    # --- simulation
    sb.subheader("Run a virtual simulation")
    game = sb.selectbox("Game (assumed math model)", SUPPORTED_GAMES, key="sim_game")
    rounds = sb.slider("Rounds", 10, svc.settings.max_session_rounds, 100, step=10, key="sim_rounds")
    strategy = sb.selectbox("Staking", ["flat", "percent", "conservative", "progressive"], key="sim_strategy")
    if sb.button("Run simulation", key="btn_sim"):
        try:
            with st.spinner("Simulating..."):
                res = run_simulation(svc.acct, game=game, rounds=rounds, strategy=strategy,
                                     edge=svc.settings.assumed_house_edge, max_rounds=svc.settings.max_session_rounds,
                                     limiter=svc.limiter)
            sb.success(f"{res['game']}: virtual result {res['pnl']:+} over {res['rounds']} rounds")
            changed = True
        except (AccountingError, RateLimitExceeded, ValueError, DatabaseError) as exc:
            sb.error(str(exc))

    # --- research
    sb.subheader("Research")
    if sb.button("Run background research now", key="btn_research"):
        started = runner.start_background(svc.db, svc.settings, quick=True)
        sb.info("Research started in the background." if started else "Research is already running.")
    if runner.is_running():
        sb.caption("Research is running... reload later to see results.")
    with sb.expander("Latest research conclusions"):
        rep = runner.latest_daily_report(svc.db)
        if rep:
            st.text(rep["body"])
            st.caption(f"Generated {rep['created_at'][:19].replace('T', ' ')} UTC. Simulated / assumed-model results only.")
        else:
            st.caption("No summary yet - run research.")
        jobs = runner.recent_jobs(svc.db, 8)
        if jobs:
            st.caption("Recent jobs: " + "; ".join(f"{j['job_type']}={j['status']}" for j in jobs))

    # --- data
    sb.subheader("Historical data")
    up = sb.file_uploader("Import CSV / JSON (your own legitimate data)", type=["csv", "json"], key="uploader")
    if up is not None and sb.button("Import file", key="btn_import"):
        try:
            rep = import_bytes(svc.db, up.getvalue(), up.name, "USER_IMPORTED",
                               svc.settings.max_upload_bytes, svc.settings.max_upload_rows)
            sb.success(f"{rep.status}: {rep.inserted} stored, {rep.malformed_rows} malformed, "
                       f"{rep.duplicates_in_file + rep.duplicates_in_db} duplicates (labelled USER_IMPORTED, unverified)")
        except (ImportFailure, DatabaseError, ValueError) as exc:
            sb.error(str(exc))
    if DEMO_CSV.exists() and sb.button("Load SYNTHETIC demo dataset", key="btn_demo_data"):
        try:
            rep = import_bytes(svc.db, DEMO_CSV.read_bytes(), DEMO_CSV.name, "SYNTHETIC_DEMO")
            sb.success(f"{rep.status}: {rep.inserted} synthetic rows (NOT real BC.GAME data)")
        except (ImportFailure, DatabaseError) as exc:
            sb.error(str(exc))

    # --- export / reset
    sb.subheader("Export & demo")
    sb.download_button("Download simulation results (CSV)", export_simulations_csv(svc.db),
                       file_name="bcra_simulations.csv", mime="text/csv")
    sb.download_button("Download full backup (JSON)", backup_json(svc.db),
                       file_name="bcra_backup.json", mime="application/json")
    confirm = sb.checkbox("I understand: restart the demo with a fresh ₹1,000 virtual budget (history is archived)",
                          key="confirm_reset")
    if sb.button("Restart demo session", key="btn_reset", disabled=not confirm):
        svc.acct.reset_demo(svc.settings.default_budget)
        _rotate()
        changed = True
    if svc.db.fallback_reason:
        sb.warning(svc.db.fallback_reason)
    sb.caption(f"Database: {svc.db.backend}. Demo/simulated data is never official BC.GAME data.")
    return changed
