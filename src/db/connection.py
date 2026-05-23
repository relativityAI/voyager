import os
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from .models import ScreenerData, NSEFinancials, NSEShareholdings
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

async def init_db():
    mongodb_url = os.getenv("MONGODB_URL")
    db_name = os.getenv("MONGODB_DB_NAME")
    
    if not mongodb_url:
        logger.error("MONGODB_URL not found in environment variables")
        return

    logger.info(f"Connecting to MongoDB at {mongodb_url}...")
    client = AsyncIOMotorClient(mongodb_url)
    
    # Motor 3.x attribute access returns a MotorDatabase, which Beanie tries to call.
    # We explicitly set append_metadata to something non-callable to skip Beanie's check.
    client.append_metadata = None # type: ignore 
    
    await init_beanie(
        database=client[db_name],
        document_models=[
            ScreenerData,
            NSEFinancials,
            NSEShareholdings
        ]
    )
    logger.info("Beanie initialization complete.")
