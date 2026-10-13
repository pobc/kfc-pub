from app.database.integrity_db import orphan_counts


def test_database_has_no_orphaned_logical_relations() -> None:
    counts = orphan_counts()
    failures = {relation: count for relation, count in counts.items() if count}
    assert not failures, f"数据库存在逻辑孤儿记录: {failures}"
