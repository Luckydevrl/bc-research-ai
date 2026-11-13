"""Minimal dashboard: view-model + HTML renderer (framework independent, unit-testable).

The main dashboard shows ONLY: title, Available Balance, AI Balance, Game-wise P/L.
All amounts are virtual.
"""
from __future__ import annotations

import html
from decimal import Decimal

from services.accounting import Accounting

CENT = Decimal("0.01")

CSS = (
    ":root{--bg:#0b0f14;--card:#121923;--line:#1f2a38;--text:#e8eef5;--muted:#93a1b3;--pos:#34d399;--neg:#ff6b6b;}"
    "[data-testid=stAppViewContainer],[data-testid=stApp],.stApp{background:var(--bg)!important;}"
    "header[data-testid=stHeader],footer,#MainMenu,[data-testid=stToolbar],[data-testid=stDecoration]{display:none!important;}"
    ".block-container{padding:1.25rem 1rem 3rem!important;max-width:720px!important;}"
    ".bcr{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:var(--text);}"
    ".bcr *{box-sizing:border-box;}"
    ".bcr-top{display:flex;align-items:center;justify-content:space-between;gap:.75rem;margin-bottom:1.1rem;}"
    ".bcr-title{font-size:1.35rem;font-weight:700;letter-spacing:.01em;margin:0;}"
    ".bcr-chip{font-size:.68rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);"
    "border:1px solid var(--line);border-radius:999px;padding:.25rem .6rem;white-space:nowrap;}"
    ".bcr-grid{display:grid;grid-template-columns:1fr;gap:.75rem;}"
    "@media(min-width:520px){.bcr-grid{grid-template-columns:1fr 1fr;}}"
    ".bcr-card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:1rem 1.1rem;}"
    ".bcr-label{font-size:.82rem;color:var(--muted);font-weight:600;letter-spacing:.02em;}"
    ".bcr-sub{font-size:.68rem;color:var(--muted);opacity:.8;margin-left:.35rem;font-weight:500;}"
    ".bcr-amt{font-size:clamp(1.7rem,7vw,2.2rem);font-weight:700;margin-top:.35rem;font-variant-numeric:tabular-nums;"
    "line-height:1.15;word-break:break-word;}"
    ".pos{color:var(--pos);}.neg{color:var(--neg);}.zero{color:var(--text);}"
    ".bcr-h{font-size:.95rem;font-weight:700;margin:1.5rem 0 .6rem;}"
    ".bcr-list{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden;}"
    ".bcr-row{display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:.85rem 1.1rem;"
    "border-top:1px solid var(--line);}"
    ".bcr-row:first-child{border-top:none;}"
    ".bcr-game{font-weight:600;font-size:1rem;}"
    ".bcr-rounds{font-size:.75rem;color:var(--muted);margin-top:.15rem;}"
    ".bcr-pl{font-weight:700;font-size:1.05rem;font-variant-numeric:tabular-nums;white-space:nowrap;}"
    ".bcr-empty{padding:1rem 1.1rem;color:var(--muted);font-size:.9rem;}"
)


def format_inr(value: Decimal, signed: bool = False, hide_zero_decimals: bool = False) -> str:
    q = Decimal(value).quantize(CENT)
    neg = q < 0
    s = f"{abs(q):.2f}"
    whole, dec = s.split(".")
    if len(whole) > 3:  # Indian digit grouping: 12,34,567
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    sign = "-" if neg else ("+" if signed and q > 0 else "")
    tail_txt = "" if (hide_zero_decimals and dec == "00") else f".{dec}"
    return f"{sign}₹{whole}{tail_txt}"


def _tone(v: Decimal) -> str:
    return "pos" if v > 0 else "neg" if v < 0 else "zero"


def build_view_model(acct: Accounting) -> dict:
    snap = acct.snapshot()
    games = [g for g in acct.game_breakdown() if g["rounds"] > 0]
    return {"available": snap.available, "ai_balance": snap.ai_balance, "games": games}


def render_dashboard_html(vm: dict) -> str:
    """Single-line HTML (no blank lines/indentation so Markdown never turns it into a code block)."""
    avail = format_inr(vm["available"], hide_zero_decimals=True)
    ai = vm["ai_balance"]
    parts = [
        f"<style>{CSS}</style>",
        '<div class="bcr">',
        '<div class="bcr-top"><h1 class="bcr-title">BC Research AI</h1><span class="bcr-chip">Virtual simulation</span></div>',
        '<div class="bcr-grid">',
        f'<div class="bcr-card"><div class="bcr-label">Available Balance<span class="bcr-sub">virtual</span></div>'
        f'<div class="bcr-amt zero">{html.escape(avail)}</div></div>',
        f'<div class="bcr-card"><div class="bcr-label">AI Balance<span class="bcr-sub">virtual</span></div>'
        f'<div class="bcr-amt {_tone(ai)}">{html.escape(format_inr(ai, signed=True))}</div></div>',
        "</div>",
        '<div class="bcr-h">Game-wise Profit/Loss</div>',
        '<div class="bcr-list">',
    ]
    if vm["games"]:
        for g in vm["games"]:
            n = g["rounds"]
            parts.append(
                f'<div class="bcr-row"><div><div class="bcr-game">{html.escape(g["game"])}</div>'
                f'<div class="bcr-rounds">{n:,} simulated round{"s" if n != 1 else ""}</div></div>'
                f'<div class="bcr-pl {_tone(g["pnl"])}">{html.escape(format_inr(g["pnl"], signed=True))}</div></div>')
    else:
        parts.append('<div class="bcr-empty">No simulated rounds yet.</div>')
    parts += ["</div>", "</div>"]
    return "".join(parts)


def render_page(vm: dict) -> str:
    """Standalone HTML page (used for tests and screenshots outside Streamlit)."""
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>BC Research AI</title></head>'
            '<body style="margin:0;background:#0b0f14"><div style="padding:1.25rem 1rem;max-width:720px;margin:0 auto">'
            + render_dashboard_html(vm) + "</div></body></html>")
