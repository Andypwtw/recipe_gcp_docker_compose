# Architecture

```text
Airflow
  ↓ schedule
YTower Crawler
  ↓
Kafka: ytower-recipes
  ↓
Kafka Consumer
  ↓
MongoDB: recipe_ai.raw_recipes
  ↓
01_profile_mongodb.py
  ↓
02_clean_recipes.py
  ↓
03a_build_unit_weight_map.py
  ↓
03_normalize_recipes.py
  ↓
04_import_recipes_mysql.py
  ↓
04a_import_reference_maps.py
  ↓
05_import_nutrition_excel.py
  ↓
06_match_ingredient_nutrition.py
  ↓
07_export_manual_review.py
  ↓ manual
08_apply_manual_review.py
  ↓
09_calculate_recipe_nutrition.py
  ↓
MySQL
  ↓
Flask API
  ↓
Hermes / LINE
```
