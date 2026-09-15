from __future__ import annotations
import json
from pathlib import Path
from app.db import get_connection

OUT=Path("/workspace/data/manual_review/manual_review.json")

def main():
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT mr.id AS review_id,mr.ingredient_id,i.canonical_name AS ingredient_name,
                   mr.candidate_nutrition_id,mr.candidate_name,mr.score,mr.status
            FROM manual_review mr
            JOIN ingredients i ON i.id=mr.ingredient_id
            WHERE mr.status='PENDING'
            ORDER BY i.canonical_name,mr.score DESC,mr.id
            """
        )
        rows=cur.fetchall()
    for r in rows:
        r["score"]=str(r["score"])
        r["decision"]=""
        r["note"]=""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2),encoding="utf-8")
    print(f"manual review exported={len(rows)} -> {OUT}")

if __name__ == "__main__":
    main()
