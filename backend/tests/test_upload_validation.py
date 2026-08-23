"""
Upload validation tests (KNOWN_ISSUES #16).

The original route trusted the filename extension and used a latin-1 fallback that can
never fail, so any binary renamed to `.txt` was accepted as contract text. Uploads are
now PDF-only, capped at 5 MB and 10 pages.
"""
import io

import pytest
from pypdf import PdfWriter

from app.services.upload_validation import (
    MAX_UPLOAD_BYTES,
    PDF_MAGIC,
    UploadValidationError,
    extension_of,
    looks_like_pdf,
    read_limited,
    validate_content_type,
    validate_declared_size,
    validate_filename,
    validate_payload,
    validate_pdf_page_count,
)

MINIMAL_PDF = PDF_MAGIC + b"1.4\n%%EOF\n"


def _blank_pdf(num_pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestExtensionOf:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("kontrak.pdf", ".pdf"),
            ("kontrak.PDF", ".pdf"),
            ("kontrak.txt", ".txt"),
            ("arsip.tar.gz", ".gz"),
            ("noext", ""),
            ("trailing.", "."),
        ],
    )
    def test_cases(self, name, expected):
        assert extension_of(name) == expected


class TestValidateFilename:
    @pytest.mark.parametrize("name", ["kontrak.pdf", "KONTRAK.PDF"])
    def test_accepts_allowed(self, name):
        assert validate_filename(name) == ".pdf"

    @pytest.mark.parametrize("name", [None, "", "   "])
    def test_rejects_missing(self, name):
        with pytest.raises(UploadValidationError) as exc:
            validate_filename(name)
        assert exc.value.status_code == 400

    @pytest.mark.parametrize(
        "name",
        [
            "payload.exe",
            "script.sh",
            "kontrak.docx",
            "image.png",
            "noextension",
            "kontrak.txt",
        ],
    )
    def test_rejects_disallowed(self, name):
        with pytest.raises(UploadValidationError):
            validate_filename(name)


class TestValidateContentType:
    @pytest.mark.parametrize(
        "ct",
        [
            "application/pdf",
            "application/pdf; charset=binary",
            "application/x-pdf",
            "application/octet-stream",
            "",
            None,
        ],
    )
    def test_accepts_browser_reported_types(self, ct):
        validate_content_type(ct)

    @pytest.mark.parametrize(
        "ct", ["text/plain", "text/markdown", "image/png", "application/zip", "video/mp4"]
    )
    def test_rejects_clearly_wrong_types(self, ct):
        with pytest.raises(UploadValidationError):
            validate_content_type(ct)


class TestValidateDeclaredSize:
    def test_allows_missing_header(self):
        validate_declared_size(None)
        validate_declared_size("")

    def test_allows_garbage_header(self):
        """A malformed header must not 500; the streaming read is the real guard."""
        validate_declared_size("not-a-number")

    def test_allows_within_limit(self):
        validate_declared_size(MAX_UPLOAD_BYTES)

    def test_rejects_oversize_before_buffering(self):
        with pytest.raises(UploadValidationError) as exc:
            validate_declared_size(MAX_UPLOAD_BYTES + 1)
        assert exc.value.status_code == 413


class TestMagicBytes:
    def test_detects_pdf_header(self):
        assert looks_like_pdf(MINIMAL_PDF)

    def test_detects_pdf_after_leading_junk(self):
        assert looks_like_pdf(b"\n\n" + MINIMAL_PDF)

    def test_ignores_pdf_marker_far_into_file(self):
        assert not looks_like_pdf(b"x" * 2000 + PDF_MAGIC)

    def test_plain_text_is_not_pdf(self):
        assert not looks_like_pdf(b"Perjanjian Kerja Sama")


class TestValidatePayload:
    def test_empty_file_rejected(self):
        with pytest.raises(UploadValidationError) as exc:
            validate_payload(b"")
        assert exc.value.status_code == 422

    def test_oversize_rejected(self):
        with pytest.raises(UploadValidationError) as exc:
            validate_payload(b"a" * (MAX_UPLOAD_BYTES + 1))
        assert exc.value.status_code == 413

    def test_valid_pdf_accepted(self):
        validate_payload(MINIMAL_PDF)

    def test_non_pdf_rejected(self):
        with pytest.raises(UploadValidationError) as exc:
            validate_payload(b"ini bukan pdf")
        assert exc.value.status_code == 422


class TestValidatePdfPageCount:
    def test_accepts_within_limit(self):
        assert validate_pdf_page_count(_blank_pdf(5), 10) == 5

    def test_accepts_exactly_at_limit(self):
        assert validate_pdf_page_count(_blank_pdf(10), 10) == 10

    def test_rejects_over_limit(self):
        with pytest.raises(UploadValidationError) as exc:
            validate_pdf_page_count(_blank_pdf(11), 10)
        assert exc.value.status_code == 422

    def test_zero_lifts_the_cap(self):
        assert validate_pdf_page_count(_blank_pdf(50), 0) == 50

    def test_unreadable_pdf_is_422(self):
        with pytest.raises(UploadValidationError) as exc:
            validate_pdf_page_count(b"bukan pdf", 10)
        assert exc.value.status_code == 422


class _FakeUpload:
    """Minimal stand-in for Starlette's UploadFile.read(size)."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk, self._pos = self._data[self._pos :], len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


class TestReadLimited:
    async def test_reads_whole_small_file(self):
        assert await read_limited(_FakeUpload(b"abc" * 100)) == b"abc" * 100

    async def test_reads_across_chunk_boundary(self):
        data = bytes(200_000)
        assert await read_limited(_FakeUpload(data), limit=MAX_UPLOAD_BYTES) == data

    async def test_stops_at_limit(self):
        with pytest.raises(UploadValidationError) as exc:
            await read_limited(_FakeUpload(b"a" * 5000), limit=1000)
        assert exc.value.status_code == 413

    async def test_exactly_at_limit_is_allowed(self):
        data = b"a" * 1000
        assert await read_limited(_FakeUpload(data), limit=1000) == data
