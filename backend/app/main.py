"""FastAPI application factory for the `app` process (REST + WebRTC signaling/media, ADR-0001)."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.auth.router import router as auth_router
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    from app.voice import registry

    await registry.close_all()  # live WebRTC sessions end with the process
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    docs = settings.env != "prod"  # no OpenAPI surface in production (consolidation T25)
    app = FastAPI(title="AI Hiring Platform", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/docs" if docs else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if docs else None)
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    for router in ROUTERS:
        app.include_router(router)

    @app.get("/api/healthz", tags=["common"])
    async def healthz() -> dict:
        return {"status": "ok"}

    return app


from app.applications.router import router as candidate_applications_router  # noqa: E402
from app.consent.router import router as consent_router  # noqa: E402
from app.interview.router import router as interview_router  # noqa: E402
from app.interview.system import router as system_router  # noqa: E402
from app.postings.router import public_router as postings_public_router  # noqa: E402
from app.postings.router import recruiter_router as postings_recruiter_router  # noqa: E402
from app.recruiter.router import router as recruiter_router  # noqa: E402
from app.resumes.router import router as resumes_router  # noqa: E402
from app.voice.router import router as voice_router  # noqa: E402

ROUTERS = [auth_router, postings_public_router, postings_recruiter_router, consent_router, resumes_router,
           candidate_applications_router, recruiter_router, interview_router, voice_router, system_router]

app = create_app()
