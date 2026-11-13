from decimal import Decimal

import pytest

from database.db import Database, DatabaseError
from services.accounting import Accounting, AccountingError, InsufficientBudget, InvalidAmount, D
from services.simulation_service import run_simulation


def test_initial_balances(acct):
    s = acct.snapshot()
    assert s.available == Decimal("1000.00") and s.ai_balance == 0 and s.equity == Decimal("1000.00")


def test_equity_identity_and_no_double_counting(acct):
    sid = acct.open_session(game="Crash", model_key="k", strategy="flat", config={}, seed=1, bankroll="400")
    s = acct.snapshot()
    assert s.allocated == Decimal("400.00") and s.available == Decimal("600.00")
    assert s.equity == Decimal("1000.00")  # allocation moves money, creates none
    acct.close_session(sid, pnl="-50.25", rounds=10, wins=4, losses=6, max_drawdown=60, longest_losing_streak=3, stop_reason="x")
    s = acct.snapshot()
    assert s.allocated == 0 and s.available == Decimal("1000.00") and s.ai_balance == Decimal("-50.25")
    assert s.equity == s.available + s.allocated + s.ai_balance == Decimal("949.75")
    assert acct.verify_ledger()["ok"]


def test_settle_twice_is_rejected(acct):
    sid = acct.open_session(game="Crash", model_key="k", strategy="flat", config={}, seed=1, bankroll="100")
    kw = dict(pnl="5", rounds=1, wins=1, losses=0, max_drawdown=0, longest_losing_streak=0, stop_reason="x")
    acct.close_session(sid, **kw)
    with pytest.raises(AccountingError):
        acct.close_session(sid, **kw)
    assert acct.snapshot().ai_balance == Decimal("5.00")


def test_loss_cannot_exceed_allocation(acct):
    sid = acct.open_session(game="Crash", model_key="k", strategy="flat", config={}, seed=1, bankroll="100")
    with pytest.raises(AccountingError):
        acct.close_session(sid, pnl="-100.01", rounds=1, wins=0, losses=1, max_drawdown=0, longest_losing_streak=1, stop_reason="x")


def test_idempotent_budget_changes(acct):
    acct.add_budget("500", key="same")
    acct.add_budget("500", key="same")
    assert acct.snapshot().budget == Decimal("1500.00")
    acct.reduce_budget("200", key="r1")
    acct.reduce_budget("200", key="r1")
    assert acct.snapshot().budget == Decimal("1300.00")
    assert acct.verify_ledger()["ok"]


def test_cannot_reduce_below_allocated_or_zero(acct):
    acct.open_session(game="Crash", model_key="k", strategy="flat", config={}, seed=1, bankroll="800")
    with pytest.raises(InsufficientBudget):
        acct.reduce_budget("300")
    with pytest.raises(InsufficientBudget):
        acct.reduce_budget("5000")


def test_zero_budget_blocks_simulation(db):
    a = Accounting(db)
    a.ensure_wallet("0")
    assert a.snapshot().available == 0
    with pytest.raises(InsufficientBudget):
        run_simulation(a, game="Crash", rounds=10, bankroll=Decimal("0.01"))


@pytest.mark.parametrize("bad", ["0", "-5", "abc", "nan", "inf", "1e99"])
def test_invalid_amounts(acct, bad):
    with pytest.raises(AccountingError):
        acct.add_budget(bad)


def test_very_large_values(acct):
    acct.add_budget("999999999000")
    s = acct.snapshot()
    assert s.budget == Decimal("1000000000000.00")
    with pytest.raises(InvalidAmount):
        acct.add_budget("1")


def test_decimal_precision_no_float_drift(acct):
    for i in range(10):
        acct.add_budget("0.10", key=f"k{i}")
    assert acct.snapshot().budget == Decimal("1001.00")
    assert D(0.1) + D(0.2) == Decimal("0.30")


def test_negative_results_and_game_breakdown(acct):
    r1 = run_simulation(acct, game="Crash", rounds=50, seed=3)
    r2 = run_simulation(acct, game="Mines", rounds=20, seed=4)
    bd = {g["game"]: g for g in acct.game_breakdown()}
    assert set(bd) == {"Crash", "Mines"}                       # only games with activity
    assert bd["Crash"]["pnl"] == r1["pnl"] and bd["Crash"]["rounds"] == r1["rounds"]
    assert sum(g["pnl"] for g in bd.values()) == acct.snapshot().ai_balance == r1["pnl"] + r2["pnl"]
    assert acct.verify_ledger()["ok"]


def test_reset_archives_history(acct):
    run_simulation(acct, game="Crash", rounds=30, seed=1)
    before = len(acct.db.query("SELECT id FROM virtual_transactions"))
    s = acct.reset_demo("1000")
    assert s.epoch == 2 and s.ai_balance == 0 and acct.game_breakdown() == []
    assert len(acct.db.query("SELECT id FROM virtual_transactions")) > before   # nothing deleted
    assert len(acct.db.query("SELECT id FROM simulation_sessions")) == 1
    assert acct.verify_ledger()["ok"]


def test_stale_session_recovery(acct):
    sid = acct.open_session(game="Crash", model_key="k", strategy="flat", config={}, seed=1, bankroll="100")
    assert acct.recover_stale_sessions(max_age_minutes=0) == 1
    s = acct.snapshot()
    assert s.allocated == 0 and s.ai_balance == 0
    assert acct.verify_ledger()["ok"]


def test_failed_simulation_releases_allocation(acct, monkeypatch):
    import services.simulation_service as ss
    monkeypatch.setattr(ss, "simulate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        run_simulation(acct, game="Crash", rounds=10)
    assert acct.snapshot().allocated == 0


def test_ledger_is_append_only(acct):
    with pytest.raises(DatabaseError):
        acct.db.execute("UPDATE virtual_transactions SET amount='1'")
    with pytest.raises(DatabaseError):
        acct.db.execute("DELETE FROM audit_logs")


def test_persistence_across_reopen(tmp_path):
    path = str(tmp_path / "p.db")
    d1 = Database(path); d1.migrate()
    a1 = Accounting(d1); a1.ensure_wallet("1000")
    run_simulation(a1, game="Plinko", rounds=40, seed=9)
    pnl = a1.snapshot().ai_balance
    d2 = Database(path); d2.migrate()
    a2 = Accounting(d2); a2.ensure_wallet("1000")      # must not re-initialise
    assert a2.snapshot().ai_balance == pnl and a2.snapshot().budget == Decimal("1000.00")
    assert a2.game_breakdown()[0]["game"] == "Plinko"


def test_migrations_idempotent(db):
    assert db.migrate() == []


def test_database_connection_failure(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    from database.db import DatabaseUnavailable
    with pytest.raises(DatabaseUnavailable):
        Database(str(blocker / "sub" / "x.db"))
    bad = Database(str(tmp_path / "ok.db"))
    bad.path = str(tmp_path / "missing_dir" / "x.db")
    with pytest.raises(DatabaseUnavailable):
        bad.query("SELECT 1")
    assert bad.ping() is False


def test_postgres_failure_falls_back_to_sqlite(tmp_path):
    from config.settings import load_settings
    from database.db import get_database
    s = load_settings()
    s = type(s)(**{**s.__dict__, "db_path": str(tmp_path / "fb.db"), "database_url": "postgresql://u:p@127.0.0.1:1/none"})
    db = get_database(s)
    assert db.backend == "sqlite" and db.fallback_reason and db.ping()
