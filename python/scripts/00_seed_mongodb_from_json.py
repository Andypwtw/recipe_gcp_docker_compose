from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
from app.mongo_db import get_raw_recipe_collection

SOURCE = Path("/workspace/data/raw/ytower_seq_recipes.json")

def main():
    rows = json.loads(SOURCE.read_text(encoding="utf-8"))
    col = get_raw_recipe_collection()
    col.create_index("SEQ", unique=True)
    for row in rows:
        seq = row.get("SEQ")
        if not seq:
            continue
        doc = dict(row)
        doc["source"] = "ytower"
        doc["ingested_at"] = datetime.now(timezone.utc)
        col.update_one({"SEQ": seq}, {"$set": doc}, upsert=True)
    print(f"seeded {len(rows)} recipes to MongoDB")

if __name__ == "__main__":
    main()
