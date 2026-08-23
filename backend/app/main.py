import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes_analysis import router as analysis_router
from app.api.routes_health import router as health_router
from app.api.routes_negotiate import router as negotiate_router
from app.api.routes_pages import router as pages_router
from app.api.routes_upload import router as upload_router
from app.config import get_settings
from app.db.session import dispose_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is owned by Alembic (`alembic upgrade head`, run by the container command).
    # metadata.create_all used to run here, which masked migration failures and never
    # created the indexes or constraints declared in __table_args__.
    yield
    await dispose_engine()


app = FastAPI(
    title="LegalShield Agent",
    description="Autonomous contract analysis platform for freelancers & startups",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(health_router)
app.include_router(pages_router)
app.include_router(upload_router, prefix="/api")
app.include_router(analysis_router, prefix="/api")
app.include_router(negotiate_router, prefix="/api")

