import datetime
from typing import Any

from jose import JWTError, jwt  # type: ignore[import-untyped]
import bcrypt

from app.config import Settings


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(subject: str, settings: Settings) -> str:
    expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=settings.jwt_access_token_expires_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError as exc:
        raise ValueError("Could not validate credentials") from exc
