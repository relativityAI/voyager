from .models import *

from beanie import init_beanie


async def init_db():
    # Create Async PyMongo client
    client = AsyncMongoClient(
        "mongodb://root:example@mongo:27017/"
    )

    # Initialize beanie with the Sample document class and a database
    await init_beanie(database=client, document_models=[Data])

