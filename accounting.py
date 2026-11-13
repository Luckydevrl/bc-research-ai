"""Virtual accounting engine (Decimal arithmetic, append-only ledger).

Definitions (all amounts are VIRTUAL, never real money):
    Available Virtual Balance = Budget - Allocated
    AI Balance                = Cumulative virtual profit/loss
    Total Virtual Equity      = Available + Allocated + Cumulative P/L
Stakes are *allocated* when a simulation session opens and *released* when it
settles; only the net P/L is added to the AI balance, so nothing is counted twice.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any, Iterable, Sequence

from database.db import Database, IntegrityViolation, Tx, new_id, now_iso

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("1000000000000")
DEFAULT_USER = "local-user"


class AccountingError(Exception):
    pass


class InvalidAmount(AccountingError):
    pass


class InsufficientBudget(AccountingError):
    pass


def D(value: Any) -> Decimal:
    """Convert to a 2-dp Decimal (banker's rounding). Rejects NaN/inf."""
    if isinstance(value, Decimal):
        d = value
    else:
        try:
            d = Decimal(str(value))
        except InvalidOperation as exc:
            raise InvalidAmount(f"Invalid amount: {value!r}") from exc
    if not d.is_finite():
        raise InvalidAmount(f"Invalid amount: {value!r}")
    try:
        return d.quantize(CENT, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        raise InvalidAmount(f"Amount out of range: {value!r}") from exc


def _positive(value: Any) -> Decimal:
    d = D(value)
    if d <= 0:
        raise InvalidAmount("Amount must be greater than zero")
    if d > MAX_AMOUNT:
        raise InvalidAmount("Amount is too large")
    return d


@dataclass(frozen=True)
class WalletSnapshot:
    budget: Decimal
    allocated: Decimal
    cumulative_pnl: Decimal
    epoch: int

    @property
    def available(self) -> Decimal:
        return self.budget - self.allocated

    @property
    def ai_balance(self) -> Decimal:
        return self.cumulative_pnl

    @property
    def equity(self) -> Decimal:
        return self.available + self.allocated + self.cumulative_pnl

    @property
    def allocatable(self) -> Decimal:
        """Max stake that can be put into a new session (cannot exceed what is left
        after virtual losses)."""
        cap = self.budget + min(Decimal("0"), self.cumulative_pnl) - self.allocated
        return max(Decimal("0.00"), cap)


class Accounting:
    def __init__(self, db: Database, user_id: str = DEFAULT_USER):
        self.db = db
        self.user_id = user_id

    # -- helpers -------------------------------------------------------
    def _wallet(self, tx: Tx) -> dict:
        row = tx.query_one("SELECT * FROM virtual_wallet WHERE user_id=?", (self.user_id,))
        if not row:
            raise AccountingError("Virtual wallet is not initialised")
        return row

    @staticmethod
    def _snap(w: dict) -> WalletSnapshot:
        return WalletSnapshot(D(w["budget"]), D(w["allocated"]), D(w["cumulative_pnl"]), int(w["epoch"]))

    @staticmethod
    def _seen(tx: Tx, key: str) -> bool:
        return tx.query_one("SELECT 1 AS x FROM virtual_transactions WHERE idempotency_key=?", (key,)) is not None

    def _save_wallet(self, tx: Tx, w: dict, budget: Decimal, allocated: Decimal, pnl: Decimal, epoch: int | None = None):
        tx.execute(
            "UPDATE virtual_wallet SET budget=?, allocated=?, cumulative_pnl=?, epoch=?, updated_at=? WHERE id=?",
            (str(budget), str(allocated), str(pnl), epoch if epoch is not None else w["epoch"], now_iso(), w["id"]),
        )

    def _ledger(self, tx: Tx, w: dict, epoch: int, tx_type: str, amount: Decimal, key: str,
                budget: Decimal, allocated: Decimal, pnl: Decimal, *,
                game: str | None = None, session_id: str | None = None, note: str = "") -> None:
        tx.execute(
            "INSERT INTO virtual_transactions (id, user_id, wallet_id, epoch, tx_type, amount, game, session_id,"
            " idempotency_key, budget_after, allocated_after, pnl_after, note, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id(), self.user_id, w["id"], epoch, tx_type, str(amount), game, session_id, key,
             str(budget), str(allocated), str(pnl), note, now_iso()),
        )

    def _audit(self, tx: Tx, action: str, entity: str, entity_id: str | None, detail: dict) -> None:
        tx.execute(
            "INSERT INTO audit_logs (id, actor, action, entity, entity_id, detail, created_at) VALUES (?,?,?,?,?,?,?)",
            (new_id(), self.user_id, action, entity, entity_id, json.dumps(detail, default=str), now_iso()),
        )

    # -- wallet lifecycle ----------------------------------------------
    def ensure_wallet(self, initial_budget: Any = "1000") -> WalletSnapshot:
        initial = D(initial_budget)
        with self.db.tx() as tx:
            row = tx.query_one("SELECT * FROM virtual_wallet WHERE user_id=?", (self.user_id,))
            if row:
                return self._snap(row)
            ts = now_iso()
            tx.execute(
                "INSERT INTO users (id, label, created_at) VALUES (?,?,?) ON CONFLICT (id) DO NOTHING",
                (self.user_id, "local research user", ts),
            )
            wid = new_id()
            tx.execute(
                "INSERT INTO virtual_wallet (id, user_id, epoch, budget, allocated, cumulative_pnl, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (wid, self.user_id, 1, "0.00", "0.00", "0.00", ts),
            )
            w = self._wallet(tx)
            if initial > 0:
                self._ledger(tx, w, 1, "BUDGET_ADD", initial, "init:1", initial, Decimal("0.00"), Decimal("0.00"),
                             note="initial virtual research budget")
                self._save_wallet(tx, w, initial, Decimal("0.00"), Decimal("0.00"))
            self._audit(tx, "WALLET_CREATED", "wallet", wid, {"initial": str(initial)})
            return self._snap(self._wallet(tx))

    def snapshot(self) -> WalletSnapshot:
        with self.db.tx() as tx:
            return self._snap(self._wallet(tx))

    def add_budget(self, amount: Any, key: str | None = None, note: str = "") -> WalletSnapshot:
        amt = _positive(amount)
        key = key or f"add:{new_id()}"
        try:
            with self.db.tx() as tx:
                w = self._wallet(tx)
                if self._seen(tx, key):
                    return self._snap(w)
                s = self._snap(w)
                nb = s.budget + amt
                if nb > MAX_AMOUNT:
                    raise InvalidAmount("Budget would exceed the maximum")
                self._ledger(tx, w, s.epoch, "BUDGET_ADD", amt, key, nb, s.allocated, s.cumulative_pnl, note=note)
                self._save_wallet(tx, w, nb, s.allocated, s.cumulative_pnl)
                self._audit(tx, "BUDGET_ADD", "wallet", w["id"], {"amount": str(amt)})
        except IntegrityViolation:
            pass  # concurrent duplicate of the same idempotency key
        return self.snapshot()

    def reduce_budget(self, amount: Any, key: str | None = None, note: str = "") -> WalletSnapshot:
        amt = _positive(amount)
        key = key or f"reduce:{new_id()}"
        try:
            with self.db.tx() as tx:
                w = self._wallet(tx)
                if self._seen(tx, key):
                    return self._snap(w)
                s = self._snap(w)
                nb = s.budget - amt
                if nb < s.allocated or nb < 0:
                    raise InsufficientBudget("Cannot reduce the budget below the amount currently allocated (or below zero)")
                self._ledger(tx, w, s.epoch, "BUDGET_REDUCE", -amt, key, nb, s.allocated, s.cumulative_pnl, note=note)
                self._save_wallet(tx, w, nb, s.allocated, s.cumulative_pnl)
                self._audit(tx, "BUDGET_REDUCE", "wallet", w["id"], {"amount": str(amt)})
        except IntegrityViolation:
            pass
        return self.snapshot()

    # -- simulation sessions -------------------------------------------
    def open_session(self, *, game: str, model_key: str, strategy: str, config: dict, seed: int | None,
                     bankroll: Any) -> str:
        amt = _positive(bankroll)
        sid = new_id()
        with self.db.tx() as tx:
            w = self._wallet(tx)
            s = self._snap(w)
            if amt > s.allocatable:
                raise InsufficientBudget(
                    f"Not enough virtual budget: requested {amt}, allocatable {s.allocatable}. Add virtual budget first.")
            tx.execute(
                "INSERT INTO simulation_sessions (id, user_id, epoch, game, model_key, strategy, config_json, seed,"
                " allocated, status, data_origin, started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, self.user_id, s.epoch, game, model_key, strategy, json.dumps(config, default=str), seed,
                 str(amt), "RUNNING", "SIMULATED", now_iso()),
            )
            na = s.allocated + amt
            self._ledger(tx, w, s.epoch, "ALLOCATE", amt, f"alloc:{sid}", s.budget, na, s.cumulative_pnl,
                         game=game, session_id=sid)
            self._save_wallet(tx, w, s.budget, na, s.cumulative_pnl)
            self._audit(tx, "SESSION_OPEN", "simulation_session", sid, {"game": game, "allocated": str(amt)})
        return sid

    def close_session(self, session_id: str, *, pnl: Any, rounds: int, wins: int, losses: int,
                      max_drawdown: Any, longest_losing_streak: int, stop_reason: str,
                      rounds_log: Iterable[Sequence[Any]] = ()) -> dict:
        """Settle a session: release the allocated stake and book net P/L exactly once."""
        pnl_d = D(pnl)
        with self.db.tx() as tx:
            sess = tx.query_one("SELECT * FROM simulation_sessions WHERE id=?", (session_id,))
            if not sess:
                raise AccountingError("Unknown simulation session")
            if sess["status"] != "RUNNING":
                raise AccountingError(f"Session is already {sess['status']}")
            w = self._wallet(tx)
            s = self._snap(w)
            if int(sess["epoch"]) != s.epoch:
                raise AccountingError("Session belongs to a previous demo epoch")
            alloc = D(sess["allocated"])
            if pnl_d < -alloc:
                raise AccountingError("Simulated loss cannot exceed the allocated virtual stake")
            tx.executemany(
                "INSERT INTO simulation_rounds (id, session_id, idx, stake, multiplier, pnl) VALUES (?,?,?,?,?,?)",
                [(new_id(), session_id, i, str(D(st)), f"{float(m):.4f}", str(D(p)))
                 for i, (st, m, p) in enumerate(rounds_log, start=1)],
            )
            tx.execute(
                "UPDATE simulation_sessions SET status='SETTLED', rounds=?, pnl=?, wins=?, losses=?, max_drawdown=?,"
                " longest_losing_streak=?, stop_reason=?, finished_at=? WHERE id=?",
                (int(rounds), str(pnl_d), int(wins), int(losses), str(D(max_drawdown)), int(longest_losing_streak),
                 stop_reason, now_iso(), session_id),
            )
            na = s.allocated - alloc
            self._ledger(tx, w, s.epoch, "RELEASE", -alloc, f"release:{session_id}", s.budget, na, s.cumulative_pnl,
                         game=sess["game"], session_id=session_id, note="stake released")
            npnl = s.cumulative_pnl + pnl_d
            self._ledger(tx, w, s.epoch, "SIM_PNL", pnl_d, f"pnl:{session_id}", s.budget, na, npnl,
                         game=sess["game"], session_id=session_id, note="virtual simulated P/L")
            self._save_wallet(tx, w, s.budget, na, npnl)
            perf = tx.query_one("SELECT * FROM game_performance WHERE user_id=? AND epoch=? AND game=?",
                                (self.user_id, s.epoch, sess["game"]))
            if perf:
                tx.execute("UPDATE game_performance SET pnl=?, rounds=?, sessions=?, updated_at=? WHERE id=?",
                           (str(D(perf["pnl"]) + pnl_d), int(perf["rounds"]) + int(rounds),
                            int(perf["sessions"]) + 1, now_iso(), perf["id"]))
            else:
                tx.execute("INSERT INTO game_performance (id, user_id, epoch, game, pnl, rounds, sessions, updated_at)"
                           " VALUES (?,?,?,?,?,?,?,?)",
                           (new_id(), self.user_id, s.epoch, sess["game"], str(pnl_d), int(rounds), 1, now_iso()))
            self._audit(tx, "SESSION_SETTLED", "simulation_session", session_id,
                        {"game": sess["game"], "pnl": str(pnl_d), "rounds": int(rounds)})
        return {"session_id": session_id, "game": sess["game"], "pnl": pnl_d, "rounds": int(rounds)}

    def void_session(self, session_id: str, reason: str = "voided") -> bool:
        """Release the stake of a stuck/failed session without booking any P/L."""
        with self.db.tx() as tx:
            sess = tx.query_one("SELECT * FROM simulation_sessions WHERE id=?", (session_id,))
            if not sess or sess["status"] != "RUNNING":
                return False
            w = self._wallet(tx)
            s = self._snap(w)
            alloc = D(sess["allocated"])
            tx.execute("UPDATE simulation_sessions SET status='VOID', stop_reason=?, finished_at=? WHERE id=?",
                       (reason, now_iso(), session_id))
            if int(sess["epoch"]) == s.epoch:
                na = s.allocated - alloc
                self._ledger(tx, w, s.epoch, "RELEASE", -alloc, f"release:{session_id}", s.budget, na,
                             s.cumulative_pnl, game=sess["game"], session_id=session_id, note=reason)
                self._save_wallet(tx, w, s.budget, na, s.cumulative_pnl)
            self._audit(tx, "SESSION_VOID", "simulation_session", session_id, {"reason": reason})
        return True

    def recover_stale_sessions(self, max_age_minutes: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat(timespec="microseconds")
        rows = self.db.query("SELECT id FROM simulation_sessions WHERE status='RUNNING' AND user_id=? AND started_at < ?",
                             (self.user_id, cutoff))
        return sum(1 for r in rows if self.void_session(r["id"], "stale session recovered"))

    # -- reporting -----------------------------------------------------
    def game_breakdown(self) -> list[dict]:
        with self.db.tx() as tx:
            s = self._snap(self._wallet(tx))
            rows = tx.query(
                "SELECT game, pnl, rounds, sessions FROM game_performance WHERE user_id=? AND epoch=? AND rounds>0",
                (self.user_id, s.epoch))
        out = [{"game": r["game"], "pnl": D(r["pnl"]), "rounds": int(r["rounds"]), "sessions": int(r["sessions"])}
               for r in rows]
        out.sort(key=lambda r: (-r["rounds"], r["game"]))
        return out

    def transactions(self, limit: int = 100) -> list[dict]:
        return self.db.query("SELECT * FROM virtual_transactions WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                             (self.user_id, int(limit)))

    def verify_ledger(self) -> dict:
        """Reconcile wallet totals against the transaction ledger (current epoch)."""
        with self.db.tx() as tx:
            s = self._snap(self._wallet(tx))
            rows = tx.query("SELECT tx_type, amount FROM virtual_transactions WHERE user_id=? AND epoch=?",
                            (self.user_id, s.epoch))
            perf = tx.query("SELECT pnl FROM game_performance WHERE user_id=? AND epoch=?", (self.user_id, s.epoch))
        tot: dict[str, Decimal] = {}
        for r in rows:
            tot[r["tx_type"]] = tot.get(r["tx_type"], Decimal("0")) + D(r["amount"])
        z = Decimal("0")
        budget = tot.get("BUDGET_ADD", z) + tot.get("BUDGET_REDUCE", z)
        allocated = tot.get("ALLOCATE", z) + tot.get("RELEASE", z)
        pnl = tot.get("SIM_PNL", z)
        perf_sum = sum((D(p["pnl"]) for p in perf), z)
        ok = budget == s.budget and allocated == s.allocated and pnl == s.cumulative_pnl and perf_sum == s.cumulative_pnl
        return {"ok": ok, "budget": budget, "allocated": allocated, "pnl": pnl, "game_pnl_sum": perf_sum}

    def reset_demo(self, initial_budget: Any = "1000") -> WalletSnapshot:
        """Start a fresh demo epoch. Old sessions/ledger rows are kept (history is never deleted)."""
        initial = D(initial_budget)
        with self.db.tx() as tx:
            w = self._wallet(tx)
            s = self._snap(w)
            tx.execute("UPDATE simulation_sessions SET status='VOID', stop_reason='demo reset', finished_at=?"
                       " WHERE user_id=? AND status='RUNNING'", (now_iso(), self.user_id))
            ne = s.epoch + 1
            self._save_wallet(tx, w, Decimal("0.00"), Decimal("0.00"), Decimal("0.00"), epoch=ne)
            w = self._wallet(tx)
            self._ledger(tx, w, ne, "RESET", Decimal("0.00"), f"reset:{ne}", Decimal("0.00"), Decimal("0.00"),
                         Decimal("0.00"), note=f"demo restarted (epoch {s.epoch} archived)")
            if initial > 0:
                self._ledger(tx, w, ne, "BUDGET_ADD", initial, f"init:{ne}", initial, Decimal("0.00"), Decimal("0.00"),
                             note="initial virtual research budget")
                self._save_wallet(tx, w, initial, Decimal("0.00"), Decimal("0.00"))
            self._audit(tx, "DEMO_RESET", "wallet", w["id"], {"new_epoch": ne, "initial": str(initial)})
        return self.snapshot()
