from datetime import datetime
from typing import Literal, Optional

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field


class Deployment(Document):
    site_id: PydanticObjectId = Indexed()
    trigger: Literal["manual", "auto_poll", "webhook", "initial"]
    status: Literal["queued", "crawling", "uploading", "live", "failed"] = "queued"
    cf_deployment_id: Optional[str] = None
    blob_container: Optional[str] = None
    pages_crawled: int = 0
    assets_count: int = 0
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "deployments"
