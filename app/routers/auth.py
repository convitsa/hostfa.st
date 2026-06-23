from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from beanie import PydanticObjectId

from app.config import Settings, get_settings
from app.models.user import User
from app.schemas.auth import (
    RegisterRequest,
    LoginRequest,
    TokenResponse,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from app.services.auth_service import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.services.email_service import EmailService

router = APIRouter()
security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    settings: Settings = Depends(get_settings),
) -> User:
    token = credentials.credentials
    try:
        payload = decode_access_token(token, settings)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    user = await User.get(PydanticObjectId(user_id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(request: RegisterRequest, settings: Settings = Depends(get_settings)):
    existing = await User.find_one(User.email == request.email)
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    user = User(
        email=request.email,
        password_hash=hash_password(request.password),
        plan=request.plan,
    )
    await user.insert()

    # Send welcome email using EmailService
    email_service = EmailService(settings)
    try:
        await email_service.send_welcome_email(user.email)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to send welcome email: {e}")

    token = create_access_token(str(user.id), settings)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, settings: Settings = Depends(get_settings)):
    user = await User.find_one(User.email == request.email)
    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token(str(user.id), settings)
    return TokenResponse(access_token=token)


@router.post("/logout")
async def logout() -> dict[str, str]:
    return {"message": "Logged out"}


@router.get("/me")
async def me(user: User = Depends(get_current_user)) -> dict:
    return {
        "email": user.email,
        "plan": user.plan,
        "is_active": user.is_active,
        "onboarding_complete": user.onboarding_complete,
    }


@router.patch("/me/onboarding")
async def patch_onboarding(user: User = Depends(get_current_user)) -> dict:
    user.onboarding_complete = True
    await user.save()
    return {"onboarding_complete": True}



@router.post("/forgot-password")
async def forgot_password(request: ForgotPasswordRequest, settings: Settings = Depends(get_settings)):
    user = await User.find_one(User.email == request.email)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    token = create_access_token(str(user.id), settings)
    reset_url = f"{settings.frontend_url}/reset-password?token={token}"
    email_service = EmailService(settings)
    await email_service.send_password_reset(user.email, reset_url)
    return {"message": "Password reset email sent"}


@router.post("/reset-password")
async def reset_password(request: ResetPasswordRequest, settings: Settings = Depends(get_settings)):
    try:
        payload = decode_access_token(request.token, settings)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid reset token")

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload")

    user = await User.get(PydanticObjectId(user_id))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.password_hash = hash_password(request.new_password)
    await user.save()
    return {"message": "Password reset successfully"}


@router.delete("/delete-account")
async def delete_account(user: User = Depends(get_current_user), settings: Settings = Depends(get_settings)):
    if user.stripe_subscription_id:
        try:
            import stripe
            stripe.api_key = settings.stripe_secret_key
            await stripe.Subscription.cancel_async(user.stripe_subscription_id)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Failed to cancel Stripe subscription during account deletion: {e}")

    from app.models.site import Site
    from app.models.deployment import Deployment
    from beanie.operators import In
    
    sites = await Site.find(Site.user_id == user.id).to_list()
    if sites:
        site_ids = [site.id for site in sites]
        await Deployment.find(In(Deployment.site_id, site_ids)).delete()
        await Site.find(Site.user_id == user.id).delete()

    await user.delete()
    return {"message": "Account deleted successfully"}
