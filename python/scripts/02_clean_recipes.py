from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from app.mongo_db import load_raw_recipes
from app.services.normalization import clean_text


OUT = Path("/workspace/data/processed/recipes_clean.json")
DEDUP_REPORT = Path("/workspace/data/processed/dedup_report.json")

# 僅移除「食譜名稱結尾的 (數字)」
# 例如：
#   紅燒牛肉(1)  -> 紅燒牛肉
#   紅燒牛肉(15) -> 紅燒牛肉
#
# 不會影響：
#   雞蛋(大)料理
#   牛肉(美國)炒飯
NUMBERED_SUFFIX_RE = re.compile(r"\s*\((\d+)\)\s*$")


def get_base_name(name: str) -> tuple[str, int | None]:
    """
    取得食譜去重用 base_name。

    回傳：
      base_name:
        移除名稱結尾 (數字) 後的名稱

      suffix_no:
        尾碼數字；沒有尾碼則為 None
    """
    text = clean_text(name or "").strip()

    match = NUMBERED_SUFFIX_RE.search(text)

    if not match:
        return text, None

    base_name = NUMBERED_SUFFIX_RE.sub("", text).strip()
    suffix_no = int(match.group(1))

    return base_name, suffix_no


def choose_recipe(group: list[dict]) -> dict:
    """
    同一 base_name 只保留一筆。

    優先規則：
    1. 有原始無 (數字) 尾碼版本時，優先保留。
    2. 若全部都有尾碼，保留 MongoDB 原始順序中的第一筆。
    """
    for item in group:
        if item["_suffix_no"] is None:
            return item

    return group[0]


def main():
    rows = load_raw_recipes()

    # --------------------------------------------------
    # Step 1：先依 base_name 分組
    # --------------------------------------------------
    grouped: dict[str, list[dict]] = defaultdict(list)

    empty_name_rows = []

    for index, row in enumerate(rows):
        original_name = str(
            row.get("食譜名稱", "") or ""
        )

        base_name, suffix_no = get_base_name(
            original_name
        )

        if not base_name:
            empty_name_rows.append(
                {
                    "index": index,
                    "seq": row.get("SEQ"),
                    "original_name": original_name,
                }
            )
            continue

        grouped[base_name].append(
            {
                "_row": row,
                "_index": index,
                "_original_name": original_name,
                "_base_name": base_name,
                "_suffix_no": suffix_no,
            }
        )

    # --------------------------------------------------
    # Step 2：每個 base_name 只保留一筆
    # --------------------------------------------------
    selected = []
    removed = []

    for base_name, group in grouped.items():
        keep = choose_recipe(group)
        selected.append(keep)

        for item in group:
            if item is keep:
                continue

            removed.append(
                {
                    "base_name": base_name,
                    "removed_seq": (
                        item["_row"].get("SEQ")
                    ),
                    "removed_name": (
                        item["_original_name"]
                    ),
                    "kept_seq": (
                        keep["_row"].get("SEQ")
                    ),
                    "kept_name_before_normalize": (
                        keep["_original_name"]
                    ),
                }
            )

    # 保留 MongoDB 原始順序
    selected.sort(
        key=lambda item: item["_index"]
    )

    # --------------------------------------------------
    # Step 3：執行原本資料清洗
    # --------------------------------------------------
    cleaned = []

    for selected_item in selected:
        row = selected_item["_row"]
        base_name = selected_item["_base_name"]

        item = dict(row)

        # --------------------------------------------------
        # 顯示用途：
        # 完整保留 MongoDB 來源「材料」原始字串。
        # API 顯示使用此資料。
        # --------------------------------------------------
        raw_materials = row.get("材料", "")

        item["材料_原始"] = (
            raw_materials
            if isinstance(raw_materials, str)
            else ""
        )

        # --------------------------------------------------
        # 食譜名稱：
        # 即使保留來源是「紅燒牛肉(1)」，
        # 也統一寫回 base_name「紅燒牛肉」。
        # --------------------------------------------------
        item["食譜名稱"] = base_name

        # --------------------------------------------------
        # 計算 / Matching / 正規化：
        # 維持原有清洗流程。
        # --------------------------------------------------
        for key in [
            "SEQ",
            "上線日期",
            "關鍵字",
            "食譜網址",
            "材料",
            "做法步驟",
        ]:
            if (
                key in item
                and isinstance(item[key], str)
            ):
                item[key] = clean_text(
                    item[key]
                )

        cleaned.append(item)

    # --------------------------------------------------
    # Step 4：輸出清洗資料
    # --------------------------------------------------
    OUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUT.write_text(
        json.dumps(
            cleaned,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    duplicate_group_count = sum(
        1
        for group in grouped.values()
        if len(group) > 1
    )

    # --------------------------------------------------
    # Step 5：輸出去重稽核報告
    # --------------------------------------------------
    report = {
        "input_count": len(rows),
        "output_count": len(cleaned),
        "removed_duplicate_count": len(removed),
        "duplicate_group_count": (
            duplicate_group_count
        ),
        "empty_name_skipped_count": (
            len(empty_name_rows)
        ),
        "rule": (
            "Remove trailing (number), "
            "group by base_name, "
            "prefer unsuffixed recipe, "
            "otherwise keep first."
        ),
        "removed": removed,
        "empty_name_rows": empty_name_rows,
    }

    DEDUP_REPORT.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    print(
        f"input recipes={len(rows)}"
    )
    print(
        f"duplicate groups="
        f"{duplicate_group_count}"
    )
    print(
        f"removed duplicates="
        f"{len(removed)}"
    )
    print(
        f"empty names skipped="
        f"{len(empty_name_rows)}"
    )
    print(
        f"cleaned recipes="
        f"{len(cleaned)} -> {OUT}"
    )
    print(
        f"dedup report -> "
        f"{DEDUP_REPORT}"
    )


if __name__ == "__main__":
    main()
