import re
import sys
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from frontend.dashboard import build_view_model, format_inr, render_dashboard_html, render_page
from services.simulation_service import run_simulation

FORBIDDEN = ["rtp", "probability", "monte carlo", "strategy", "database", "api", "log", "variance", "volatility", "job", "supabase"]


@pytest.mark.parametrize("v,kw,exp", [
    (Decimal("1000"), dict(hide_zero_decimals=True), "₹1,000"),
    (Decimal("1000.50"), dict(hide_zero_decimals=True), "₹1,000.50"),
    (Decimal("0"), {}, "₹0.00"),
    (Decimal("125.5"), dict(signed=True), "+₹125.50"),
    (Decimal("-80"), dict(signed=True), "-₹80.00"),
    (Decimal("1234567.891"), {}, "₹12,34,567.89"),
])
def test_money_format(v, kw, exp):
    assert format_inr(v, **kw) == exp


def test_empty_dashboard_exact_content(acct):
    html = render_dashboard_html(build_view_model(acct))
    text = re.sub(r"<style>.*?</style>", "", html)
    text = re.sub(r"<[^>]+>", "|", text)
    parts = [t for t in text.split("|") if t.strip()]
    assert parts[0] == "BC Research AI"
    assert "Available Balance" in parts and "₹1,000" in parts and "AI Balance" in parts and "+₹0.00" not in parts
    assert "₹0.00" in parts and "Game-wise Profit/Loss" in parts
    assert "\n\n" not in html                      # safe inside Markdown


def test_dashboard_shows_only_games_with_activity_and_colours(acct):
    for g, s in (("Crash", 1), ("Mines", 2)):
        run_simulation(acct, game=g, rounds=40, seed=s)
    vm = build_view_model(acct)
    html = render_dashboard_html(vm)
    assert "Crash" in html and "Mines" in html and "Plinko" not in html and "Limbo" not in html
    assert "40 simulated rounds" in html
    for g in vm["games"]:
        assert ("pos" if g["pnl"] > 0 else "neg" if g["pnl"] < 0 else "zero") in html
    tone = "pos" if vm["ai_balance"] > 0 else "neg" if vm["ai_balance"] < 0 else "zero"
    assert f'bcr-amt {tone}' in html


def test_dashboard_has_no_technical_sections(acct):
    run_simulation(acct, game="Crash", rounds=10, seed=1)
    html = render_dashboard_html(build_view_model(acct))
    text = re.sub(r"<style>.*?</style>", "", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text).lower()
    for w in FORBIDDEN:
        assert re.search(rf"\b{re.escape(w)}\b", text) is None, w
    assert html.count('class="bcr-card"') == 2 and "<table" not in html and "<canvas" not in html and "<svg" not in html


def test_game_names_are_escaped():
    vm = {"available": Decimal("1"), "ai_balance": Decimal("0"),
          "games": [{"game": "<script>alert(1)</script>", "pnl": Decimal("1"), "rounds": 1, "sessions": 1}]}
    html = render_dashboard_html(vm)
    assert "<script>alert" not in html and "&lt;script&gt;" in html


def test_responsive_markup():
    page = render_page({"available": Decimal("1000"), "ai_balance": Decimal("0"), "games": []})
    assert 'name="viewport"' in page and "@media(min-width:520px)" in page and "No simulated rounds yet." in page


def test_app_smoke_with_mocked_streamlit(tmp_path, monkeypatch):
    """No real Streamlit is installed in CI sandbox: exercise app.py control flow against a mock."""
    st = MagicMock()
    st.cache_resource = lambda **k: (lambda f: f)
    st.columns.side_effect = lambda n: [MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
    st.sidebar.columns.side_effect = st.columns.side_effect
    for name in ("button", "checkbox", "download_button"):
        getattr(st, name).return_value = False
        getattr(st.sidebar, name).return_value = False
    st.sidebar.file_uploader.return_value = None
    st.sidebar.text_input.return_value = "100"
    st.sidebar.selectbox.side_effect = lambda label, opts, **k: list(opts)[0]
    st.sidebar.slider.return_value = 50
    monkeypatch.setitem(sys.modules, "streamlit", st)
    monkeypatch.setenv("BCRA_DB_PATH", str(tmp_path / "app.db"))
    for m in [m for m in sys.modules if m in ("app", "frontend.controls")]:
        del sys.modules[m]
    import importlib
    importlib.import_module("app")
    calls = [c for c in st.markdown.call_args_list if c.kwargs.get("unsafe_allow_html")]
    assert calls and "Available Balance" in calls[0].args[0] and "₹1,000" in calls[0].args[0]
    st.error.assert_not_called()
