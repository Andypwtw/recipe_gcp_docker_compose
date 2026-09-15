from __future__ import annotations
import json
from pathlib import Path
from app.db import get_connection

UNIT_MAP=Path("/workspace/data/reference/unit_weight_map.json")
DENSITY=Path("/workspace/data/reference/ingredient_density_map.json")

def main():
    with get_connection() as conn, conn.cursor() as cur:
        if UNIT_MAP.exists():
            for r in json.loads(UNIT_MAP.read_text(encoding="utf-8")):
                cur.execute("INSERT INTO ingredients(canonical_name) VALUES(%s) ON DUPLICATE KEY UPDATE canonical_name=VALUES(canonical_name)",(r["ingredient"],))
                cur.execute("SELECT id FROM ingredients WHERE canonical_name=%s",(r["ingredient"],)); iid=cur.fetchone()["id"]
                cur.execute("INSERT INTO units(canonical_unit,unit_type) VALUES(%s,'count') ON DUPLICATE KEY UPDATE canonical_unit=VALUES(canonical_unit)",(r["unit"],))
                cur.execute("SELECT id FROM units WHERE canonical_unit=%s",(r["unit"],)); uid=cur.fetchone()["id"]
                cur.execute(
                    """
                    INSERT INTO ingredient_unit_weights(ingredient_id,unit_id,grams_per_unit,status,source)
                    VALUES(%s,%s,%s,'ACTIVE','unit_weight_map.json')
                    ON DUPLICATE KEY UPDATE grams_per_unit=VALUES(grams_per_unit), status='ACTIVE'
                    """,(iid,uid,r["grams_per_unit"])
                )
        if DENSITY.exists():
            for r in json.loads(DENSITY.read_text(encoding="utf-8")):
                cur.execute("INSERT INTO ingredients(canonical_name) VALUES(%s) ON DUPLICATE KEY UPDATE canonical_name=VALUES(canonical_name)",(r["ingredient"],))
                cur.execute("SELECT id FROM ingredients WHERE canonical_name=%s",(r["ingredient"],)); iid=cur.fetchone()["id"]
                cur.execute(
                    """
                    INSERT INTO ingredient_densities(ingredient_id,density_g_ml,density_type,status,source,note)
                    VALUES(%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE density_g_ml=VALUES(density_g_ml),status=VALUES(status),note=VALUES(note)
                    """,
                    (iid,r["density_g_ml"],r.get("density_type","liquid"),r.get("status","ACTIVE"),r.get("source","json"),r.get("note"))
                )
        conn.commit()
    print("reference maps imported")

if __name__ == "__main__":
    main()
