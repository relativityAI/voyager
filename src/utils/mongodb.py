import logging
from typing import Any, Dict, List, Optional, Union

from pymongo import ASCENDING, UpdateOne
from pymongo.mongo_client import MongoClient

from src.utils import console

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


# ==========================================
from dotenv import load_dotenv

load_dotenv()
import os

MONGODB_URL = os.getenv("MONGODB_URL")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME")
# ==========================================


class DB:
    def __init__(self, db_name: str = MONGODB_DB_NAME, uri: str = MONGODB_URL):
        self.client = MongoClient(uri)
        self.db = self.client[db_name]

        # self._test_connection()

    def _test_connection(self):
        try:
            self.client.admin.command("ping")
            console.log("🛢 MongoDB connection successfull !")
        except Exception as e:
            console.rule(f"[bold red]{e}")
            raise e

    def create_index(self, collection, tags: List[str]):
        key_list = [(tag, ASCENDING) for tag in tags]

        index_exists = False
        for idx in collection.list_indexes():
            if list(idx["key"].items()) == key_list:
                index_exists = True
                break

        if not index_exists:
            collection.create_index(key_list, unique=True)

    def get_collection(self, collection_name: str):
        return self.db[collection_name]

    def insert(self, collection, data):
        # for record in data:

        log_str = ""
        keys = [
            "symbol",
            "slug",
            "date",
            "toYr",
            "created_at",
            "an_dt",
            "consolidated",
            "desc",
        ]
        for key in keys:
            if key in data.keys():
                log_str += f" {data[key]}"

        try:
            collection.insert_one(data)
            logging.info(f"Inserted {log_str}")
        except Exception:
            logging.warning(f"Ignored duplicate: {log_str}")

    def create(
        self,
        collection,
        documents: Union[Dict[str, Any], List[Dict[str, Any]]],
        unique_cols: List[str] = None,
    ):
        if isinstance(documents, dict):
            documents = [documents]

        if not isinstance(documents, list):
            raise ValueError("documents must be a dict or list of dicts")

        if unique_cols:
            index_keys = [(col, ASCENDING) for col in unique_cols]
            collection.create_index(index_keys, unique=True)

        query_filters = ["symbol", "period", "exchange", "date"]
        operations = []
        for doc in documents:
            # Build the query that defines a "duplicate"
            filter_query = {k: doc[k] for k in query_filters if k in doc}

            # Add the upsert operation
            operations.append(
                UpdateOne(
                    filter_query,
                    {"$set": doc},  # update existing or insert new
                    upsert=True,
                )
            )

        if not operations:
            return {"inserted_count": 0, "updated_count": 0}

        # Execute all upserts in bulk
        result = collection.bulk_write(operations, ordered=False)
        return {
            "inserted_count": result.upserted_count,
            "updated_count": result.modified_count,
            "matched_count": result.matched_count,
        }

    def read(
        self,
        collection,
        query: Dict[str, Any] = {},
        projection: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """projections let you choose specific columns"""
        return list(collection.find(query, projection))

    def update(
        self, collection, query: Dict[str, Any], update_values: Dict[str, Any]
    ) -> int:
        result = collection.update_many(query, {"$set": update_values})
        return result.modified_count

    def delete(self, collection, query: Dict[str, Any]) -> int:
        result = collection.delete_many(query)
        return result.deleted_count


# Example usage:
if __name__ == "__main__":
    URI = "your_mongodb_atlas_connection_string"
    DB_NAME = "testdb"
    COLLECTION = "users"

    mongo = MongoDBHandler(URI, DB_NAME, COLLECTION)

    # Create
    user_id = mongo.create({"name": "Alice", "age": 25})
    print("Inserted ID:", user_id)

    # Read
    users = mongo.read({"name": "Alice"})
    print("Users:", users)

    # Update
    updated = mongo.update({"name": "Alice"}, {"age": 26})
    print("Documents updated:", updated)

    # Delete
    deleted = mongo.delete({"name": "Alice"})
    print("Documents deleted:", deleted)
