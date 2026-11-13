# BC Research AI

A research and **mathematical simulation** platform for studying game mechanics (RTP, house edge, variance, streaks, bonuses) and testing whether a **₹1,000 daily virtual target** is mathematically feasible. Everything is **virtual**: no real money, no BC.GAME account access, no live betting.

> **Headline finding of the built-in research:** under any model with a positive house edge, expected profit is negative for every staking rule, stop-loss or take-profit. A target can be hit on many simulated days (e.g. 90%+ with a large budget), but the expected day is still a loss and the target is not sustainable. The app reports this; it never manufactures profit.

## What you see
Main page only: **Available Balance**, **AI Balance** (green/red), **Game-wise Profit/Loss** (only games with simulated rounds, with round counts). Dark, mobile-first.
Controls (add/reduce virtual budget, run simulation, import data, export, restart demo, research summary) are in the **collapsed sidebar** (tap `>` top-left). All analysis runs in the background.

## Verified vs. simulated (read this)
| Label | Meaning |
|---|---|
| `VERIFIED_OFFICIAL` | A person recorded an **https** source URL on a configured official domain (`BCRA_OFFICIAL_DOMAINS`). The app does not read the page for you. |
| `PUBLIC_UNVERIFIED` / `USER_CLAIM` | Recorded source that is not official / a user's claim. |
| `UNVERIFIED` | Default for every catalogue game. No RTP, URL or limits are invented. |
| `USER_IMPORTED` | Your CSV/JSON. Never treated as verified (provenance can't be proven). |
| `SYNTHETIC_DEMO` | Generated sample data in `sample_data/`. **Not BC.GAME data.** |
| `SIMULATED` | Output of the math models below. **Not winnings.** |

**Game models are assumptions.** Crash, Limbo, Mines, Plinko and Dice use generic formulas (payout = (1 − edge) / win-probability; binomial Plinko with a generic payout shape) with an **assumed** house edge (`BCRA_ASSUMED_HOUSE_EDGE`, default 1%). They are *not* verified BC.GAME parameters. Games without a model (Hilo, Keno, Wheel) are reported as `INSUFFICIENT_DATA`.

## Data limitations
- No live outcomes, no private APIs, no scraping behind logins/CAPTCHA/region locks. A source check records reachability only (HTTP status) and never retries or bypasses.
- Empirical RTP needs bet + payout columns (≥1,000 rows). Crash/Limbo-style result multipliers get an *implied-RTP* estimate. Otherwise: insufficient data.
- Statistical tests flag **data anomalies**, never predictors. Past rounds do not predict the next round.
- Bonus value is computed only with complete terms and explicit official withdrawability.

## Run locally (demo mode, no credentials)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py          # opens with a ₹1,000 virtual budget
pytest                        # 118 tests
```
Demo flow: sidebar → *Run simulation* → watch AI Balance and Game-wise P/L → *Load SYNTHETIC demo dataset* → *Run background research now* → open *Latest research conclusions* → *Download* CSV/JSON → *Restart demo session*.

## Architecture
```
app.py                      Streamlit entry (minimal dashboard)
frontend/                   dashboard.py (view-model + HTML), controls.py (sidebar)
services/                   accounting.py (Decimal ledger), simulation_service.py, importer.py, export.py, ratelimit.py
research/                   game_discovery, probability_engine, rtp_engine, statistical_analysis,
                            strategy_engine, monte_carlo, bonus_analyzer, ai_analyst (rules-based, no paid API)
simulation/games.py         assumed discrete payout models
jobs/runner.py              background jobs (thread + research_jobs table)
database/                   db.py (SQLite / optional Postgres), migrations/*.sql
models/schemas.py           Pydantic validation
tests/  sample_data/  deployment/
```
Accounting: `Available = Budget − Allocated`, `AI Balance = cumulative P/L`, `Equity = Available + Allocated + P/L`. Stakes are allocated on session open and released on settle (booked once, idempotent keys). Ledger/audit/round tables are append-only (SQLite triggers); *Restart demo* starts a new epoch and archives history. `verify_ledger()` reconciles wallet vs ledger.

Background jobs: catalogue refresh, source check, historical analysis, RTP calc, probability profiles, strategy comparison (multi-seed), Monte Carlo daily target (30-day repeat stats, required capital), daily summary. On free hosting there is **no always-on scheduler**: jobs run in a background thread when the app starts/opens if the last summary is >24h old, or via the manual button.

## Free-tier reality (verify current terms)
- **Streamlit Community Cloud**: free, but apps sleep when idle and the local disk is ephemeral, so SQLite data can reset on restart. Use Supabase for persistence.
- **Supabase free**: free projects can be paused after inactivity; limits apply.
- Not possible on free hosting: guaranteed always-on background execution.
- Optional LLM wording helper: hook exists, **not implemented**; the app is fully functional without it.

## Supabase (optional)
1. Create a project at supabase.com → *Project Settings → Database → Connection string (URI)*.
2. Add `psycopg[binary]>=3.1` to `requirements.txt`.
3. Set `DATABASE_URL` (Streamlit: *App settings → Secrets*, e.g. `DATABASE_URL = "postgresql://..."`). Never put it in code or the browser.
4. Start the app; migrations `001_*.sql` apply automatically. If Postgres is unreachable the app falls back to SQLite and says so in the sidebar.
> The Postgres path is written to be portable but **has not been tested against a live Supabase project**.

## Deploy from an Android phone
See [`deployment/DEPLOY_ANDROID.md`](deployment/DEPLOY_ANDROID.md).

## Security
Secrets only via environment/Secrets; upload size/row limits; every record validated; no credentials from BC.GAME are ever stored or requested; simulation rate limit; audit log; HTML output escaped.
