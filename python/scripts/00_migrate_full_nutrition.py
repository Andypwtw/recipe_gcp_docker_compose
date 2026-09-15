from __future__ import annotations

from app.db import get_connection


def column_exists(cur, table_name, column_name):
    cur.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
        """,
        (table_name, column_name),
    )
    return cur.fetchone()["cnt"] > 0


def main():
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS nutrient_definitions (
              id BIGINT PRIMARY KEY AUTO_INCREMENT,
              nutrient_name VARCHAR(255) NOT NULL,
              unit VARCHAR(50),
              source_column_name VARCHAR(255) NOT NULL,
              nutrient_group VARCHAR(100),
              display_order INT NOT NULL,
              created_at TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP,
              UNIQUE KEY uq_nutrient_source_column
                (source_column_name)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS nutrition_values (
              nutrition_source_id BIGINT NOT NULL,
              nutrient_id BIGINT NOT NULL,
              value_numeric DECIMAL(24,10),
              value_text VARCHAR(255),
              PRIMARY KEY
                (nutrition_source_id, nutrient_id),
              CONSTRAINT fk_nv_source
                FOREIGN KEY (nutrition_source_id)
                REFERENCES nutrition_source(id)
                ON DELETE CASCADE,
              CONSTRAINT fk_nv_definition
                FOREIGN KEY (nutrient_id)
                REFERENCES nutrient_definitions(id)
                ON DELETE CASCADE
            )
            """
        )

        additions = [
            ("nutrition_source", "food_category",
             "VARCHAR(255) NULL"),
            ("nutrition_source", "content_description",
             "TEXT NULL"),
            ("nutrition_source", "common_names",
             "TEXT NULL"),
            ("nutrition_source", "waste_percent",
             "DECIMAL(18,6) NULL"),
            ("nutrition_source", "raw_data",
             "JSON NULL"),
        ]

        for table_name, column_name, sql_type in additions:
            if not column_exists(
                cur,
                table_name,
                column_name,
            ):
                cur.execute(
                    f"ALTER TABLE `{table_name}` "
                    f"ADD COLUMN `{column_name}` "
                    f"{sql_type}"
                )

        conn.commit()

    print(
        "Full nutrition normalized schema "
        "migration completed."
    )
    print(
        "Next: uv run python "
        "scripts/05_import_nutrition_excel.py"
    )


if __name__ == "__main__":
    main()
