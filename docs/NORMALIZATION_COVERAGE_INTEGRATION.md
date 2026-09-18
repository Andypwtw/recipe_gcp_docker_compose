# Production integration: normalization / nutrition coverage

This project keeps the uploaded production GCP architecture as the source of truth:

```text
Airflow -> Kafka -> Crawler -> Mongo Writer -> MongoDB
                                   |
                                   v
                         ETL 01~09 / 10_pipeline.py
                                   |
                                   v
                                 MySQL
                                   |
                                   v
                          Flask API / Hermes
```

The integration intentionally **does not** replace production components with the JSON-only test architecture. In particular:

- `docker-compose.yml` remains the uploaded 18-service production compose.
- `01_profile_mongodb.py` remains active.
- `02_clean_recipes.py` continues to read MongoDB.
- `10_pipeline.py` continues to start from `01_profile_mongodb.py`.
- `python/pyproject.toml` retains `pymongo` and `kafka-python-ng`.
- Python container remains internal port `5000`, published through host `${FLASK_PORT:-5001}`.

## Added / changed ETL behavior

1. `03a_build_unit_weight_map.py`
   - Derives ingredient + count-unit gram weights only from explicit recipe annotations such as `3瓣（約15g）`.
   - Uses median/outlier handling and records sample count / confidence.

2. `03_normalize_recipes.py` + `normalization.py`
   - Removes A-I / Chinese / numeric group prefixes without damaging names such as `X.O醬`.
   - Preserves digit-containing product names such as `A1醬` and `8吋戚風蛋糕`.
   - Rejects malformed zero quantities instead of creating `weight_g=0`.

3. `04a_import_reference_maps.py`
   - Imports `qualitative_amount_rules.json`.
   - Self-creates `qualitative_amount_rules` for existing MySQL volumes.

4. `04b_apply_weight_estimates.py`
   - Applies explicit unit-weight map first, then densities, then only high-confidence qualitative rules (`confidence >= 80`, `auto_convert=true`).
   - Generic `適量` remains unresolved unless a future researched rule explicitly enables it.

5. `06_match_ingredient_nutrition.py` / `07_auto_review_nutrition.py`
   - Uses shared nutrition-name normalization.
   - Adds exact/common-name/normalized variant/curated safe target/guarded fuzzy matching.

6. `09_calculate_recipe_nutrition.py`
   - Calorie and price coverage are weight-based.
   - `weight_coverage_percent` remains a line-based guard against recipes with too many unknown quantities.
   - Water/ice are excluded from food-price denominator.

## Existing MySQL volume

Fresh databases receive the new table/column from `mysql/init/001_schema.sql`.

Existing databases are supported in two ways:

- `04a_import_reference_maps.py` creates `qualitative_amount_rules` if missing.
- `09_calculate_recipe_nutrition.py` adds missing summary columns if required.

Migration is also provided:

```text
mysql/migrations/010_qualitative_rules_weighted_coverage.sql
```

## Runtime validation on GCP

After deployment:

```bash
docker compose config
docker compose up -d --build
docker compose ps
```

Run ETL inside the production Python container:

```bash
docker compose exec python uv run python scripts/10_pipeline.py
```

Then inspect coverage:

```sql
SELECT calorie_status, COUNT(*)
FROM recipe_nutrition_summary
GROUP BY calorie_status;

SELECT price_status, COUNT(*)
FROM recipe_nutrition_summary
GROUP BY price_status;

SELECT
  ROUND(AVG(coverage_percent),2) AS avg_calorie_weight_coverage,
  ROUND(AVG(price_coverage_percent),2) AS avg_price_weight_coverage,
  ROUND(AVG(weight_coverage_percent),2) AS avg_known_weight_line_coverage
FROM recipe_nutrition_summary;
```
