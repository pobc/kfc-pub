from __future__ import annotations

import pytest

from app.database import db


@pytest.fixture(scope="session", autouse=True)
def real_database_pool():
    db.open()
    yield
    db.close()
