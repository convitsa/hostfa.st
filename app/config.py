from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    mongodb_url: str
    mongodb_db_name: str = "hostfast_db"
    redis_url: str = "redis://redis:6379/0"
    secret_key: str

    azure_storage_connection_string: str
    azure_storage_container: str

    cf_api_token: str
    cf_account_id: str

    stripe_secret_key: str
    stripe_webhook_secret: str
    stripe_starter_monthly: str = "price_1TknJpLOHS12DKyrsVhBph61"
    stripe_starter_annual: str = "price_1TknJtLOHS12DKyr5gfm36j1"
    stripe_pro_monthly: str = "price_1TknJxLOHS12DKyrAKOJs59X"
    stripe_pro_annual: str = "price_1TknK0LOHS12DKyrVDVAX61i"
    stripe_scale_monthly: str = "price_1TknK3LOHS12DKyrVjn8Lypn"
    stripe_scale_annual: str = "price_1TknK6LOHS12DKyrIO6kz6Cc"
    resend_api_key: str
    frontend_url: str = "http://localhost:3000"

    pagespeed_api_key: str = ""

    cors_origins: str = "*"
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_access_token_expires_minutes: int = 60 * 24


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
