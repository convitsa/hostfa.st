import httpx
from typing import Any

from app.config import Settings


class EmailService:
    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.resend_api_key
        self.base_url = "https://api.resend.com"

    async def send_welcome_email(self, email: str) -> dict[str, Any]:
        message = {
            "from": "no-reply@hostfa.st",
            "to": [email],
            "subject": "Welcome to Hostfa.st!",
            "html": "<p>Welcome to Hostfa.st! We are excited to help you host your Framer websites with ease.</p>",
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/emails",
                json=message,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
            response.raise_for_status()
            return response.json()

    async def send_password_reset(self, email: str, reset_url: str) -> dict[str, Any]:
        message = {
            "from": "no-reply@hostfa.st",
            "to": [email],
            "subject": "Reset your Hostfa.st password",
            "html": f"<p>Reset your password: <a href=\"{reset_url}\">Reset password</a></p>",
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/emails",
                json=message,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
            response.raise_for_status()
            return response.json()

    async def send_deployment_success(self, email: str, project_name: str) -> dict[str, Any]:
        message = {
            "from": "no-reply@hostfa.st",
            "to": [email],
            "subject": f"Deployment Successful: {project_name}",
            "html": f"<p>Your site {project_name} has been successfully deployed!</p>",
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/emails",
                json=message,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
            response.raise_for_status()
            return response.json()

    async def send_deployment_error(self, email: str, project_name: str, error: str) -> dict[str, Any]:
        message = {
            "from": "no-reply@hostfa.st",
            "to": [email],
            "subject": f"Deployment Failed: {project_name}",
            "html": f"<p>Your site {project_name} failed to deploy.</p><p>Error: {error}</p>",
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/emails",
                json=message,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
            response.raise_for_status()
            return response.json()

    async def send_payment_failed(self, email: str) -> dict[str, Any]:
        message = {
            "from": "no-reply@hostfa.st",
            "to": [email],
            "subject": "Payment Failed",
            "html": "<p>Your recent payment failed. Please update your billing info.</p>",
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/emails",
                json=message,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
            response.raise_for_status()
            return response.json()
