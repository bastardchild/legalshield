"""
Upload validation (KNOWN_ISSUES #16).

The previous checks trusted the filename extension and then buffered the whole body
before looking at its size. Three consequences:

* `.txt` never failed to decode, because the latin-1 fallback maps every byte, so an
  executable renamed to `.txt` became "contract text" full of mojibake.
* A PDF renamed to `.txt` skipped extraction entirely.
* A 2 GB upload was fully read into memory before the size check ran.

Validation here is content-first: the declared type and extension must agree with what
the bytes actually are.
"""
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)

# Capped at 5 MB by default (config `max_upload_mb`); the limit is read once at import
# so the three enforcement points below share one value.
MAX_UPLOAD_BYTES = get_settings().max_upload_mb * 1024 * 1024
MAX_UPLOAD_MB = MAX_UPLOAD_BYTES // (1024 * 1024)

ALLOWED_EXTENSIONS = {".pdf", ".txt"}
# Browsers are inconsistent: Windows reports text/plain for .txt, some report
# application/octet-stream for both, and a few send nothing at all.
ALLOWED_MIMETYPES = {
    "application/pdf",
    "application/x-pdf",
    "text/plain",
    "text/markdown",
    "application/octet-stream",
    "",
}

PDF_MAGIC = b"%PDF-"
# Chunk read while streaming, sized so a small text file needs one pass.
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
        raise UploadValidationError("Hanya file PDF atau TXT yang diterima.")
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


def is_probably_binary(data: bytes) -> bool:
    """
    Heuristic for "this is not text".

    A NUL byte is decisive: no valid UTF-8 or latin-1 document contains one. Beyond that,
    a high ratio of non-printable bytes means the latin-1 fallback would produce garbage
    that we would then bill an LLM to read.
    """
    sample = data[:8192]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    printable = sum(1 for b in sample if b in (9, 10, 13) or 32 <= b <= 126 or b >= 160)
    return (printable / len(sample)) < 0.85


def decode_text(data: bytes) -> str:
    """
    Decode a .txt upload, rejecting binary masquerading as text.

    UTF-8 first, then latin-1 for legacy Indonesian documents. latin-1 cannot fail, so the
    binary check must happen before it, not as an except branch.
    """
    if is_probably_binary(data):
        raise UploadValidationError(
            "File ini terlihat seperti data biner, bukan teks.", status_code=422
        )
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        logger.info("Upload is not valid UTF-8; falling back to latin-1.")
        return data.decode("latin-1")


def validate_payload(ext: str, data: bytes) -> str:
    """
    Cross-check the bytes against the declared extension. Returns the effective extension.

    A PDF uploaded as `.txt` is accepted and routed to the PDF extractor rather than
    rejected — the content is what matters, and the alternative is a page of binary noise.
    """
    if not data:
        raise UploadValidationError("File kosong.", status_code=422)
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadValidationError(
            f"File terlalu besar. Maksimal {MAX_UPLOAD_MB} MB.", status_code=413
        )

    if looks_like_pdf(data):
        if ext != ".pdf":
            logger.info("Upload declared %s but has a PDF header; treating it as PDF.", ext)
        return ".pdf"

    if ext == ".pdf":
        raise UploadValidationError(
            "File tidak dikenali sebagai PDF yang valid (header %PDF- tidak ditemukan).",
            status_code=422,
        )
    return ".txt"


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
