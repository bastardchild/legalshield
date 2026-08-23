import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import load_owned_contract
from app.db.models import AnalysisResult, Contract
from app.db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

NOT_FOUND_FRAGMENT = "<p>Contract not found.</p>"


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("upload.html", {"request": request})


@router.get("/contracts/{contract_id}", response_class=HTMLResponse)
async def contract_page(request: Request, contract: Contract = Depends(load_owned_contract)):
    return templates.TemplateResponse("result.html", {
        "request": request,
        "contract": contract,
        "contract_id": str(contract.id),
    })


async def _owned_or_fragment(contract_id: str, request: Request, db: AsyncSession):
    """
    Ownership check for the HTMX partials.

    The partials answer with an HTML fragment rather than a JSON error body, because htmx
    swaps whatever comes back into the page. Raising the shared dependency's HTTPException
    would put a JSON blob inside the results card.
    """
    try:
        return await load_owned_contract(contract_id, request, db)
    except HTTPException:
        return None


@router.get("/partials/{contract_id}/status", response_class=HTMLResponse)
async def partial_status(request: Request, contract_id: str, db: AsyncSession = Depends(get_db)):
    contract = await _owned_or_fragment(contract_id, request, db)
    if contract is None:
        return HTMLResponse(NOT_FOUND_FRAGMENT, status_code=404)
    return templates.TemplateResponse("partials/status.html", {
        "request": request,
        "contract": contract,
        "contract_id": contract_id,
    })


@router.get("/partials/{contract_id}/result", response_class=HTMLResponse)
async def partial_result(request: Request, contract_id: str, db: AsyncSession = Depends(get_db)):
    contract = await _owned_or_fragment(contract_id, request, db)
    if contract is None:
        return HTMLResponse(NOT_FOUND_FRAGMENT, status_code=404)

    ar_result = await db.execute(
        select(AnalysisResult).where(AnalysisResult.contract_id == contract.id)
    )
    analysis_rows = ar_result.scalars().all()
    analysis = {r.agent_type.value: r for r in analysis_rows}

    return templates.TemplateResponse("partials/result.html", {
        "request": request,
        "contract": contract,
        "contract_id": contract_id,
        "analysis": analysis,
    })
