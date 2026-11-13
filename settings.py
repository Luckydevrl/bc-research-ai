"""Central configuration. All secrets/options come from environment variables
(or a local .env file). Nothing sensitive is hardcoded."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env reader (no extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    db_path: str
    database_url: str | None          # Supabase/Postgres connection string (optional)
    default_budget: str               # virtual research budget for a fresh demo
    assumed_house_edge: float         # ASSUMED model edge, not a verified figure
    official_domains: tuple[str, ...]  # domains accepted as "official" sources
    max_upload_bytes: int
    max_upload_rows: int
    max_session_rounds: int
    sims_per_minute: int
    currency: str = "₹"
    app_name: str = "BC Research AI"


def load_settings() -> Settings:
    _load_dotenv(ROOT / ".env")
    env = os.environ.get

    def _float(name: str, default: float) -> float:
        try:
            return float(env(name, default))
        except ValueError:
            return default

    def _int(name: str, default: int) -> int:
        try:
            return int(env(name, default))
        except ValueError:
            return default

    edge = _float("BCRA_ASSUMED_HOUSE_EDGE", 0.01)
    if not (0.0 <= edge < 0.5):
        edge = 0.01
    domains = tuple(
        d.strip().lower() for d in env("BCRA_OFFICIAL_DOMAINS", "bc.game").split(",") if d.strip()
    )
    return Settings(
        db_path=env("BCRA_DB_PATH") or str(ROOT / "data" / "bc_research.db"),
        database_url=env("DATABASE_URL") or None,
        default_budget=env("BCRA_DEFAULT_BUDGET", "1000"),
        assumed_house_edge=edge,
        official_domains=domains or ("bc.game",),
        max_upload_bytes=_int("BCRA_MAX_UPLOAD_BYTES", 5_000_000),
        max_upload_rows=_int("BCRA_MAX_UPLOAD_ROWS", 200_000),
        max_session_rounds=_int("BCRA_MAX_SESSION_ROUNDS", 1000),
        sims_per_minute=_int("BCRA_SIMS_PER_MINUTE", 30),
    )
