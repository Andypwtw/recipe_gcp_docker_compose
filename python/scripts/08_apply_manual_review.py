from __future__ import annotations

from app.db import get_connection


def main():
    """
    將 07_auto_review_nutrition.py 已經判定為 APPROVED 的候選
    寫入 ingredient_nutrition_map。

    REJECTED 只保留在 manual_review 作為 audit trail，不建立 mapping。
    """
    with get_connection() as conn, conn.cursor() as cur:
        # 防止同一 ingredient 出現多筆 APPROVED。
        cur.execute(
            """
            SELECT ingredient_id, COUNT(*) AS cnt
            FROM manual_review
            WHERE status = 'APPROVED'
            GROUP BY ingredient_id
            HAVING COUNT(*) > 1
            """
        )
        duplicates = cur.fetchall()

        if duplicates:
            raise RuntimeError(
                "Automatic review produced more than one APPROVED "
                f"candidate for ingredient(s): {duplicates}"
            )

        cur.execute(
            """
            SELECT
                ingredient_id,
                candidate_nutrition_id,
                score
            FROM manual_review
            WHERE status = 'APPROVED'
            ORDER BY ingredient_id
            """
        )
        approved_rows = cur.fetchall()

        for row in approved_rows:
            cur.execute(
                """
                INSERT INTO ingredient_nutrition_map
                (
                    ingredient_id,
                    nutrition_source_id,
                    match_method,
                    match_score,
                    status,
                    reviewed_at
                )
                VALUES (%s, %s, 'auto_review', %s, 'APPROVED', NOW())
                ON DUPLICATE KEY UPDATE
                    nutrition_source_id = VALUES(nutrition_source_id),
                    match_method = 'auto_review',
                    match_score = VALUES(match_score),
                    status = 'APPROVED',
                    reviewed_at = NOW()
                """,
                (
                    row["ingredient_id"],
                    row["candidate_nutrition_id"],
                    float(row["score"]),
                ),
            )

        conn.commit()

    print(
        "Automatic review mappings applied. "
        f"approved mappings={len(approved_rows)}"
    )


if __name__ == "__main__":
    main()
