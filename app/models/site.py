from datetime import datetime
from typing import Literal, Optional

from beanie import Document, Indexed
from pydantic import Field
from beanie import PydanticObjectId


class Site(Document):
    user_id: PydanticObjectId = Indexed()
    framer_url: str
    custom_domain: str
    cf_project_name: Optional[str] = None
    cf_project_id: Optional[str] = None
    status: Literal["pending", "live", "active", "deploying", "error", "paused"] = "pending"
    last_etag: Optional[str] = None
    last_modified: Optional[str] = None
    last_polled_at: Optional[datetime] = None
    last_deployed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "sites"
