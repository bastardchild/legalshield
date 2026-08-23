import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Contract, ContractStatus
from app.db.session import get_db
from app.middleware import client_key, current_owner
from app.schemas import ContractUploadResponse
from app.services.contract_filter import assess_contract
from app.services.pdf_extractor import extract_text_from_pdf
from app.services.queue import EnqueueError, enqueue_analysis
from app.services.rate_limit import RateLimitExceeded, check
from app.services.upload_validation import (
    UploadValidationError,
    decode_text,
    read_limited,
    validate_content_type,
    validate_declared_size,
    validate_filename,
    validate_payload,
    validate_pdf_page_count,
)

logger = logging.getLogger(__name__)
router = APIRouter()

ENQUEUE_FAILED_MESSAGE = (
    "Antrean analisis tidak dapat dihubungi saat unggah. Silakan coba lagi beberapa saat lagi."
)
UPLOAD_LIMIT_MESSAGE = (
    "Batas unggahan tercapai. Setiap unggahan menjalankan tiga agen LLM, jadi jumlahnya "
    "dibatasi per jam. Coba lagi nanti."
)
NOT_CONTRACT_MESSAGE = (
    "File ini tidak dikenali sebagai dokumen kontrak/hukum. Unggah kontrak PDF atau TXT "
    "yang berisi perjanjian, pasal, dan klausul."
)
TOO_SHORT_MESSAGE = "Teks yang diekstrak terlalu pendek untuk dianalisis sebagai kontrak."


def _enforce_upload_limit(request: Request) -> None:
    """
    Hourly upload cap, on top of the global per-minute request limit.

    Uploads are the only endpoint that spends money: each one runs three LLM agents. The
    general limit is far too generous to protect that, so uploads get their own window.
    """
    limit = get_settings().rate_limit_uploads_per_hour
    try:
        check(f"upload:{client_key(request)}", limit, 3600)
    except RateLimitExceeded as e:
        logger.warning(f"[RateLimit] {client_key(request)} exceeded {e.limit} uploads/hour")
        raise HTTPException(
            status_code=429,
            detail=UPLOAD_LIMIT_MESSAGE,
            headers={"Retry-After": str(e.retry_after)},
        ) from e


@router.post("/contracts/upload", response_model=ContractUploadResponse)
async def upload_contract(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    _enforce_upload_limit(request)
    settings = get_settings()

    try:
        ext = validate_filename(file.filename)
        validate_content_type(file.content_type)
        # Checked from the header first so an oversized body is refused before buffering.
        validate_declared_size(request.headers.get("content-length"))
        file_bytes = await read_limited(file)
        ext = validate_payload(ext, file_bytes)

        if ext == ".pdf":
            # Page cap before extraction: extraction and the LLM spend grow with pages.
            validate_pdf_page_count(file_bytes, settings.max_pdf_pages)
            try:
                raw_text = extract_text_from_pdf(file_bytes)
            except ValueError as e:
                raise UploadValidationError(str(e), status_code=422) from e
        else:
            raw_text = decode_text(file_bytes)
    except UploadValidationError as e:
        logger.info(f"Rejected upload '{file.filename}': {e.detail}")
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e

    if not raw_text.strip():
        raise HTTPException(
            status_code=422,
            detail="Tidak ada teks yang bisa diekstrak dari file. Apakah ini hasil scan gambar?",
        )

    # Gate: is this actually a contract / legal paper? Every accepted upload runs three
    # LLM agents, so junk (CVs, invoices, recipes) must be rejected before any queueing.
    # The filter fails open when the lexicon is unavailable — see contract_filter.py.
    assessment = assess_contract(raw_text)
    if settings.contract_filter_enabled and not assessment.is_contract:
        logger.info(
            f"Rejected upload '{file.filename}': {assessment.reason} "
            f"(score {assessment.score:.2f}, {assessment.token_count} tokens)"
        )
        detail = (
            TOO_SHORT_MESSAGE if assessment.reason == "too_short" else NOT_CONTRACT_MESSAGE
        )
        raise HTTPException(status_code=422, detail=detail)

    contract = Contract(
        id=uuid.uuid4(),
        # Stamped at creation: this is what every later read is filtered by.
        owner_id=current_owner(request),
        filename=file.filename,
        raw_text=raw_text,
        status=ContractStatus.uploaded,
    )
    db.add(contract)
    await db.commit()
    await db.refresh(contract)

    # The row is already committed, so a dead Redis would otherwise strand this contract
    # in `uploaded` and the front-end would poll it forever (KNOWN_ISSUES #9).
    try:
        contract.job_id = enqueue_analysis(str(contract.id))
    except EnqueueError as e:
        contract.status = ContractStatus.failed
        contract.error = ENQUEUE_FAILED_MESSAGE
        await db.commit()
        logger.error(f"Contract {contract.id} marked failed: enqueue error: {e}")
        raise HTTPException(status_code=503, detail=ENQUEUE_FAILED_MESSAGE) from e

    await db.commit()
    logger.info(f"Contract {contract.id} uploaded ({ext}), job {contract.job_id} enqueued.")

    return ContractUploadResponse(
        id=str(contract.id),
        filename=contract.filename,
        status=contract.status.value,
    )
