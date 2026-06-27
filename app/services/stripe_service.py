import stripe
from typing import Any, Optional

from app.config import get_settings
from app.models.user import User

class StripeService:
    def __init__(self) -> None:
        self.settings = get_settings()
        stripe.api_key = self.settings.stripe_secret_key

    async def get_or_create_customer(self, user: User) -> str:
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

    async def create_checkout_session(self, user: User, plan: str, interval: str) -> str:
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

    async def create_portal_session(self, user: User) -> str:
        customer_id = await self.get_or_create_customer(user)
        session = await stripe.billing_portal.Session.create_async(
            customer=customer_id,
            return_url=f"{self.settings.frontend_url}/billing",
        )
        return str(session.url)

    async def get_subscription_details(self, user: User) -> dict:
        """Fetch real subscription details from Stripe."""
        if not user.stripe_subscription_id:
            return {"status": "inactive", "current_period_end": None, "interval": None, "cancel_at_period_end": False}

        try:
            sub = await stripe.Subscription.retrieve_async(
                user.stripe_subscription_id,
                expand=["items.data.price"]
            )
            interval = None
            if sub.get("items") and sub["items"].get("data"):
                price = sub["items"]["data"][0].get("price", {})
                interval = price.get("recurring", {}).get("interval")  # "month" or "year"

            return {
                "status": sub.get("status"),  # "active", "past_due", "canceled", etc.
                "current_period_end": sub.get("current_period_end"),  # Unix timestamp
                "interval": interval,
                "cancel_at_period_end": sub.get("cancel_at_period_end", False),
            }
        except stripe.error.InvalidRequestError:
            return {"status": "inactive", "current_period_end": None, "interval": None, "cancel_at_period_end": False}

    async def get_invoices(self, user: User, limit: int = 20) -> list:
        """Fetch real invoices from Stripe."""
        if not user.stripe_customer_id:
            return []

        try:
            invoices = await stripe.Invoice.list_async(
                customer=user.stripe_customer_id,
                limit=limit,
            )
            result = []
            for inv in invoices.get("data", []):
                result.append({
                    "id": inv.get("id"),
                    "date": inv.get("created"),          # Unix timestamp
                    "amount": inv.get("amount_paid"),    # In cents
                    "currency": inv.get("currency", "usd"),
                    "status": inv.get("status"),         # "paid", "open", "void", "uncollectible"
                    "pdf_url": inv.get("invoice_pdf"),   # Real PDF URL
                    "hosted_url": inv.get("hosted_invoice_url"),
                })
            return result
        except stripe.error.InvalidRequestError:
            return []

    async def get_payment_method(self, user: User) -> Optional[dict]:
        """Fetch the default payment method for the customer."""
        if not user.stripe_customer_id:
            return None

        try:
            customer = await stripe.Customer.retrieve_async(
                user.stripe_customer_id,
                expand=["invoice_settings.default_payment_method"]
            )
            pm = customer.get("invoice_settings", {}).get("default_payment_method")
            if not pm:
                # Fall back to listing payment methods
                pms = await stripe.PaymentMethod.list_async(
                    customer=user.stripe_customer_id,
                    type="card",
                )
                pm_list = pms.get("data", [])
                if not pm_list:
                    return None
                pm = pm_list[0]

            if isinstance(pm, str):
                # pm is just an ID string, retrieve it
                pm = await stripe.PaymentMethod.retrieve_async(pm)

            card = pm.get("card", {}) if isinstance(pm, dict) else {}
            return {
                "brand": card.get("brand", "card"),
                "last4": card.get("last4", "****"),
                "exp_month": card.get("exp_month"),
                "exp_year": card.get("exp_year"),
            }
        except Exception:
            return None

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
