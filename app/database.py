from typing import Optional

from pymongo import AsyncMongoClient
from beanie import init_beanie

from app.config import Settings
from app.models.user import User
from app.models.site import Site
from app.models.deployment import Deployment
from app.models.billing_event import BillingEvent

client: Optional[AsyncMongoClient] = None


async def init_db(settings: Settings) -> None:
    global client
    client = AsyncMongoClient(settings.mongodb_url)
    database = client[settings.mongodb_db_name]
    await init_beanie(database=database, document_models=[User, Site, Deployment, BillingEvent])


async def close_db() -> None:
    global client
    if client is not None:
        client.close()
        client = None
