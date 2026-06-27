from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator
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
    azure_storage_sites_container: str = "hostfast-sites"

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

    @model_validator(mode="after")
    def clean_redis_url(self) -> "Settings":
        url = self.redis_url
        if "ssl_cert_reqs=CERT_NONE" in url:
            self.redis_url = url.replace("ssl_cert_reqs=CERT_NONE", "ssl_cert_reqs=none")
        elif "ssl_cert_reqs=CERT_REQUIRED" in url:
            self.redis_url = url.replace("ssl_cert_reqs=CERT_REQUIRED", "ssl_cert_reqs=required")
        elif "ssl_cert_reqs=CERT_OPTIONAL" in url:
            self.redis_url = url.replace("ssl_cert_reqs=CERT_OPTIONAL", "ssl_cert_reqs=optional")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
