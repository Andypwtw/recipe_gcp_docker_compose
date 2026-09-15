from __future__ import annotations

import json
import re
from pathlib import Path

from app.services.normalization import (
    clean_text,
    parse_number,
    normalize_unit,
    direct_weight_g,
)


SRC = Path("/workspace/data/processed/recipes_clean.json")
OUT = Path("/workspace/data/processed/recipes_normalized.json")

LINE_SPLIT = re.compile(r"\s*\|\s*")
QUALITATIVE_TERMS = ("少許", "適量", "酌量", "少量", "些許")

# 前置材料編號，如：
# 1.牛小排 4塊
# 2、馬鈴薯 1個
# A.醬油 1大匙
LEADING_ITEM_NO_RE = re.compile(
    r"^\s*(?:\d{1,3}|[A-Za-z])\s*[\.、\)]\s*"
)

# Keep longer units before shorter units.
UNIT_PATTERN = (
    r"公斤|公克|毫升|公升|盎司|人份|量杯|"
    r"大匙|湯匙|小匙|茶匙|"
    r"小塊|大塊|小片|大片|小段|大段|大碗|小碗|中匙|"
    r"c\.c\.|C\.C\.|c\.c|C\.C|"
    r"mL|ML|ml|㏄|cc|CC|kg|lb|oz|"
    r"克|斤|兩|錢|钱|磅|g|L|l|"
    r"個|顆|朵|片|格|隻|尾|條|根|杯|碗|匙|"
    r"支|粒|塊|包|張|罐|盒|把|瓣|份|棵|枝|株|瓶|副|"
    r"段|球|葉|枚|串|束|卷|捲|袋|管|盤|鍋|桶"
)

# Number atom:
# 1又1/2 | 1 1/2 | 1/2 | 1.5 | 1
NUMBER_ATOM = (
    r"(?:"
    r"\d+\s*又\s*\d+\s*/\s*\d+"
    r"|"
    r"\d+\s+\d+\s*/\s*\d+"
    r"|"
    r"\d+\s*/\s*\d+"
    r"|"
    r"\d+(?:\.\d+)?"
    r")"
)

# Optional range:
# 2~3 / 5-6 / 1/2~1
AMOUNT_RE = re.compile(
    rf"^(?P<name>.*?)"
    rf"(?P<qty1>{NUMBER_ATOM})"
    rf"(?:\s*(?P<range_sep>~|-)\s*(?P<qty2>{NUMBER_ATOM}))?"
    rf"\s*"
    rf"(?P<unit>{UNIT_PATTERN})?"
    rf"(?P<trailing>.*)$"
)

QUALITATIVE_RE = re.compile(
    r"^(?P<name>.*?)\s*(?P<qual>少許|適量|酌量|少量|些許)\s*$"
)

SOFT_SUFFIXES = (
    "厚片", "薄片", "切片", "切絲", "切丁",
    "丁", "塊", "條", "片", "絲", "末", "碎",
    "粒", "葉", "梗", "蒂", "圈", "段", "泥",
    "茸", "蓉"
)


def canonicalize_ingredient_name(name: str) -> str:
    s = clean_text(name)

    # Source strings such as "牛肉 約1/2斤" are parsed with raw_name="牛肉 約".
    # Strip a trailing approximation marker so nutrition matching uses "牛肉".
    s = re.sub(r"\s*約\s*$", "", s).strip()

    changed = True
    while changed:
        changed = False
        for suffix in SOFT_SUFFIXES:
            if len(s) > len(suffix) + 1 and s.endswith(suffix):
                s = s[:-len(suffix)].strip()
                changed = True
                break

    return s


def strip_leading_item_number(text: str) -> str:
    return LEADING_ITEM_NO_RE.sub("", clean_text(text), count=1)


def split_original_materials(text: str) -> list[str]:
    if not isinstance(text, str):
        return []
    return [
        part.strip()
        for part in text.split("|")
        if part.strip()
    ]


def split_clean_materials(text: str) -> list[str]:
    if not isinstance(text, str):
        return []
    return [
        part.strip()
        for part in LINE_SPLIT.split(clean_text(text))
        if part.strip()
    ]


def parse_material_line(clean_raw: str) -> dict:
    source = strip_leading_item_number(clean_raw)

    # First handle qualitative units because they contain no numeric quantity.
    qualitative_match = QUALITATIVE_RE.match(source)

    if qualitative_match:
        raw_name = clean_text(qualitative_match.group("name"))
        qualitative_unit = qualitative_match.group("qual")
        return {
            "raw_name": raw_name,
            "canonical_name": canonicalize_ingredient_name(raw_name),
            "quantity_min": None,
            "quantity_max": None,
            "quantity_value": None,
            "unit": qualitative_unit,
            "weight_g": None,
            "is_estimated": True,
        }

    amount_match = AMOUNT_RE.match(source)

    if amount_match:
        raw_name = clean_text(amount_match.group("name"))
        qty1 = parse_number(amount_match.group("qty1"))
        qty2 = parse_number(amount_match.group("qty2") or "")
        unit = normalize_unit(amount_match.group("unit") or "")
        trailing = clean_text(amount_match.group("trailing") or "")

        # Important guard:
        # A numeric token is considered a quantity only when:
        # - a recognized unit exists, OR
        # - there is no trailing text after the number.
        # This avoids interpreting model/product codes inside ingredient names.
        if unit or not trailing:
            quantity_min = qty1
            quantity_max = qty2 if qty2 is not None else qty1

            if qty1 is not None and qty2 is not None:
                quantity_value = (qty1 + qty2) / 2
                estimated = True
            else:
                quantity_value = qty1
                estimated = False

            return {
                "raw_name": raw_name,
                "canonical_name": canonicalize_ingredient_name(raw_name),
                "quantity_min": quantity_min,
                "quantity_max": quantity_max,
                "quantity_value": quantity_value,
                "unit": unit,
                "weight_g": direct_weight_g(quantity_value, unit),
                "is_estimated": estimated,
            }

    raw_name = source
    return {
        "raw_name": raw_name,
        "canonical_name": canonicalize_ingredient_name(raw_name),
        "quantity_min": None,
        "quantity_max": None,
        "quantity_value": None,
        "unit": "",
        "weight_g": None,
        "is_estimated": False,
    }


def parse_materials(cleaned_text: str, original_text: str):
    clean_items = split_clean_materials(cleaned_text)
    original_items = split_original_materials(original_text)

    result = []

    for idx, clean_raw in enumerate(clean_items, 1):
        display_raw = (
            original_items[idx - 1]
            if idx <= len(original_items)
            else clean_raw
        )

        parsed = parse_material_line(clean_raw)

        result.append(
            {
                "line_no": idx,
                "raw_text": display_raw,
                **parsed,
            }
        )

    return result


def main():
    rows = json.loads(SRC.read_text(encoding="utf-8"))
    out = []

    for r in rows:
        out.append(
            {
                "seq": r.get("SEQ"),
                "name": r.get("食譜名稱"),
                "published_date": r.get("上線日期"),
                "raw_keywords": r.get("關鍵字"),
                "source_url": r.get("食譜網址"),
                "steps": r.get("做法步驟"),
                "ingredients": parse_materials(
                    r.get("材料", ""),
                    r.get("材料_原始", r.get("材料", "")),
                ),
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"normalized={len(out)}")


if __name__ == "__main__":
    main()
