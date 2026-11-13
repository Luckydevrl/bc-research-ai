import pytest

from database.db import Database
from services.accounting import Accounting


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.migrate()
    return d


@pytest.fixture
def acct(db):
    a = Accounting(db)
    a.ensure_wallet("1000")
    return a
