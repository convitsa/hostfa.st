"""
STRIPE WEBHOOK SETUP INSTRUCTIONS
==================================

1. Go to Stripe Dashboard -> Developers -> Webhooks
2. Click "Add endpoint"
3. Endpoint URL: https://api.hostfa.st/billing/webhook
4. Select these events to listen to:
   - checkout.session.completed
   - customer.subscription.updated
   - customer.subscription.deleted
   - invoice.payment_failed
   - invoice.payment_succeeded
5. Copy the "Signing secret" (starts with whsec_)
6. Add to your .env file:
   STRIPE_WEBHOOK_SECRET=whsec_your_secret_here
7. Also add test webhook for local development:
   Use Stripe CLI: stripe listen --forward-to localhost:8000/billing/webhook
   This gives you a temporary webhook secret for local testing.

IMPORTANT: The webhook secret is different from your API key.
Make sure to use the webhook signing secret, not the API secret key.
"""

import logging
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

from app.config import get_settings
from app.routers.auth import get_current_user

logger = logging.getLogger(__name__)
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
    try:
        payload = await request.body()
        stripe_service = StripeService()
        try:
            event = stripe_service.construct_webhook_event(payload, stripe_signature)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        existing_event = await BillingEvent.find_one(BillingEvent.stripe_event_id == event["id"])
        if existing_event:
            return {"status": "already processed"}

        stripe_object = event["data"]["object"]
        payload_dict = dict(stripe_object) if hasattr(stripe_object, "__iter__") else stripe_object.to_dict() if hasattr(stripe_object, "to_dict") else {}

        billing_event = BillingEvent(
            stripe_event_id=event["id"],
            event_type=event["type"],
            payload=payload_dict
        )
        await billing_event.insert()

        settings = get_settings()

        if event["type"] == "checkout.session.completed":
            session = payload_dict
            customer_id = session.get("customer")
            subscription_id = session.get("subscription")
            user = await User.find_one(User.stripe_customer_id == customer_id)
            if user:
                user.stripe_subscription_id = subscription_id
                await user.save()

        elif event["type"] == "customer.subscription.updated":
            subscription = payload_dict
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
            subscription = payload_dict
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
            invoice = payload_dict
            customer_id = invoice.get("customer")
            user = await User.find_one(User.stripe_customer_id == customer_id)
            if user:
                email_service = EmailService(settings)
                await email_service.send_payment_failed(user.email)

        return {"status": "success"}
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        return {"status": "error", "detail": str(e)}
