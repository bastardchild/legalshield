import uuid
import logging
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.db.models import Contract, ContractStatus
from app.services.pdf_extractor import extract_text_from_pdf
from app.services.queue import enqueue_analysis
from app.schemas import ContractUploadResponse

logger = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_EXTENSIONS = {".pdf", ".txt"}
ALLOWED_MIMETYPES = {"application/pdf", "text/plain"}


@router.post("/contracts/upload", response_model=ContractUploadResponse)
async def upload_contract(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    ext = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only PDF or TXT files are accepted.")

    file_bytes = await file.read()
    if len(file_bytes) > 20 * 1024 * 1024:  # 20 MB limit
        raise HTTPException(status_code=413, detail="File too large. Max 20 MB.")

    # Extract text
    if ext == ".pdf":
        try:
            raw_text = extract_text_from_pdf(file_bytes)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    else:
        # Plain text — decode directly
        try:
            raw_text = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = file_bytes.decode("latin-1")

    if not raw_text.strip():
        raise HTTPException(status_code=422, detail="Could not extract text from file. Is it a scanned image?")

    contract = Contract(
        id=uuid.uuid4(),
        filename=file.filename,
        raw_text=raw_text,
        status=ContractStatus.uploaded,
    )
    db.add(contract)
    await db.commit()
    await db.refresh(contract)

    # Push to Redis queue
    enqueue_analysis(str(contract.id))
    logger.info(f"Contract {contract.id} uploaded ({ext}), job enqueued.")

    return ContractUploadResponse(
        id=str(contract.id),
        filename=contract.filename,
        status=contract.status.value,
    )

