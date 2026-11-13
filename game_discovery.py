"""Game catalogue (Module A).

Nothing here is invented: every game starts as UNVERIFIED with no RTP, URL or limits.
A game becomes VERIFIED_OFFICIAL only when a person records a source URL on a
configured official domain. The app does not scrape pages for RTP numbers; it only
checks whether a recorded source URL is reachable and records that fact.
"""
from __future__ import annotations

import json
from datetime import date
from urllib.parse import urlparse

from database.db import Database, new_id, now_iso
from simulation.games import DEFAULT_PARAMS, ORIGIN, build_model

# Working list of game names (all UNVERIFIED until a source is recorded).
SEED_GAMES = [
    ("Crash", "Originals"), ("Limbo", "Originals"), ("Mines", "Originals"), ("Plinko", "Originals"),
    ("Dice", "Originals"), ("Hilo", "Originals"), ("Keno", "Originals"), ("Wheel", "Originals"),
]
CONFIDENCE = ("UNVERIFIED", "USER_CLAIM", "PUBLIC_UNVERIFIED", "VERIFIED_OFFICIAL")


def seed_catalogue(db: Database, edge: float = 0.01) -> int:
    """Insert missing games (UNVERIFIED) and the assumed math models. Idempotent."""
    created = 0
    with db.tx() as tx:
        ts = now_iso()
        for name, cat in SEED_GAMES:
            row = tx.query_one("SELECT id FROM games WHERE name=?", (name,))
            if row is None:
                tx.execute("INSERT INTO games (id, name, category, confidence, notes, created_at, updated_at)"
                           " VALUES (?,?,?,?,?,?,?)",
                           (new_id(), name, cat, "UNVERIFIED", "Listed by name only; no verified source.", ts, ts))
                created += 1
        for name in DEFAULT_PARAMS:
            g = tx.query_one("SELECT id FROM games WHERE name=?", (name,))
            if not g:
                continue
            m = build_model(name, None, edge)
            tx.execute(
                "INSERT INTO game_rules (id, game_id, model_key, params_json, description, origin, created_at)"
                " VALUES (?,?,?,?,?,?,?) ON CONFLICT (game_id, model_key) DO NOTHING",
                (new_id(), g["id"], "default", json.dumps(m.params), m.description, ORIGIN, ts))
    return created


def _host_ok(url: str, official_domains: tuple[str, ...]) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == d or host.endswith("." + d) for d in official_domains)


def record_source(db: Database, game: str, url: str, rtp: float | None, kind: str,
                  official_domains: tuple[str, ...] = ("bc.game",), verified_on: str | None = None,
                  min_bet: str | None = None, max_bet: str | None = None, volatility: str | None = None,
                  official_url: str | None = None) -> dict:
    """Record a source for a game. 'OFFICIAL' is accepted only for https URLs on an official domain."""
    if kind not in ("OFFICIAL", "PUBLIC", "USER_CLAIM"):
        raise ValueError("kind must be OFFICIAL, PUBLIC or USER_CLAIM")
    if not url or urlparse(url).scheme not in ("http", "https"):
        raise ValueError("a valid source URL is required")
    if kind == "OFFICIAL" and not _host_ok(url, official_domains):
        raise ValueError("source URL is not on a configured official domain; record it as PUBLIC or USER_CLAIM")
    if rtp is not None:
        rtp = float(rtp)
        if rtp > 1.5:
            rtp /= 100.0
        if not (0 < rtp <= 1.5):
            raise ValueError("RTP out of range")
    confidence = {"OFFICIAL": "VERIFIED_OFFICIAL", "PUBLIC": "PUBLIC_UNVERIFIED", "USER_CLAIM": "USER_CLAIM"}[kind]
    ts = now_iso()
    vdate = verified_on or date.today().isoformat()
    with db.tx() as tx:
        g = tx.query_one("SELECT * FROM games WHERE name=?", (game,))
        if not g:
            gid = new_id()
            tx.execute("INSERT INTO games (id, name, category, confidence, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                       (gid, game, None, "UNVERIFIED", ts, ts))
            g = {"id": gid, "confidence": "UNVERIFIED"}
        tx.execute("INSERT INTO game_sources (id, game_id, url, source_kind, rtp_claimed, retrieved_on, status, created_at)"
                   " VALUES (?,?,?,?,?,?,?,?)",
                   (new_id(), g["id"], url, kind, None if rtp is None else repr(rtp), vdate, "UNCHECKED", ts))
        # never downgrade an already-verified game
        if CONFIDENCE.index(confidence) >= CONFIDENCE.index(g["confidence"]):
            tx.execute(
                "UPDATE games SET confidence=?, published_rtp=COALESCE(?, published_rtp), source_url=?, verified_on=?,"
                " official_url=COALESCE(?, official_url), min_bet=COALESCE(?, min_bet), max_bet=COALESCE(?, max_bet),"
                " volatility=COALESCE(?, volatility), updated_at=? WHERE id=?",
                (confidence, None if rtp is None else repr(rtp), url, vdate, official_url, min_bet, max_bet,
                 volatility, ts, g["id"]))
        tx.execute("INSERT INTO audit_logs (id, actor, action, entity, entity_id, detail, created_at) VALUES (?,?,?,?,?,?,?)",
                   (new_id(), "user", "SOURCE_RECORDED", "game", g["id"], json.dumps({"url": url, "kind": kind}), ts))
    return {"game": game, "confidence": confidence}


def check_sources(db: Database, fetcher=None, timeout: int = 10) -> dict:
    """Check reachability of recorded sources. No auth/CAPTCHA/region bypass: a refusal is recorded and we move on."""
    rows = db.query("SELECT id, url FROM game_sources")
    if fetcher is None:
        try:
            import requests

            def fetcher(url):  # type: ignore[no-redef]
                return requests.get(url, timeout=timeout, headers={"User-Agent": "BCResearchAI/1.0 (research; polite)"},
                                    allow_redirects=True).status_code
        except ImportError:
            fetcher = None
    ok = bad = 0
    for r in rows:
        status, code, note = "INACCESSIBLE", None, None
        if fetcher is None:
            note = "no HTTP client available"
        else:
            try:
                code = int(fetcher(r["url"]))
                status = "OK" if 200 <= code < 300 else "INACCESSIBLE"
                if status != "OK":
                    note = f"HTTP {code}; not retried or bypassed"
            except Exception as exc:  # noqa: BLE001
                note = f"{type(exc).__name__}: {exc}"[:200]
        db.execute("UPDATE game_sources SET status=?, http_status=?, note=? WHERE id=?", (status, code, note, r["id"]))
        ok += status == "OK"
        bad += status != "OK"
    return {"checked": len(rows), "ok": ok, "inaccessible": bad}


def detect_inconsistent_rtp(db: Database, tolerance: float = 0.001) -> list[dict]:
    rows = db.query("SELECT g.name AS game, s.rtp_claimed, s.url, s.source_kind FROM game_sources s "
                    "JOIN games g ON g.id = s.game_id WHERE s.rtp_claimed IS NOT NULL")
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["game"], []).append({"rtp": float(r["rtp_claimed"]), "url": r["url"], "kind": r["source_kind"]})
    out = []
    for game, items in by.items():
        vals = [i["rtp"] for i in items]
        if max(vals) - min(vals) > tolerance:
            out.append({"game": game, "claims": items})
    return out


def catalogue(db: Database) -> list[dict]:
    return db.query("SELECT name, category, confidence, published_rtp, source_url, verified_on FROM games ORDER BY name")
