-- SQLite only: history is append-only ("never modify historical records silently").
CREATE TRIGGER IF NOT EXISTS trg_vtx_no_update BEFORE UPDATE ON virtual_transactions
BEGIN SELECT RAISE(ABORT, 'virtual_transactions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_vtx_no_delete BEFORE DELETE ON virtual_transactions
BEGIN SELECT RAISE(ABORT, 'virtual_transactions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_no_update BEFORE UPDATE ON audit_logs
BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete BEFORE DELETE ON audit_logs
BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_simrounds_no_update BEFORE UPDATE ON simulation_rounds
BEGIN SELECT RAISE(ABORT, 'simulation_rounds is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_simrounds_no_delete BEFORE DELETE ON simulation_rounds
BEGIN SELECT RAISE(ABORT, 'simulation_rounds is append-only'); END;
