from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class DeploymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    site_id: str
    trigger: str
    status: str
    cf_deployment_id: Optional[str] = None
    blob_container: Optional[str] = None
    pages_crawled: int = 0
    assets_count: int = 0
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
