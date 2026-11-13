"""Thin database layer: SQLite (default / demo) with optional Postgres (Supabase).

* One short-lived connection per transaction -> safe with Streamlit threads.
* SQL is written with ``?`` placeholders; they are translated for Postgres.
* Errors are translated into ``DatabaseError`` subclasses with clear messages.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class DatabaseError(Exception):
    """Base class for database problems."""


class DatabaseUnavailable(DatabaseError):
    """The database cannot be opened / reached."""


class IntegrityViolation(DatabaseError):
    """A constraint (unique, foreign key, ...) was violated."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_id() -> str:
    return uuid.uuid4().hex


def _translate(exc: Exception) -> DatabaseError:
    name = type(exc).__name__
    if "Integrity" in name or "Unique" in name:
        return IntegrityViolation(str(exc))
    if "Operational" in name:
        return DatabaseUnavailable(f"Database unavailable: {exc}")
    return DatabaseError(str(exc))


def _is_driver_error(exc: Exception) -> bool:
    mod = type(exc).__module__ or ""
    return mod.startswith("sqlite3") or mod.startswith("psycopg")


class Tx:
    """Operations inside one transaction."""

    def __init__(self, conn: Any, backend: str):
        self.conn = conn
        self.backend = backend

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.backend == "postgres" else sql

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        try:
            return self.conn.execute(self._sql(sql), tuple(params))
        except Exception as exc:  # noqa: BLE001
            if _is_driver_error(exc):
                raise _translate(exc) from exc
            raise

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> int:
        rows = [tuple(r) for r in seq]
        if not rows:
            return 0
        try:
            if self.backend == "postgres":
                cur = self.conn.cursor()
                cur.executemany(self._sql(sql), rows)
                return cur.rowcount
            cur = self.conn.executemany(sql, rows)
            return cur.rowcount
        except Exception as exc:  # noqa: BLE001
            if _is_driver_error(exc):
                raise _translate(exc) from exc
            raise

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        cur = self.execute(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None


class Database:
    def __init__(self, path: str | None = None, url: str | None = None):
        self.url = url
        self.path = str(path) if path else None
        self.backend = "postgres" if url else "sqlite"
        self.fallback_reason: str | None = None
        if self.backend == "sqlite":
            if not self.path:
                raise DatabaseUnavailable("No database path configured")
            try:
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise DatabaseUnavailable(f"Cannot create database folder: {exc}") from exc

    # -- connections ---------------------------------------------------
    def _connect(self) -> Any:
        if self.backend == "sqlite":
            try:
                conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            except sqlite3.Error as exc:
                raise DatabaseUnavailable(f"Cannot open database: {exc}") from exc
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass
            return conn
        try:  # pragma: no cover - needs a live Postgres
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(self.url, row_factory=dict_row, connect_timeout=10)
        except Exception as exc:  # noqa: BLE001
            raise DatabaseUnavailable(f"Cannot connect to Postgres/Supabase: {exc}") from exc

    @contextmanager
    def tx(self) -> Iterator[Tx]:
        conn = self._connect()
        try:
            try:
                if self.backend == "sqlite":
                    conn.execute("BEGIN IMMEDIATE")
                yield Tx(conn, self.backend)
                if self.backend == "sqlite":
                    conn.execute("COMMIT")
                else:  # pragma: no cover
                    conn.commit()
            except BaseException:
                try:
                    if self.backend == "sqlite":
                        conn.execute("ROLLBACK")
                    else:  # pragma: no cover
                        conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                raise
        except sqlite3.Error as exc:
            raise _translate(exc) from exc
        finally:
            conn.close()

    # -- convenience ---------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self.tx() as tx:
            tx.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        with self.tx() as tx:
            return tx.query(sql, params)

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def ping(self) -> bool:
        try:
            self.query("SELECT 1 AS ok")
            return True
        except DatabaseError:
            return False

    # -- migrations ----------------------------------------------------
    def migrate(self) -> list[str]:
        """Apply pending SQL migrations in filename order. Returns applied versions."""
        applied: list[str] = []
        conn = self._connect()
        try:
            if self.backend == "sqlite":
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
            else:  # pragma: no cover
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                conn.commit()
            done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
            for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if ".sqlite." in f.name and self.backend != "sqlite":
                    continue
                if f.stem in done:
                    continue
                sql = f.read_text(encoding="utf-8")
                try:
                    if self.backend == "sqlite":
                        conn.executescript(sql)
                        conn.execute(
                            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                            (f.stem, now_iso()),
                        )
                    else:  # pragma: no cover
                        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                            conn.execute(stmt)
                        conn.execute(
                            "INSERT INTO schema_migrations(version, applied_at) VALUES (%s, %s)",
                            (f.stem, now_iso()),
                        )
                        conn.commit()
                except Exception as exc:  # noqa: BLE001
                    raise DatabaseError(f"Migration {f.name} failed: {exc}") from exc
                applied.append(f.stem)
        finally:
            conn.close()
        return applied


def get_database(settings) -> Database:
    """Supabase/Postgres if configured and reachable, otherwise SQLite fallback."""
    if settings.database_url:
        db = Database(url=settings.database_url)
        try:
            db.migrate()
            return db
        except DatabaseError as exc:
            fallback = Database(path=settings.db_path)
            fallback.fallback_reason = f"Postgres unavailable, using SQLite fallback ({exc})"
            fallback.migrate()
            return fallback
    db = Database(path=settings.db_path)
    db.migrate()
    return db
