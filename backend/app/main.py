import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes_analysis import router as analysis_router
from app.api.routes_health import router as health_router
from app.api.routes_negotiate import router as negotiate_router
from app.api.routes_pages import router as pages_router
from app.api.routes_upload import router as upload_router
from app.config import get_settings
from app.db.session import dispose_engine
from app.middleware import AccessGateMiddleware, OwnerMiddleware, RateLimitMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is owned by Alembic (`alembic upgrade head`, run by the container command).
    # metadata.create_all used to run here, which masked migration failures and never
    # created the indexes or constraints declared in __table_args__.
    if not settings.access_token:
        logger.warning(
            "ACCESS_TOKEN is not set: the app is reachable by anyone who can connect. "
            "Contracts are still scoped to their uploader's cookie, but uploads are open."
        )
    yield
    await dispose_engine()


app = FastAPI(
    title="LegalShield Agent",
    description="Autonomous contract analysis platform for freelancers & startups",
    version="0.1.0",
    lifespan=lifespan,
)

# Starlette runs middleware in reverse registration order, so these are added
# inside-out: OwnerMiddleware first here means it runs last at request time.
# Intended order per request: rate limit -> access gate -> owner identity.
app.add_middleware(OwnerMiddleware)
app.add_middleware(AccessGateMiddleware)
app.add_middleware(RateLimitMiddleware)

# No CORS middleware at all unless origins are configured: the UI is same-origin, so the
# secure default is for browsers to refuse cross-origin reads. `allow_credentials` with a
# wildcard is rejected by browsers anyway, so the list must be explicit.
_origins = settings.cors_origins()
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Access-Token", "HX-Request", "HX-Current-URL"],
    )
    logger.info(f"CORS enabled for: {', '.join(_origins)}")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(health_router)
app.include_router(pages_router)
app.include_router(upload_router, prefix="/api")
app.include_router(analysis_router, prefix="/api")
app.include_router(negotiate_router, prefix="/api")
