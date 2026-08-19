from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import logging

from app.config import get_settings
from app.db.session import engine, Base
from app.api.routes_pages import router as pages_router
from app.api.routes_upload import router as upload_router
from app.api.routes_analysis import router as analysis_router
from app.api.routes_negotiate import router as negotiate_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Import models so metadata is populated before create_all
    from app.db import models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured.")
    yield
    await engine.dispose()


app = FastAPI(
    title="LegalShield Agent",
    description="Autonomous contract analysis platform for freelancers & startups",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(pages_router)
app.include_router(upload_router, prefix="/api")
app.include_router(analysis_router, prefix="/api")
app.include_router(negotiate_router, prefix="/api")

