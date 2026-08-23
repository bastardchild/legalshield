import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnalysisResult, Contract
from app.db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("upload.html", {"request": request})


@router.get("/contracts/{contract_id}", response_class=HTMLResponse)
async def contract_page(request: Request, contract_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Contract).where(Contract.id == contract_id))
    contract = result.scalar_one_or_none()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    return templates.TemplateResponse("result.html", {
        "request": request,
        "contract": contract,
        "contract_id": contract_id,
    })


@router.get("/partials/{contract_id}/status", response_class=HTMLResponse)
async def partial_status(request: Request, contract_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Contract).where(Contract.id == contract_id))
    contract = result.scalar_one_or_none()
    if not contract:
        return HTMLResponse("<p>Contract not found.</p>", status_code=404)
    return templates.TemplateResponse("partials/status.html", {
        "request": request,
        "contract": contract,
        "contract_id": contract_id,
    })


@router.get("/partials/{contract_id}/result", response_class=HTMLResponse)
async def partial_result(request: Request, contract_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Contract).where(Contract.id == contract_id))
    contract = result.scalar_one_or_none()
    if not contract:
        return HTMLResponse("<p>Contract not found.</p>", status_code=404)

    ar_result = await db.execute(
        select(AnalysisResult).where(AnalysisResult.contract_id == contract_id)
    )
    analysis_rows = ar_result.scalars().all()
    analysis = {r.agent_type.value: r for r in analysis_rows}

    return templates.TemplateResponse("partials/result.html", {
        "request": request,
        "contract": contract,
        "contract_id": contract_id,
        "analysis": analysis,
    })
