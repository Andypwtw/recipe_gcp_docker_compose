from __future__ import annotations
import sys
from app.db import get_connection
from app.mongo_db import get_mongo_client

print("python:", sys.executable)
with get_connection() as conn, conn.cursor() as cur:
    cur.execute("SELECT VERSION() AS version")
    print("mysql:", cur.fetchone())
print("mongodb:", get_mongo_client().admin.command("ping"))
