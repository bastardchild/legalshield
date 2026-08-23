"""
Upload validation (KNOWN_ISSUES #16).

The previous checks trusted the filename extension and then buffered the whole body
before looking at its size. Three consequences:

* `.txt` never failed to decode, because the latin-1 fallback maps every byte, so an
  executable renamed to `.txt` became "contract text" full of mojibake.
* A PDF renamed to `.txt` skipped extraction entirely.
* A 2 GB upload was fully read into memory before the size check ran.

The MVP accepts PDFs only. Validation here is content-first: the declared type and
extension must agree with what the bytes actually are, the body is capped at 5 MB
(`MAX_UPLOAD_BYTES`), and the page count is capped separately in
`validate_pdf_page_count`.
"""
import logging

from app.config import get_settings
from app.services.pdf_extractor import pdf_page_count

logger = logging.getLogger(__name__)

# Capped at 5 MB by default (config `max_upload_mb`); the limit is read once at import
# so the three enforcement points below share one value.
MAX_UPLOAD_BYTES = get_settings().max_upload_mb * 1024 * 1024
MAX_UPLOAD_MB = MAX_UPLOAD_BYTES // (1024 * 1024)

ALLOWED_EXTENSIONS = {".pdf"}
# Browsers are inconsistent: most report application/pdf, some application/octet-stream,
# and a few send nothing at all.
ALLOWED_MIMETYPES = {
    "application/pdf",
    "application/x-pdf",
    "application/octet-stream",
    "",
}

PDF_MAGIC = b"%PDF-"
# Chunk read while streaming, sized so a small file needs one pass.
READ_CHUNK = 64 * 1024


class UploadValidationError(ValueError):
    """Rejected upload. `status_code` mirrors the HTTP response the route should send."""

    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def extension_of(filename: str) -> str:
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()


def validate_filename(filename: str | None) -> str:
    if not filename or not filename.strip():
        raise UploadValidationError("Nama file tidak ada.")
    ext = extension_of(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadValidationError("Hanya file PDF yang diterima.")
    return ext


def validate_content_type(content_type: str | None) -> None:
    """
    Reject an obviously wrong declared type.

    Deliberately permissive — `application/octet-stream` is allowed because browsers send
    it for legitimate uploads. The magic-byte check below is what actually decides.
    """
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared not in ALLOWED_MIMETYPES:
        raise UploadValidationError(f"Tipe konten '{declared}' tidak didukung.")


def validate_declared_size(content_length: str | int | None) -> None:
    """Reject oversized uploads from the header, before buffering anything."""
    if content_length in (None, ""):
        return
    try:
        size = int(content_length)
    except (TypeError, ValueError):
        return
    if size > MAX_UPLOAD_BYTES:
        raise UploadValidationError(
            f"File terlalu besar. Maksimal {MAX_UPLOAD_MB} MB.", status_code=413
        )


def looks_like_pdf(data: bytes) -> bool:
    """PDFs may carry leading junk; the spec allows the header within the first 1 KB."""
    return PDF_MAGIC in data[:1024]


def validate_payload(data: bytes) -> None:
    """
    Reject an empty or oversized body and anything that is not really a PDF.

    The magic-byte check is the final arbiter: a non-PDF renamed to `.pdf` is refused
    here rather than reaching pypdf.
    """
    if not data:
        raise UploadValidationError("File kosong.", status_code=422)
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadValidationError(
            f"File terlalu besar. Maksimal {MAX_UPLOAD_MB} MB.", status_code=413
        )
    if not looks_like_pdf(data):
        raise UploadValidationError(
            "File tidak dikenali sebagai PDF yang valid (header %PDF- tidak ditemukan).",
            status_code=422,
        )


def validate_pdf_page_count(file_bytes: bytes, max_pages: int) -> int:
    """
    Reject a PDF that is longer than the page cap, before any text extraction.

    Extraction cost and LLM token spend grow with page count, so the check runs early.
    Returns the page count; a `max_pages` of 0 or less lifts the cap.
    """
    try:
        count = pdf_page_count(file_bytes)
    except ValueError as e:
        raise UploadValidationError(str(e), status_code=422) from e
    if max_pages > 0 and count > max_pages:
        raise UploadValidationError(
            f"File PDF memiliki {count} halaman. Maksimal {max_pages} halaman "
            "untuk dianalisis.",
            status_code=422,
        )
    return count


async def read_limited(file, limit: int = MAX_UPLOAD_BYTES) -> bytes:
    """
    Read at most `limit` bytes, then one more to detect overflow.

    Streaming in chunks means an oversized upload is rejected after the size cap is
    buffered rather than after the client finishes sending however much it wanted to.
    """
    buf = bytearray()
    while True:
        chunk = await file.read(READ_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limit:
            raise UploadValidationError(
                f"File terlalu besar. Maksimal {MAX_UPLOAD_MB} MB.", status_code=413
            )
    return bytes(buf)
