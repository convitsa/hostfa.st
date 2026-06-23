from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings
from app.database import init_db, close_db
from app.routers import auth, sites, deployments, billing, webhooks, tools, proxy


def create_app() -> FastAPI:
    settings = Settings()  # type: ignore[call-arg]
    
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_db(settings)
        yield
        await close_db()

    app = FastAPI(title="hostfa.st", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(sites.router, prefix="/sites", tags=["sites"])
    app.include_router(deployments.router, prefix="/deployments", tags=["deployments"])
    app.include_router(billing.router, prefix="/billing", tags=["billing"])
    app.include_router(webhooks.router, prefix="/hooks", tags=["webhooks"])
    app.include_router(tools.router, prefix="/tools", tags=["tools"])
    app.include_router(proxy.router)

    return app


app = create_app()
