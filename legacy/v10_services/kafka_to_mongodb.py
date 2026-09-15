from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from kafka import KafkaConsumer
from pymongo import MongoClient

def main():
    topic = os.getenv("KAFKA_RESULT_TOPIC", "ytower-recipes")
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")

    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap,
        group_id="recipe-mongodb-writer",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
    )

    client = MongoClient(
        os.getenv(
            "MONGO_URI",
            "mongodb://root:rootpassword@mongodb:27017/recipe_ai?authSource=admin",
        ),
        serverSelectionTimeoutMS=5000,
    )
    collection = client[
        os.getenv("MONGO_DATABASE", "recipe_ai")
    ][
        os.getenv("MONGO_COLLECTION", "raw_recipes")
    ]

    collection.create_index("SEQ", unique=True)

    print("Kafka -> MongoDB consumer started", flush=True)

    for message in consumer:
        recipe = message.value
        seq = recipe.get("SEQ")

        if not seq:
            print("Skip message without SEQ", flush=True)
            consumer.commit()
            continue

        recipe["source"] = "ytower"
        recipe["ingested_at"] = datetime.now(timezone.utc)

        collection.update_one(
            {"SEQ": seq},
            {"$set": recipe},
            upsert=True,
        )

        consumer.commit()

        print(
            f"upserted seq={seq} partition={message.partition} offset={message.offset}",
            flush=True,
        )

if __name__ == "__main__":
    main()
