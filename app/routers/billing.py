from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

from app.config import get_settings
from app.routers.auth import get_current_user
from app.models.user import User
from app.models.site import Site
from app.models.billing_event import BillingEvent
from app.services.stripe_service import StripeService
from app.services.email_service import EmailService
from app.schemas.billing import CheckoutRequest

router = APIRouter()

class PortalRequest(BaseModel):
    pass

@router.get("/plans")
async def get_plans() -> Dict[str, Any]:
    return {
        "starter": {"limits": {"sites": 1}, "features": ["Basic Analytics"]},
        "pro": {"limits": {"sites": 3}, "features": ["Advanced Analytics", "Priority Support"]},
        "scale": {"limits": {"sites": 10}, "features": ["Custom Domains", "24/7 Support"]},
    }

@router.post("/checkout")
async def checkout(request: CheckoutRequest, user: User = Depends(get_current_user)) -> Dict[str, str]:
    stripe_service = StripeService()
    url = await stripe_service.create_checkout_session(user, request.plan, request.interval)
    return {"checkout_url": url}

@router.post("/portal")
async def portal(request: PortalRequest, user: User = Depends(get_current_user)) -> Dict[str, str]:
    stripe_service = StripeService()
    url = await stripe_service.create_portal_session(user)
    return {"portal_url": url}

@router.get("/subscription")
async def get_subscription(user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Return real subscription details from Stripe."""
    stripe_service = StripeService()
    details = await stripe_service.get_subscription_details(user)
    return {
        "plan": user.plan,
        **details,
    }

@router.get("/invoices")
async def get_invoices(user: User = Depends(get_current_user)) -> List[Dict[str, Any]]:
    """Return real invoices from Stripe."""
    stripe_service = StripeService()
    return await stripe_service.get_invoices(user)

@router.get("/payment-method")
async def get_payment_method(user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Return the default payment method for the customer."""
    stripe_service = StripeService()
    pm = await stripe_service.get_payment_method(user)
    if pm is None:
        return {"has_payment_method": False}
    return {"has_payment_method": True, **pm}

@router.post("/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(...)) -> Dict[str, str]:
    payload = await request.body()
    stripe_service = StripeService()
    try:
        event = stripe_service.construct_webhook_event(payload, stripe_signature)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    existing_event = await BillingEvent.find_one(BillingEvent.stripe_event_id == event["id"])
    if existing_event:
        return {"status": "already processed"}

    billing_event = BillingEvent(
        stripe_event_id=event["id"],
        event_type=event["type"],
        payload=event["data"]["object"]
    )
    await billing_event.insert()

    settings = get_settings()

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        customer_id = session.get("customer")
        subscription_id = session.get("subscription")
        user = await User.find_one(User.stripe_customer_id == customer_id)
        if user:
            user.stripe_subscription_id = subscription_id
            await user.save()

    elif event["type"] == "customer.subscription.updated":
        subscription = event["data"]["object"]
        customer_id = subscription.get("customer")
        try:
            price_id = subscription["items"]["data"][0]["price"]["id"]
        except (KeyError, IndexError):
            price_id = None
        user = await User.find_one(User.stripe_customer_id == customer_id)
        if user:
            if price_id in (settings.stripe_starter_monthly, settings.stripe_starter_annual):
                user.plan = "starter"
            elif price_id in (settings.stripe_pro_monthly, settings.stripe_pro_annual):
                user.plan = "pro"
            elif price_id in (settings.stripe_scale_monthly, settings.stripe_scale_annual):
                user.plan = "scale"
            await user.save()

    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        customer_id = subscription.get("customer")
        user = await User.find_one(User.stripe_customer_id == customer_id)
        if user:
            user.plan = "starter"
            user.stripe_subscription_id = None
            await user.save()
            sites = await Site.find(Site.user_id == user.id).to_list()
            for site in sites:
                site.status = "paused"
                await site.save()

    elif event["type"] == "invoice.payment_failed":
        invoice = event["data"]["object"]
        customer_id = invoice.get("customer")
        user = await User.find_one(User.stripe_customer_id == customer_id)
        if user:
            email_service = EmailService(settings)
            await email_service.send_payment_failed(user.email)

    return {"status": "success"}
