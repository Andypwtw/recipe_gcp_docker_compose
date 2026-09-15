from __future__ import annotations
import json
from pathlib import Path
from collections import Counter
from app.mongo_db import load_raw_recipes

OUT = Path("/workspace/data/processed/profile_report.json")

def main():
    rows = load_raw_recipes()
    fields = ["SEQ","食譜名稱","上線日期","關鍵字","食譜網址","材料","做法步驟"]
    missing = Counter()
    for r in rows:
        for f in fields:
            if r.get(f) in (None, ""):
                missing[f] += 1
    report = {"source": "mongodb", "total_recipes": len(rows), "missing_fields": dict(missing)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
