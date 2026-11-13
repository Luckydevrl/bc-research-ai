"""Rules-based research analyst (default, no paid API required).

It only *explains numbers that were actually computed*. It never invents game results,
live balances, RTP figures or historical data. An optional LLM hook exists but is a
no-op unless a provider is wired in later; the app works fully without it.
"""
from __future__ import annotations

import json
import os
from datetime import date

from research.rtp_engine import normalize_rtp

FORBIDDEN_PROMISES = ("guaranteed", "risk-free", "sure win", "will win")


def _inr(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}INR {abs(x):,.0f}"


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def explain_profile(p: dict) -> str:
    if p.get("status") != "OK":
        return f"{p.get('game')}: insufficient data - no verified rules, so no mathematical model is used."
    return (f"{p['game']} (assumed model): win probability {_pct(p['p_win'])}, expected value "
            f"{100 * p['ev_per_unit']:.2f}% of each stake (house edge {_pct(p['house_edge'])}), "
            f"volatility {p['volatility']} (std {p['std']:.2f}x stake). Expected value is negative whenever "
            f"the house edge is positive, whatever the staking pattern.")


def assess_feasibility(mc: dict) -> dict:
    """Interpret one Monte Carlo result without overstating anything."""
    rows = mc["rows"]
    ref_budget = rows[0]["budget"]
    lines = [f"{mc['game']} (assumed edge {_pct(mc['assumed_house_edge'])}), target {_inr(mc['target'])}/day:"]
    for r in [x for x in rows if x["budget"] == ref_budget]:
        lo, hi = r["expected_daily_pnl_ci95"]
        lines.append(
            f"  budget {_inr(r['budget'])}, stake {_pct(r['stake_fraction'])} of budget: target hit on {_pct(r['p_target'])} of days, "
            f"whole budget lost on {_pct(r['p_lose_budget'])}; expected day {_inr(r['expected_daily_pnl'])} "
            f"(95% CI {_inr(lo)} to {_inr(hi)}), median {_inr(r['median_daily_pnl'])}, worst {_inr(r['worst_daily_pnl'])}.")
    best = max(rows, key=lambda r: r["p_target"])
    lines.append(
        f"  Highest one-day hit rate tested: {_pct(best['p_target'])} (budget {_inr(best['budget'])}, stake "
        f"{_pct(best['stake_fraction'])}) with expected day {_inr(best['expected_daily_pnl'])}; target on every one of "
        f"{mc['days']} days: {best['p_target_every_day']:.2e}; at least one whole-budget loss in {mc['days']} days: "
        f"{_pct(best['p_lose_budget_at_least_once'])}.")
    req = mc.get("required_capital_for_50pct_single_day_target")
    lines.append("  Smallest tested budget reaching the target on a single day at least half the time: "
                 + (f"{_inr(req['budget'])} at {_pct(req['stake_fraction'])} stakes" if req else "none of the tested budgets")
                 + " (a one-day hit rate, not expected profit).")
    verdict = mc["ev_verdict"]
    if verdict == "NEGATIVE_EV":
        lines.append("  Verdict: NEGATIVE expected value. Expected P/L equals minus the house edge times the amount staked, "
                     "for any pre-committed staking rule; simulations "
                     + ("agree with this." if mc.get("all_rows_consistent_with_theory") else "did not all agree - investigate.")
                     + " Reaching the target on some days is not sustainable.")
    elif verdict == "ZERO_EV":
        lines.append("  Verdict: zero expected value (no house edge assumed); real games always carry an edge.")
    else:
        lines.append("  Verdict: positive expected value exists only because the MODEL assumed a negative house edge. "
                     "This is not evidence about any real game.")
    return {"game": mc["game"], "verdict": verdict, "sustainable": bool(mc["statistically_sustainable"]), "lines": lines}


def compare_games(profiles: list[dict]) -> list[str]:
    ok = [p for p in profiles if p.get("status") == "OK"]
    if not ok:
        return ["No game has enough information for a mathematical comparison."]
    ok.sort(key=lambda p: (p["house_edge"], p["std"]))
    out = ["Games ranked by assumed house edge (lowest first), then volatility:"]
    for p in ok:
        out.append(f"  - {p['game']}: edge {_pct(p['house_edge'])}, P(win) {_pct(p['p_win'])}, volatility {p['volatility']}")
    out.append("Differences come from the assumed edge parameter and payout shape; they are not verified BC.GAME figures.")
    return out


