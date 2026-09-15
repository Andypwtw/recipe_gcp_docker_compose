from __future__ import annotations
import json
from pathlib import Path
from crawler.kafka_producer import create_producer, publish_recipe

SOURCE=Path("/opt/airflow/project/data/raw/ytower_seq_recipes.json")

def main():
    rows=json.loads(SOURCE.read_text(encoding="utf-8"))
    producer=create_producer()
    for r in rows:
        if r.get("SEQ"):
            publish_recipe(producer,r)
    producer.flush()
    print(f"published={len(rows)}")

if __name__=="__main__":
    main()
