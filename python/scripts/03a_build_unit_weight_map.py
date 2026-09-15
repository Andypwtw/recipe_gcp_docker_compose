from __future__ import annotations
import json, re
from pathlib import Path

SRC = Path("/workspace/data/processed/recipes_clean.json")
OUT = Path("/workspace/data/reference/unit_weight_map.json")
CAND = Path("/workspace/data/reference/unit_weight_candidates.json")

PAT = re.compile(r"(?P<name>[^|]+?)\s+(?P<count>\d+(?:\.\d+)?)\s*(?P<unit>顆|朵|片|格|隻|尾|條|根|杯|碗|匙).*?[（(]約?(?P<grams>\d+(?:\.\d+)?)\s*(?:公克|克|g)[）)]")

def main():
    rows = json.loads(SRC.read_text(encoding="utf-8"))
    candidates = []
    for r in rows:
        text = r.get("材料", "")
        for m in PAT.finditer(text):
            count = float(m.group("count"))
            grams = float(m.group("grams"))
            if count > 0:
                candidates.append({
                    "ingredient": m.group("name").strip(),
                    "unit": m.group("unit"),
                    "grams_per_unit": round(grams/count, 6),
                    "source_seq": r.get("SEQ"),
                })
    CAND.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
    merged = {}
    for c in candidates:
        key = (c["ingredient"], c["unit"])
        merged.setdefault(key, []).append(c["grams_per_unit"])
    active = [
        {"ingredient": k[0], "unit": k[1], "grams_per_unit": round(sum(v)/len(v), 6), "status": "ACTIVE"}
        for k, v in merged.items()
    ]
    OUT.write_text(json.dumps(active, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"unit weight map={len(active)}")

if __name__ == "__main__":
    main()
