import json
from pathlib import Path

import pytest

from services.importer import ImportFailure, import_bytes

GOOD = b"game,round_id,timestamp,multiplier\nCrash,1,2024-01-01T00:00:00Z,2.5\nCrash,2,2024-01-01T00:00:20Z,1.1\nCrash,3,2024-01-01T00:00:40Z,10\n"


def count(db):
    return db.query_one("SELECT COUNT(*) AS n FROM historical_rounds")["n"]


def test_valid_csv_import_and_label(db):
    r = import_bytes(db, GOOD, "a.csv", "USER_IMPORTED")
    assert r.inserted == 3 and r.malformed_rows == 0 and count(db) == 3
    assert db.query_one("SELECT DISTINCT source_type s FROM historical_rounds")["s"] == "USER_IMPORTED"


def test_same_file_not_imported_twice(db):
    import_bytes(db, GOOD, "a.csv")
    assert import_bytes(db, GOOD, "a.csv").status == "ALREADY_IMPORTED" and count(db) == 3


def test_duplicates_within_file_and_across_files(db):
    dup = GOOD + b"Crash,1,2024-01-01T00:00:00Z,2.5\n"
    r = import_bytes(db, dup, "dup.csv")
    assert r.duplicates_in_file == 1 and r.inserted == 3
    other = b"game,round_id,timestamp,multiplier\nCrash,3,2024-01-01T00:00:40Z,10\nCrash,4,2024-01-01T00:01:00Z,3\n"
    r2 = import_bytes(db, other, "o.csv")
    assert r2.duplicates_in_db == 1 and r2.inserted == 1 and count(db) == 4


def test_malformed_and_missing_values_counted(db):
    bad = (b"game,round_id,timestamp,multiplier\nCrash,1,2024-01-01T00:00:00Z,abc\nCrash,2,not-a-date,2\n"
           b"Crash,,2024-01-01T00:00:00Z,2\nCrash,4,2024-01-01T00:00:00Z,-3\nCrash,5,2024-01-01T00:00:00Z,\nCrash,6,2024-01-01T00:00:00Z,1.5\n")
    r = import_bytes(db, bad, "b.csv")
    assert r.total_rows == 6 and r.valid_rows == 1 and r.malformed_rows == 5 and r.inserted == 1
    assert r.missing_values["multiplier"] == 1 and r.missing_values["round_id"] == 1 and r.sample_errors


def test_column_aliases_and_epoch_timestamps(db):
    c = b"Game Name,ID,ts,crash_point,stake,payout_amount\nCrash,a1,1704067200,2.0,10,20\nCrash,a2,1704067200000,1.0,10,0\n"
    assert import_bytes(db, c, "alias.csv").inserted == 2


def test_json_formats(db):
    rows = [{"game": "Limbo", "round_id": "j1", "timestamp": "2024-01-01T00:00:00+00:00", "multiplier": 4.2}]
    assert import_bytes(db, json.dumps(rows).encode(), "a.json").inserted == 1
    assert import_bytes(db, json.dumps({"rounds": [{**rows[0], "round_id": "j2"}]}).encode(), "b.json").inserted == 1
    with pytest.raises(ImportFailure):
        import_bytes(db, b'"just a string"', "c.json")
    with pytest.raises(ImportFailure):
        import_bytes(db, b"{not json", "d.json")


@pytest.mark.parametrize("content,name", [(b"", "e.csv"), (b"game,round_id\nCrash,1\n", "m.csv"),
                                           (b"\x00\x01\x02\xff", "bin.csv"), (b"a,b\n1,2\n", "x.txt")])
def test_invalid_files_fail_cleanly(db, content, name):
    with pytest.raises(ImportFailure):
        import_bytes(db, content, name)
    assert count(db) == 0


def test_limits(db):
    with pytest.raises(ImportFailure):
        import_bytes(db, GOOD, "a.csv", max_bytes=10)
    with pytest.raises(ImportFailure):
        import_bytes(db, GOOD, "a.csv", max_rows=2)


def test_cannot_label_import_as_official(db):
    for label in ("VERIFIED_OFFICIAL", "OFFICIAL", "anything"):
        with pytest.raises(ValueError):
            import_bytes(db, GOOD, "a.csv", label)


def test_empty_dataset_rows_only_header(db):
    with pytest.raises(ImportFailure):
        import_bytes(db, b"game,round_id,timestamp,multiplier\n", "h.csv")


def test_sample_demo_dataset_is_synthetic(db):
    p = Path(__file__).resolve().parent.parent / "sample_data" / "demo_synthetic_crash_rounds.csv"
    r = import_bytes(db, p.read_bytes(), p.name, "SYNTHETIC_DEMO")
    assert r.inserted == 3000
    assert db.query_one("SELECT DISTINCT source_type s FROM historical_rounds")["s"] == "SYNTHETIC_DEMO"
