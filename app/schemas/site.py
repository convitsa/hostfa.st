from datetime import datetime
from typing import Optional

from pydantic import BaseModel, HttpUrl, ConfigDict, Field


class SiteBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, json_schema_extra={"example": {}})


class CreateSiteRequest(BaseModel):
    framer_url: HttpUrl
    custom_domain: str = Field(
        ...,
        min_length=3,
        pattern=r"^[a-zA-Z0-9]+([\-\.]{1}[a-zA-Z0-9]+)*\.[a-zA-Z]{2,}$"
    )


class SiteResponse(SiteBase):
    id: str
    user_id: str
    framer_url: HttpUrl
    custom_domain: Optional[str]
    cf_project_name: Optional[str]
    cf_project_id: Optional[str]
    status: str
    last_etag: Optional[str]
    last_modified: Optional[str]
    last_polled_at: Optional[datetime]
    last_deployed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


from app.schemas.deployment import DeploymentResponse


class SiteDetailResponse(SiteResponse):
    deployments: list[DeploymentResponse] = []

