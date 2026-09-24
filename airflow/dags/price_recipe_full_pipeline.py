"""每 14 天更新 MOA 食品價格，再重建食譜營養資料。"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator


DEFAULT_ARGS = {
    "owner": "airflow",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
}


with DAG(
    dag_id="price_recipe_full_pipeline",
    description="每 14 天依序更新 MOA 食品價格與食譜營養資料",
    default_args=DEFAULT_ARGS,
    start_date=datetime(2026, 9, 22),
    schedule_interval=timedelta(days=14),
    catchup=False,
    max_active_runs=1,
    tags=["price", "moa", "nutrition", "recipe"],
) as dag:
    update_moa_prices = BashOperator(
        task_id="update_moa_prices",
        bash_command="""
            set -euo pipefail
            cd /workspace/python/Price
            python 09_pipeline.py
        """,
    )

    rebuild_recipe_nutrition = BashOperator(
        task_id="rebuild_recipe_nutrition",
        bash_command="""
            set -euo pipefail
            cd /workspace/python
            python scripts/10_pipeline.py
        """,
    )

    update_moa_prices >> rebuild_recipe_nutrition
