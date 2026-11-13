"""Safe CSV/JSON import of historical rounds.

* Size/row limits, no eval, every record validated with Pydantic.
* Duplicates removed (within the file and against stored data).
* The same file is never imported twice (content hash).
* Imported data can only be labelled USER_IMPORTED / PUBLIC / SYNTHETIC_DEMO -
  never "official/verified", because the app cannot verify a file's provenance.
"""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import asdict, dataclass, field

import pandas as pd
from pydantic import ValidationError

from database.db import Database, new_id, now_iso
from models.schemas import SOURCE_TYPES, HistoricalRecord

ALIASES = {
    "game": "game", "game_name": "game",
    "round_id": "round_id", "id": "round_id", "round": "round_id",
    "timestamp": "timestamp", "ts": "timestamp", "time": "timestamp", "date": "timestamp",
    "multiplier": "multiplier", "mult": "multiplier", "crash_point": "multiplier", "result_multiplier": "multiplier",
    "result": "result",
    "bet": "bet", "bet_amount": "bet", "stake": "bet",
    "payout": "payout", "payout_amount": "payout",
}
REQUIRED = ("game", "round_id", "timestamp")


class ImportFailure(Exception):
    """The file cannot be processed at all (bad format, too large, missing columns...)."""


@dataclass
class ImportReport:
    status: str = "OK"
    dataset_id: str | None = None
    total_rows: int = 0
    valid_rows: int = 0
    malformed_rows: int = 0
    duplicates_in_file: int = 0
    duplicates_in_db: int = 0
    inserted: int = 0
    missing_values: dict = field(default_factory=dict)
    sample_errors: list = field(default_factory=list)
    source_type: str = "USER_IMPORTED"

    def as_dict(self) -> dict:
        return asdict(self)


def _parse(content: bytes, fmt: str, max_rows: int) -> list[dict]:
    try:
        if fmt == "csv":
            df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, nrows=max_rows + 1,
                             on_bad_lines="skip", encoding="utf-8-sig")
            records = df.to_dict("records")
        elif fmt == "json":
            raw = json.loads(content.decode("utf-8-sig"))
            if isinstance(raw, dict):
                raw = raw.get("rounds") or raw.get("data") or []
            if not isinstance(raw, list) or not all(isinstance(r, dict) for r in raw):
                raise ImportFailure("JSON must be a list of objects (or {\"rounds\": [...]})")
            records = raw
        else:
            raise ImportFailure("Unsupported format (use csv or json)")
    except ImportFailure:
        raise
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ImportFailure(f"Could not parse file: {exc}") from exc
    if len(records) > max_rows:
        raise ImportFailure(f"Too many rows (limit {max_rows})")
    return records


def _normalise(rec: dict) -> dict:
    out: dict = {}
    for k, v in rec.items():
        key = ALIASES.get(str(k).strip().lower().replace(" ", "_"))
        if key and key not in out:
            out[key] = v
    return out


def import_bytes(db: Database, content: bytes, filename: str, source_type: str = "USER_IMPORTED",
                 max_bytes: int = 5_000_000, max_rows: int = 200_000, notes: str = "") -> ImportReport:
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"source_type must be one of {SOURCE_TYPES} (imports can never be 'official')")
    if not content:
        raise ImportFailure("File is empty")
    if len(content) > max_bytes:
        raise ImportFailure(f"File too large (limit {max_bytes} bytes)")
    fmt = "json" if filename.lower().endswith(".json") else "csv" if filename.lower().endswith(".csv") else None
    if fmt is None:
        raise ImportFailure("Only .csv and .json files are supported")

    digest = hashlib.sha256(content).hexdigest()
    existing = db.query_one("SELECT id FROM imported_datasets WHERE file_hash=?", (digest,))
    if existing:
        return ImportReport(status="ALREADY_IMPORTED", dataset_id=existing["id"], source_type=source_type)

    records = _parse(content, fmt, max_rows)
    if not records:
        raise ImportFailure("File contains no records")
    present = set()
    for r in records[:50]:
        present |= set(_normalise(r))
    missing_cols = [c for c in REQUIRED if c not in present]
    if missing_cols:
        raise ImportFailure(f"Missing required column(s): {', '.join(missing_cols)}")

    rep = ImportReport(total_rows=len(records), source_type=source_type)
    seen: set[tuple[str, str]] = set()
    good: list[HistoricalRecord] = []
    for i, raw in enumerate(records, start=1):
        norm = _normalise(raw)
        for col in ("game", "round_id", "timestamp", "multiplier", "bet", "payout"):
            if norm.get(col) in (None, ""):
                rep.missing_values[col] = rep.missing_values.get(col, 0) + 1
        try:
            rec = HistoricalRecord(**norm)
        except (ValidationError, TypeError) as exc:
            rep.malformed_rows += 1
            if len(rep.sample_errors) < 10:
                msg = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
                rep.sample_errors.append(f"row {i}: {msg}")
            continue
        key = (rec.game, rec.round_id)
        if key in seen:
            rep.duplicates_in_file += 1
            continue
        seen.add(key)
        good.append(rec)
    rep.valid_rows = len(good)
    if not good:
        rep.status = "NO_VALID_ROWS"
        return rep

    dataset_id = new_id()
    with db.tx() as tx:
        # dataset row must exist first (foreign key)
        tx.execute(
            "INSERT INTO imported_datasets (id, name, source_type, file_hash, total_rows, valid_rows, inserted_rows,"
            " duplicate_rows, malformed_rows, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (dataset_id, filename[:200], source_type, digest, rep.total_rows, rep.valid_rows, 0,
             rep.duplicates_in_file, rep.malformed_rows, notes, now_iso()))
        inserted = tx.executemany(
            "INSERT INTO historical_rounds (id, dataset_id, game, round_id, ts, result, multiplier, bet, payout, source_type)"
            " VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT (game, round_id) DO NOTHING",
            [(new_id(), dataset_id, r.game, r.round_id, r.timestamp.isoformat(), r.result, r.multiplier,
              None if r.bet is None else str(r.bet), None if r.payout is None else str(r.payout), source_type)
             for r in good])
        rep.inserted = int(inserted)
        rep.duplicates_in_db = rep.valid_rows - rep.inserted
        tx.execute("UPDATE imported_datasets SET inserted_rows=?, duplicate_rows=? WHERE id=?",
                   (rep.inserted, rep.duplicates_in_file + rep.duplicates_in_db, dataset_id))
        tx.execute("INSERT INTO audit_logs (id, actor, action, entity, entity_id, detail, created_at) VALUES (?,?,?,?,?,?,?)",
                   (new_id(), "user", "DATASET_IMPORTED", "dataset", dataset_id,
                    json.dumps({"file": filename, "source_type": source_type, "inserted": rep.inserted}), now_iso()))
    rep.dataset_id = dataset_id
    return rep
