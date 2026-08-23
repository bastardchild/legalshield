import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Contract, ContractStatus
from app.db.session import get_db
from app.schemas import ContractUploadResponse
from app.services.pdf_extractor import extract_text_from_pdf
from app.services.queue import EnqueueError, enqueue_analysis
from app.services.upload_validation import (
    UploadValidationError,
    decode_text,
    read_limited,
    validate_content_type,
    validate_declared_size,
    validate_filename,
    validate_payload,
)

logger = logging.getLogger(__name__)
router = APIRouter()

ENQUEUE_FAILED_MESSAGE = (
    "Antrean analisis tidak dapat dihubungi saat unggah. Silakan coba lagi beberapa saat lagi."
)


@router.post("/contracts/upload", response_model=ContractUploadResponse)
async def upload_contract(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    try:
        ext = validate_filename(file.filename)
        validate_content_type(file.content_type)
        # Checked from the header first so an oversized body is refused before buffering.
        validate_declared_size(request.headers.get("content-length"))
        file_bytes = await read_limited(file)
        ext = validate_payload(ext, file_bytes)

        if ext == ".pdf":
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

    contract = Contract(
        id=uuid.uuid4(),
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
