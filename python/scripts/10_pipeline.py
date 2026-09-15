from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main():
    script_dir = Path(__file__).resolve().parent
    python_dir = script_dir.parent

    steps = [
        "01_profile_mongodb.py",
        "02_clean_recipes.py",
        "03a_build_unit_weight_map.py",
        "03_normalize_recipes.py",
        "04_import_recipes_mysql.py",
        "04c_build_recipe_categories.py",
        "04a_import_reference_maps.py",
        "04b_apply_weight_estimates.py",
        "05_import_nutrition_excel.py",
        "06_match_ingredient_nutrition.py",
        "07_auto_review_nutrition.py",
        "08_apply_manual_review.py",
        "09_calculate_recipe_nutrition.py",
    ]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(python_dir)

    for step in steps:
        print(f"\n===== RUN {step} =====", flush=True)

        subprocess.run(
            [sys.executable, str(script_dir / step)],
            check=True,
            cwd=str(python_dir),
            env=env,
        )

        print(f"===== DONE {step} =====", flush=True)

    print(
        "\nFORMAL V8 PIPELINE COMPLETED: "
        "MongoDB -> MySQL -> API",
        flush=True,
    )


if __name__ == "__main__":
    main()
