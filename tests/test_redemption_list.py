from __future__ import annotations

from datetime import datetime, timezone

from app.database.access import access_link_redemption_db


def test_redemptions_are_sorted_by_latest_activity_and_expose_ordered_at(monkeypatch) -> None:
    executed_sql: list[str] = []
    started_at = datetime(2026, 9, 22, 17, 19, 23, tzinfo=timezone.utc)

    def fake_fetchone(sql: str, _args: tuple[object, ...]) -> dict[str, int]:
        executed_sql.append(sql)
        return {"n": 1}

    def fake_fetchall(sql: str, _args: tuple[object, ...] = ()) -> list[dict[str, object]]:
        executed_sql.append(sql)
        if "GROUP BY product_id" in sql:
            return [{"product_id": None, "n": 1}]
        return [
            {
                "id": 99,
                "public_id": "test-access-link",
                "redemption_status": "COMPLETED",
                "redemption_payload": {},
                "redemption_started_at": started_at,
                "createdAt": datetime(2026, 9, 1, tzinfo=timezone.utc),
            }
        ]

    monkeypatch.setattr(access_link_redemption_db.db, "fetchone", fake_fetchone)
    monkeypatch.setattr(access_link_redemption_db.db, "fetchall", fake_fetchall)

    result = access_link_redemption_db.list_redemptions()

    list_sql = next(sql for sql in executed_sql if "FROM access_links l" in sql and "SELECT l.*" in sql)
    assert "l.redemption_finished_at" in list_sql
    assert "l.redemption_started_at" in list_sql
    assert 'l."updatedAt"' in list_sql
    assert result["items"][0]["orderedAt"] == "2026-09-22T17:19:23Z"
