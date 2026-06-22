from datetime import datetime
from typing import Any, Optional

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field


class BillingEvent(Document):
    user_id: Optional[PydanticObjectId] = None
    event_type: str
    stripe_event_id: Indexed(str, unique=True)  # type: ignore[valid-type]
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "billing_events"
