from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings
from app.database import init_db, close_db
from app.routers import auth, sites, deployments, billing, webhooks, tools


def create_app() -> FastAPI:
    settings = Settings()  # type: ignore[call-arg]
    app = FastAPI(title="hostfa.st", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def startup_event() -> None:
        await init_db(settings)

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        await close_db()

    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(sites.router, prefix="/sites", tags=["sites"])
    app.include_router(deployments.router, prefix="/deployments", tags=["deployments"])
    app.include_router(billing.router, prefix="/billing", tags=["billing"])
    app.include_router(webhooks.router, prefix="/hooks", tags=["webhooks"])
    app.include_router(tools.router, prefix="/api/v1/tools", tags=["tools"])
    app.include_router(tools.router, prefix="/tools", tags=["tools"])

    return app


app = create_app()
