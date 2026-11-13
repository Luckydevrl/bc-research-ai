"""Export / backup helpers."""
from __future__ import annotations

import csv
import io
import json

from database.db import Database

LABEL = "VIRTUAL SIMULATION - not real money, not a BC.GAME wallet"


def _safe(v):
    """Neutralise spreadsheet formula injection in text cells."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v


def export_simulations_csv(db: Database) -> str:
    rows = db.query("SELECT id, epoch, game, strategy, seed, allocated, rounds, pnl, wins, losses, max_drawdown,"
                    " longest_losing_streak, stop_reason, status, data_origin, started_at, finished_at"
                    " FROM simulation_sessions ORDER BY started_at")
    buf = io.StringIO()
    cols = ["label"] + (list(rows[0].keys()) if rows else
                        ["id", "epoch", "game", "strategy", "seed", "allocated", "rounds", "pnl", "wins", "losses",
                         "max_drawdown", "longest_losing_streak", "stop_reason", "status", "data_origin",
                         "started_at", "finished_at"])
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([LABEL] + [_safe(r[c]) for c in cols[1:]])
    return buf.getvalue()


def backup_json(db: Database) -> str:
    tables = ["virtual_wallet", "virtual_transactions", "simulation_sessions", "game_performance", "games",
              "game_sources", "imported_datasets", "research_reports", "promotions", "audit_logs"]
    data = {"label": LABEL, "tables": {t: db.query(f"SELECT * FROM {t}") for t in tables}}  # noqa: S608 (fixed names)
    return json.dumps(data, indent=2, default=str)
