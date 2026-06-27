import logging
import stripe
from typing import Any, Optional
from fastapi import HTTPException

from app.config import get_settings
from app.models.user import User

logger = logging.getLogger(__name__)

class StripeService:
    def __init__(self) -> None:
        self.settings = get_settings()
        stripe.api_key = self.settings.stripe_secret_key

    async def get_or_create_customer(self, user: User) -> str:
        try:
            if user.stripe_customer_id:
                try:
                    await stripe.Customer.retrieve_async(user.stripe_customer_id)
                    return user.stripe_customer_id
                except stripe.error.InvalidRequestError as e:
                    if "No such customer" in str(e):
                        user.stripe_customer_id = None
                        await user.save()
                    else:
                        raise e

            customer = await stripe.Customer.create_async(
                email=user.email,
                metadata={"user_id": str(user.id)}
            )
            user.stripe_customer_id = customer.id
            await user.save()
            return customer.id
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Stripe error: {e}")
            raise HTTPException(status_code=500, detail="Payment service error")

    async def create_checkout_session(self, user: User, plan: str, interval: str) -> str:
        try:
            customer_id = await self.get_or_create_customer(user)
            price_id = self._get_price_id(plan, interval)

            session = await stripe.checkout.Session.create_async(
                customer=customer_id,
                mode="subscription",
                allow_promotion_codes=True,
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=f"{self.settings.frontend_url}/onboarding?checkout=success&plan={plan}",
                cancel_url=f"{self.settings.frontend_url}/onboarding?checkout=cancelled",
            )
            return str(session.url)
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Stripe error: {e}")
            raise HTTPException(status_code=500, detail="Payment service error")

    async def create_portal_session(self, user: User) -> str:
        try:
            customer_id = await self.get_or_create_customer(user)
            session = await stripe.billing_portal.Session.create_async(
                customer=customer_id,
                return_url=f"{self.settings.frontend_url}/billing",
            )
            return str(session.url)
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Stripe error: {e}")
            raise HTTPException(status_code=500, detail="Payment service error")

    async def get_subscription_details(self, user: User) -> dict:
        """Fetch real subscription details from Stripe."""
        if not user.stripe_subscription_id:
            return {"status": "inactive", "current_period_end": None, "interval": None, "cancel_at_period_end": False}

        try:
            try:
                sub = await stripe.Subscription.retrieve_async(
                    user.stripe_subscription_id,
                    expand=["items.data.price"]
                )
            except stripe.error.InvalidRequestError:
                return {"status": "inactive", "current_period_end": None, "interval": None, "cancel_at_period_end": False}

            interval = None
            try:
                interval = sub.items.data[0].price.recurring.interval
            except (IndexError, AttributeError):
                pass

            return {
                "status": getattr(sub, "status", "inactive"),
                "current_period_end": getattr(sub, "current_period_end", None),
                "interval": interval,
                "cancel_at_period_end": getattr(sub, "cancel_at_period_end", False),
            }
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Stripe error: {e}")
            raise HTTPException(status_code=500, detail="Payment service error")

    async def get_invoices(self, user: User, limit: int = 20) -> list:
        """Fetch real invoices from Stripe."""
        if not user.stripe_customer_id:
            return []

        try:
            try:
                invoices = await stripe.Invoice.list_async(
                    customer=user.stripe_customer_id,
                    limit=limit,
                )
            except stripe.error.InvalidRequestError:
                return []

            result = []
            invoice_data = invoices.data if hasattr(invoices, "data") else []
            for inv in invoice_data:
                result.append({
                    "id": getattr(inv, "id", None),
                    "date": getattr(inv, "created", None),          # Unix timestamp
                    "amount": getattr(inv, "amount_paid", 0),       # In cents
                    "currency": getattr(inv, "currency", "usd"),
                    "status": getattr(inv, "status", None),         # "paid", "open", "void", "uncollectible"
                    "pdf_url": getattr(inv, "invoice_pdf", None),   # Real PDF URL
                    "hosted_url": getattr(inv, "hosted_invoice_url", None),
                })
            return result
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Stripe error: {e}")
            raise HTTPException(status_code=500, detail="Payment service error")

    async def get_payment_method(self, user: User) -> Optional[dict]:
        """Fetch the default payment method for the customer."""
        if not user.stripe_customer_id:
            return None

        try:
            try:
                customer = await stripe.Customer.retrieve_async(
                    user.stripe_customer_id,
                    expand=["invoice_settings.default_payment_method"]
                )
                
                try:
                    pm = customer.invoice_settings.default_payment_method
                except AttributeError:
                    pm = None

                if not pm:
                    # Fall back to listing payment methods
                    pms = await stripe.PaymentMethod.list_async(
                        customer=user.stripe_customer_id,
                        type="card",
                    )
                    pm_list = pms.data if hasattr(pms, "data") else []
                    if not pm_list:
                        return None
                    pm = pm_list[0]

                if isinstance(pm, str):
                    pm = await stripe.PaymentMethod.retrieve_async(pm)

                brand = "card"
                last4 = "****"
                exp_month = None
                exp_year = None
                try:
                    if pm and hasattr(pm, "card") and pm.card is not None:
                        brand = getattr(pm.card, "brand", "card")
                        last4 = getattr(pm.card, "last4", "****")
                        exp_month = getattr(pm.card, "exp_month", None)
                        exp_year = getattr(pm.card, "exp_year", None)
                except AttributeError:
                    pass

                return {
                    "brand": brand,
                    "last4": last4,
                    "exp_month": exp_month,
                    "exp_year": exp_year,
                }
            except stripe.error.InvalidRequestError:
                return None
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Stripe error: {e}")
            raise HTTPException(status_code=500, detail="Payment service error")

    def _get_price_id(self, plan: str, interval: str) -> str:
        plan_map = {
            "starter": {
                "monthly": self.settings.stripe_starter_monthly,
                "annual": self.settings.stripe_starter_annual,
            },
            "pro": {
                "monthly": self.settings.stripe_pro_monthly,
                "annual": self.settings.stripe_pro_annual,
            },
            "scale": {
                "monthly": self.settings.stripe_scale_monthly,
                "annual": self.settings.stripe_scale_annual,
            },
        }
        return plan_map.get(plan, {}).get(interval, plan_map["starter"]["monthly"])

    def construct_webhook_event(self, payload: bytes, sig_header: str) -> Any:
        return stripe.Webhook.construct_event(payload, sig_header, self.settings.stripe_webhook_secret)