def data_findings(catalogue: list[dict], stats_reports: list[dict], rtp_reports: list[dict],
                  inconsistent: list[dict]) -> list[str]:
    out = []
    unverified = [g["name"] for g in catalogue if g.get("confidence") == "UNVERIFIED"]
    if unverified:
        out.append(f"{len(unverified)} of {len(catalogue)} catalogue games are UNVERIFIED (no recorded source): "
                   f"{', '.join(unverified)}.")
    verified = [g["name"] for g in catalogue if g.get("confidence") == "VERIFIED_OFFICIAL"]
    out.append("Games with a recorded official source: " + (", ".join(verified) if verified else "none") + ".")
    if not stats_reports:
        out.append("No historical dataset has been imported, so no empirical RTP or pattern analysis exists. "
                   "Only simulations of assumed models are available.")
    for r in stats_reports:
        d = r["data"]
        tag = r.get("label", "")
        if d.get("status") != "OK":
            out.append(f"{tag}: insufficient data (n={d.get('n', 0)}, need {d.get('min_required')}).")
        else:
            out.append(f"{tag}: n={d['n']}. {d['conclusion']}")
    for r in rtp_reports:
        d = r["data"]
        out.append(f"{r.get('label', '')}: RTP comparison status {d.get('status')}.")
    for inc in inconsistent:
        vals = ", ".join(f"{c['rtp']:.4f} ({c['kind']})" for c in inc["claims"])
        out.append(f"INCONSISTENT RTP claims for {inc['game']}: {vals}. Treat the figure as unverified.")
    return out


def optional_llm_polish(text: str) -> str:
    """Hook for an optional external LLM. Intentionally a no-op: the app never depends on it,
    and a provider would only be allowed to rephrase, never to add numbers."""
    if not os.environ.get("BCRA_OPTIONAL_LLM_API_KEY"):
        return text
    return text  # provider integration deliberately not implemented


def build_daily_report(profiles: list[dict], mc_results: list[dict], strategy_results: list[dict],
                       catalogue: list[dict], stats_reports: list[dict], rtp_reports: list[dict],
                       inconsistent: list[dict]) -> dict:
    sections: list[str] = [f"BC Research AI - daily research summary ({date.today().isoformat()})",
                           "All figures below come from VIRTUAL simulations of ASSUMED models or from imported data "
                           "as labelled. Nothing here is real winnings or official BC.GAME data.", ""]
    sections.append("1. Mathematics of each modelled game")
    sections += [f"  {explain_profile(p)}" for p in profiles]
    sections += [""] + compare_games(profiles)

    sections += ["", "2. Is a daily virtual target of INR 1,000 feasible?"]
    verdicts = []
    for mc in mc_results:
        a = assess_feasibility(mc)
        verdicts.append(a)
        sections += [f"  {line}" if not line.startswith("  ") else line for line in a["lines"]]
    if verdicts:
        if all(v["verdict"] == "NEGATIVE_EV" for v in verdicts):
            sections.append("  Overall: every modelled game has negative expected value. No staking rule, stop-loss or "
                            "take-profit changes the sign of the expected value; they only change how losses are distributed.")
        elif any(v["sustainable"] for v in verdicts):
            sections.append("  Overall: some model shows a sustainable positive result. Verify the model assumptions - "
                            "this is a modelling outcome, not a real-world claim.")
        else:
            sections.append("  Overall: no model shows a statistically sustainable positive result.")
    else:
        sections.append("  Monte Carlo has not been run yet.")

    sections += ["", "3. Strategy comparison (multiple random seeds)"]
    if strategy_results:
        for s in strategy_results:
            note = " (profit in some seeds only - not reliable)" if s.get("profit_in_some_seeds_only") else ""
            sections.append(f"  {s['game']} / {s['strategy']}: pooled mean P/L {_inr(s['pooled_mean_pnl'])} "
                            f"(95% CI {_inr(s['pooled_ci95'][0])} to {_inr(s['pooled_ci95'][1])}) -> {s['verdict']}{note}")
    else:
        sections.append("  No strategy comparison has been run yet.")

    sections += ["", "4. Data and verification status"]
    sections += [f"  {line}" for line in data_findings(catalogue, stats_reports, rtp_reports, inconsistent)]
    sections += ["", "Reminder: RTP is a long-run expectation. Past rounds do not predict the next round."]
    body = optional_llm_polish("\n".join(sections))
    return {"title": f"Daily research summary {date.today().isoformat()}", "body": body,
            "verdicts": [{"game": v["game"], "verdict": v["verdict"], "sustainable": v["sustainable"]} for v in verdicts]}
