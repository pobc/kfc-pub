from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import db
from app.database.integrity_db import orphan_counts


def main() -> int:
    db.open()
    try:
        counts = orphan_counts()
    finally:
        db.close()
    failures = {relation: count for relation, count in counts.items() if count}
    if failures:
        for relation, count in failures.items():
            print(f"{relation}: {count}")
        return 1
    print("database logical integrity: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
