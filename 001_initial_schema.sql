-- BC Research AI schema (portable SQLite / Postgres).
-- Money is stored as TEXT decimal strings (exact); aggregation is done in Python Decimal.
-- Timestamps are ISO-8601 UTC strings.

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS virtual_wallet (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  epoch INTEGER NOT NULL DEFAULT 1,
  budget TEXT NOT NULL,
  allocated TEXT NOT NULL,
  cumulative_pnl TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (user_id)
);

CREATE TABLE IF NOT EXISTS virtual_transactions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  wallet_id TEXT NOT NULL REFERENCES virtual_wallet(id),
  epoch INTEGER NOT NULL,
  tx_type TEXT NOT NULL,
  amount TEXT NOT NULL,
  game TEXT,
  session_id TEXT,
  idempotency_key TEXT NOT NULL UNIQUE,
  budget_after TEXT NOT NULL,
  allocated_after TEXT NOT NULL,
  pnl_after TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vtx_user_epoch ON virtual_transactions(user_id, epoch);
CREATE INDEX IF NOT EXISTS idx_vtx_session ON virtual_transactions(session_id);

CREATE TABLE IF NOT EXISTS games (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  category TEXT,
  official_url TEXT,
  published_rtp TEXT,
  source_url TEXT,
  verified_on TEXT,
  min_bet TEXT,
  max_bet TEXT,
  volatility TEXT,
  confidence TEXT NOT NULL DEFAULT 'UNVERIFIED',
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_sources (
  id TEXT PRIMARY KEY,
  game_id TEXT NOT NULL REFERENCES games(id),
  url TEXT NOT NULL,
  source_kind TEXT NOT NULL,          -- OFFICIAL | PUBLIC | USER_CLAIM
  rtp_claimed TEXT,
  retrieved_on TEXT,
  status TEXT NOT NULL DEFAULT 'UNCHECKED',  -- UNCHECKED | OK | INACCESSIBLE
  http_status INTEGER,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_game_sources_game ON game_sources(game_id);

CREATE TABLE IF NOT EXISTS game_rules (
  id TEXT PRIMARY KEY,
  game_id TEXT NOT NULL REFERENCES games(id),
  model_key TEXT NOT NULL,
  params_json TEXT NOT NULL,
  description TEXT,
  origin TEXT NOT NULL,               -- ASSUMED_MODEL | OFFICIAL_RULES
  created_at TEXT NOT NULL,
  UNIQUE (game_id, model_key)
);

CREATE TABLE IF NOT EXISTS imported_datasets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  source_type TEXT NOT NULL,          -- USER_IMPORTED | PUBLIC | SYNTHETIC_DEMO
  file_hash TEXT NOT NULL UNIQUE,
  total_rows INTEGER NOT NULL,
  valid_rows INTEGER NOT NULL,
  inserted_rows INTEGER NOT NULL,
  duplicate_rows INTEGER NOT NULL,
  malformed_rows INTEGER NOT NULL,
  notes TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS historical_rounds (
  id TEXT PRIMARY KEY,
  dataset_id TEXT NOT NULL REFERENCES imported_datasets(id),
  game TEXT NOT NULL,
  round_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  result TEXT,
  multiplier REAL,
  bet TEXT,
  payout TEXT,
  source_type TEXT NOT NULL,
  UNIQUE (game, round_id)
);
CREATE INDEX IF NOT EXISTS idx_hist_game_ts ON historical_rounds(game, ts);

CREATE TABLE IF NOT EXISTS simulation_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  epoch INTEGER NOT NULL,
  game TEXT NOT NULL,
  model_key TEXT,
  strategy TEXT NOT NULL,
  config_json TEXT NOT NULL,
  seed INTEGER,
  allocated TEXT NOT NULL,
  rounds INTEGER NOT NULL DEFAULT 0,
  pnl TEXT NOT NULL DEFAULT '0.00',
  wins INTEGER NOT NULL DEFAULT 0,
  losses INTEGER NOT NULL DEFAULT 0,
  max_drawdown TEXT,
  longest_losing_streak INTEGER,
  stop_reason TEXT,
  status TEXT NOT NULL,               -- RUNNING | SETTLED | VOID
  data_origin TEXT NOT NULL DEFAULT 'SIMULATED',
  started_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_epoch ON simulation_sessions(user_id, epoch);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON simulation_sessions(status);

CREATE TABLE IF NOT EXISTS simulation_rounds (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES simulation_sessions(id),
  idx INTEGER NOT NULL,
  stake TEXT NOT NULL,
  multiplier TEXT NOT NULL,
  pnl TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_simrounds_session ON simulation_rounds(session_id);

CREATE TABLE IF NOT EXISTS game_performance (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  epoch INTEGER NOT NULL,
  game TEXT NOT NULL,
  pnl TEXT NOT NULL,
  rounds INTEGER NOT NULL,
  sessions INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (user_id, epoch, game)
);

CREATE TABLE IF NOT EXISTS strategy_results (
  id TEXT PRIMARY KEY,
  game TEXT NOT NULL,
  strategy TEXT NOT NULL,
  config_json TEXT NOT NULL,
  n_sessions INTEGER NOT NULL,
  seeds_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_strategy_game ON strategy_results(game, created_at);

CREATE TABLE IF NOT EXISTS monte_carlo_results (
  id TEXT PRIMARY KEY,
  game TEXT NOT NULL,
  config_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mc_game ON monte_carlo_results(game, created_at);

CREATE TABLE IF NOT EXISTS research_jobs (
  id TEXT PRIMARY KEY,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL,               -- RUNNING | SUCCESS | FAILED
  started_at TEXT NOT NULL,
  finished_at TEXT,
  detail TEXT,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_type ON research_jobs(job_type, started_at);

CREATE TABLE IF NOT EXISTS research_reports (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  data_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_kind ON research_reports(kind, created_at);

CREATE TABLE IF NOT EXISTS promotions (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  source_url TEXT,
  eligibility TEXT,
  wagering_multiple TEXT,
  wagering_base TEXT,
  expiry TEXT,
  withdrawal_restrictions TEXT,
  terms TEXT,
  bonus_amount TEXT,
  deposit_amount TEXT,
  withdrawable_explicit INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id TEXT PRIMARY KEY,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  entity TEXT,
  entity_id TEXT,
  detail TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);
