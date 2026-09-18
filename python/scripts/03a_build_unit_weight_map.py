from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

from app.services.normalization import canonicalize_ingredient_name, parse_number

SRC = Path("/workspace/data/processed/recipes_clean.json")
OUT = Path("/workspace/data/reference/unit_weight_map.json")
CAND = Path("/workspace/data/reference/unit_weight_candidates.json")

NUMBER = r"(?:\d+\s*又\s*\d+\s*/\s*\d+|\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d+(?:\.\d+)?)"
UNIT = r"顆|個|瓣|根|片|包|盒|支|條|朵|塊|把|束|杯|碗|罐|瓶|張|枚|尾|隻|粒|段|葉|串"
PAT = re.compile(
    rf"(?P<name>[^|]+?)\s*(?P<count>{NUMBER})\s*(?P<unit>{UNIT})"
    rf"[^|()（）]*[（(]\s*約?\s*(?P<grams>\d+(?:\.\d+)?)\s*(?:公克|克|g)\s*[）)]"
)


def robust_stats(values: list[float]) -> tuple[float, float, int]:
    values = sorted(v for v in values if v > 0)
    if not values:
        return 0.0, 0.0, 0
    median = statistics.median(values)
    if len(values) >= 4:
        q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
        iqr = q3 - q1
        if iqr > 0:
            low, high = max(0, q1 - 1.5 * iqr), q3 + 1.5 * iqr
            values = [v for v in values if low <= v <= high] or values
            median = statistics.median(values)
    dispersion = 0.0
    if len(values) > 1 and median > 0:
        dispersion = statistics.median(abs(v - median) for v in values) / median
    return median, dispersion, len(values)


def confidence(sample_count: int, dispersion: float) -> str:
    if sample_count >= 5 and dispersion <= 0.20:
        return "HIGH"
    if sample_count >= 2 and dispersion <= 0.35:
        return "MEDIUM"
    return "LOW"


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    CAND.parent.mkdir(parents=True, exist_ok=True)

    rows = json.loads(SRC.read_text(encoding="utf-8"))
    candidates = []
    for r in rows:
        for m in PAT.finditer(r.get("材料", "")):
            count = parse_number(m.group("count"))
            grams = float(m.group("grams"))
            if count and count > 0 and grams > 0:
                candidates.append(
                    {
                        "ingredient": canonicalize_ingredient_name(m.group("name")),
                        "unit": m.group("unit"),
                        "grams_per_unit": round(grams / count, 6),
                        "source_seq": r.get("SEQ"),
                    }
                )

    CAND.write_text(json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")

    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for c in candidates:
        grouped[(c["ingredient"], c["unit"])].append(c["grams_per_unit"])

    active = []
    for (ingredient, unit), values in sorted(grouped.items()):
        median, dispersion, sample_count = robust_stats(values)
        if median <= 0:
            continue
        active.append(
            {
                "ingredient": ingredient,
                "unit": unit,
                "grams_per_unit": round(median, 6),
                "sample_count": sample_count,
                "confidence": confidence(sample_count, dispersion),
                "dispersion_ratio": round(dispersion, 4),
                "status": "ACTIVE",
                "source": "YTOWER_EXPLICIT_GRAM_ANNOTATION",
            }
        )

    OUT.write_text(json.dumps(active, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"unit weight candidates={len(candidates)}")
    print(f"unit weight map={len(active)}")


if __name__ == "__main__":
    main()
