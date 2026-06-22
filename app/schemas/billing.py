from typing import Literal
from pydantic import BaseModel


class CheckoutRequest(BaseModel):
    plan: Literal["starter", "pro", "scale"]
    payment_method_id: str


class PlanResponse(BaseModel):
    plan: Literal["starter", "pro", "scale"]
    price_id: str
    display_name: str
    monthly_cost_cents: int
