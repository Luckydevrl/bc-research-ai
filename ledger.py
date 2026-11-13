"""Virtual accounting engine. Every amount here is VIRTUAL, never real money.

Accounting rules (from the spec):
    available = budget - allocated
    ai_balance = cumulative_pnl
    equity = available + allocated + cumulative_pnl

A stake is "allocated" while a round is open. Settling releases the stake and
books (payout - stake) to cumulative P/L exactly once. A stake may not exceed
`spendable` (= available + cumulative_pnl) so the virtual bankroll can never go
below zero. Every change writes an immutable, idempotent transaction row.
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from database.db import audit, now_iso, tx

CENT = Decimal("0.01")
MAX_ABS = Decimal("1e15")


class LedgerError(ValueError):
    """Invalid virtual-accounting operation."""


def to_money(value) -> Decimal:
    """Convert to a 2-dp Decimal; reject NaN/inf/absurd magnitudes."""
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LedgerError(f"Not a valid amount: {value!r}") from exc
    if not d.is_finite():
        raise LedgerError("Amount must be a finite number")
    if abs(d) > MAX_ABS:
        raise LedgerError("Amount is too large")
    return d.quantize(CENT, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class WalletState:
    epoch: int
    budget: Decimal
    allocated: Decimal
    cumulative_pnl: Decimal

    @property
    def available(self) -> Decimal:
        return self.budget - self.allocated

    @property
    def equity(self) -> Decimal:
        return self.available + self.allocated + self.cumulative_pnl

    @property
    def spendable(self) -> Decimal:
        return self.available + self.cumulative_pnl


class Ledger:
    def __init__(self, conn: sqlite3.Connection, user_id: int = 1, initial_budget=Decimal("1000")):
        self.conn = conn
        self.user_id = user_id
        with tx(conn):
            conn.execute("INSERT OR IGNORE INTO users(id,label,created_at) VALUES(?,?,?)",
                         (user_id, "local-demo", now_iso()))
            if conn.execute("SELECT 1 FROM virtual_wallet WHERE user_id=?", (user_id,)).fetchone() is None:
                conn.execute("INSERT INTO virtual_wallet(user_id,epoch,budget,allocated,cumulative_pnl,updated_at)"
                             " VALUES(?,?,?,?,?,?)", (user_id, 1, "0.00", "0.00", "0.00", now_iso()))
                initial = to_money(initial_budget)
                if initial > 0:
                    self.adjust_budget(initial, key=f"init:{user_id}:1", note="Initial demo virtual budget")

    # ---- reads -----------------------------------------------------------
    def state(self) -> WalletState:
        r = self.conn.execute("SELECT * FROM virtual_wallet WHERE user_id=?", (self.user_id,)).fetchone()
        return WalletState(r["epoch"], Decimal(r["budget"]), Decimal(r["allocated"]), Decimal(r["cumulative_pnl"]))

    def game_pnl(self) -> list[dict]:
        """Only games with recorded simulation activity in the current epoch."""
        st = self.state()
        rows = self.conn.execute(
            "SELECT game,pnl,rounds FROM game_performance WHERE user_id=? AND epoch=? AND rounds>0 ORDER BY game",
            (self.user_id, st.epoch)).fetchall()
        return [{"game": r["game"], "pnl": Decimal(r["pnl"]), "rounds": r["rounds"]} for r in rows]

    # ---- internal --------------------------------------------------------
    def _commit(self, kind, amount, new, *, stake=None, game=None, session_id=None, key=None, note=""):
        st = self.state()
        key = key or f"auto:{uuid.uuid4()}"
        self.conn.execute(
            "UPDATE virtual_wallet SET budget=?,allocated=?,cumulative_pnl=?,updated_at=? WHERE user_id=?",
            (str(new["budget"]), str(new["allocated"]), str(new["pnl"]), now_iso(), self.user_id))
        self.conn.execute(
            "INSERT INTO virtual_transactions(user_id,epoch,kind,amount,stake,game,session_id,idempotency_key,"
            "budget_after,allocated_after,pnl_after,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.user_id, st.epoch, kind, str(amount), None if stake is None else str(stake), game, session_id,
             key, str(new["budget"]), str(new["allocated"]), str(new["pnl"]), note, now_iso()))

    def _seen(self, key) -> bool:
        return key is not None and self.conn.execute(
            "SELECT 1 FROM virtual_transactions WHERE idempotency_key=?", (key,)).fetchone() is not None

    # ---- writes ----------------------------------------------------------
    def adjust_budget(self, delta, key: str | None = None, note: str = "") -> bool:
        """Add (+) or reduce (-) the virtual research budget. Returns False if the key was already applied."""
        delta = to_money(delta)
        if delta == 0:
            raise LedgerError("Budget change must be non-zero")
        with tx(self.conn):
            if self._seen(key):
                return False
            st = self.state()
            new_budget = st.budget + delta
            if new_budget < 0:
                raise LedgerError("Budget cannot go below zero")
            if new_budget < st.allocated:
                raise LedgerError("Budget cannot be reduced below the currently allocated stake")
            if new_budget + st.cumulative_pnl < 0:
                raise LedgerError("Reduction would make virtual equity negative")
            self._commit("BUDGET_ADD" if delta > 0 else "BUDGET_REDUCE", delta,
                         {"budget": new_budget, "allocated": st.allocated, "pnl": st.cumulative_pnl},
                         key=key, note=note)
            audit(self.conn, "ledger", "budget_adjust", f"{delta} (virtual)")
        return True

    def place_stake(self, stake, game: str, session_id: int | None = None, key: str | None = None) -> bool:
        stake = to_money(stake)
        if stake <= 0:
            raise LedgerError("Stake must be positive")
        with tx(self.conn):
            if self._seen(key):
                return False
            st = self.state()
            if stake > st.spendable:
                raise LedgerError("Insufficient virtual balance for this stake")
            self._commit("STAKE_ALLOCATE", stake,
                         {"budget": st.budget, "allocated": st.allocated + stake, "pnl": st.cumulative_pnl},
                         stake=stake, game=game, session_id=session_id, key=key)
        return True

    def settle_round(self, stake, payout, game: str, session_id: int | None = None, key: str | None = None) -> bool:
        """Release `stake` and book (payout - stake) to P/L exactly once."""
        stake, payout = to_money(stake), to_money(payout)
        if stake <= 0 or payout < 0:
            raise LedgerError("Invalid stake/payout")
        with tx(self.conn):
            if self._seen(key):
                return False
            st = self.state()
            if stake > st.allocated:
                raise LedgerError("No matching allocated stake to settle (possible double settlement)")
            net = payout - stake
            self._commit("ROUND_SETTLE", payout,
                         {"budget": st.budget, "allocated": st.allocated - stake, "pnl": st.cumulative_pnl + net},
                         stake=stake, game=game, session_id=session_id, key=key)
            row = self.conn.execute("SELECT pnl,rounds,staked FROM game_performance WHERE user_id=? AND epoch=? AND game=?",
                                    (self.user_id, st.epoch, game)).fetchone()
            if row is None:
                self.conn.execute("INSERT INTO game_performance(user_id,epoch,game,pnl,rounds,staked) VALUES(?,?,?,?,?,?)",
                                  (self.user_id, st.epoch, game, str(net), 1, str(stake)))
            else:
                self.conn.execute("UPDATE game_performance SET pnl=?,rounds=?,staked=? WHERE user_id=? AND epoch=? AND game=?",
                                  (str(Decimal(row["pnl"]) + net), row["rounds"] + 1, str(Decimal(row["staked"]) + stake),
                                   self.user_id, st.epoch, game))
        return True

    def restart_demo(self, initial_budget=Decimal("1000")) -> int:
        """Start a fresh demo epoch. Old transactions/sessions are kept (audit trail) but no longer shown."""
        initial = to_money(initial_budget)
        with tx(self.conn):
            st = self.state()
            new_epoch = st.epoch + 1
            self.conn.execute("UPDATE virtual_wallet SET epoch=?,budget='0.00',allocated='0.00',cumulative_pnl='0.00',"
                              "updated_at=? WHERE user_id=?", (new_epoch, now_iso(), self.user_id))
            audit(self.conn, "ledger", "restart_demo", f"epoch {st.epoch} -> {new_epoch}")
            if initial > 0:
                self.adjust_budget(initial, key=f"init:{self.user_id}:{new_epoch}", note="Demo restart budget")
        return new_epoch
