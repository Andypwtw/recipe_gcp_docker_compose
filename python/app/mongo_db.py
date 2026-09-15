from __future__ import annotations
from pymongo import MongoClient
from pymongo.collection import Collection
from app.config import MONGO_URI, MONGO_DATABASE, MONGO_COLLECTION

def get_mongo_client() -> MongoClient:
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)

def get_raw_recipe_collection() -> Collection:
    client = get_mongo_client()
    return client[MONGO_DATABASE][MONGO_COLLECTION]

def load_raw_recipes() -> list[dict]:
    return list(get_raw_recipe_collection().find({}, {"_id": 0}))
