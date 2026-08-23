"""
Upload validation tests (KNOWN_ISSUES #16).

The original route trusted the filename extension and used a latin-1 fallback that can
never fail, so any binary renamed to `.txt` was accepted as contract text.
"""
import io

import pytest
from pypdf import PdfWriter

from app.services.upload_validation import (
    MAX_UPLOAD_BYTES,
    PDF_MAGIC,
    UploadValidationError,
    decode_text,
    extension_of,
    is_probably_binary,
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
    @pytest.mark.parametrize("name", ["kontrak.pdf", "kontrak.txt", "KONTRAK.PDF"])
    def test_accepts_allowed(self, name):
        assert validate_filename(name) in {".pdf", ".txt"}

    @pytest.mark.parametrize("name", [None, "", "   "])
    def test_rejects_missing(self, name):
        with pytest.raises(UploadValidationError) as exc:
            validate_filename(name)
        assert exc.value.status_code == 400

    @pytest.mark.parametrize(
        "name", ["payload.exe", "script.sh", "kontrak.docx", "image.png", "noextension"]
    )
    def test_rejects_disallowed(self, name):
        with pytest.raises(UploadValidationError):
            validate_filename(name)


class TestValidateContentType:
    @pytest.mark.parametrize(
        "ct",
        [
            "application/pdf",
            "text/plain",
            "text/plain; charset=utf-8",
            "application/octet-stream",
            "",
            None,
        ],
    )
    def test_accepts_browser_reported_types(self, ct):
        validate_content_type(ct)

    @pytest.mark.parametrize("ct", ["image/png", "application/zip", "video/mp4"])
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


class TestIsProbablyBinary:
    def test_nul_byte_is_decisive(self):
        assert is_probably_binary(b"MZ\x00\x00some exe")

    def test_utf8_indonesian_text_is_text(self):
        assert not is_probably_binary("Pasal 1 — Ruang Lingkup Pekerjaan\n".encode())

    def test_latin1_text_is_text(self):
        # "é" encodes to 0xE9, which is invalid UTF-8 on its own — the case the fallback exists for.
        assert not is_probably_binary("Pasal 1 Ketentuan Umum – Café".replace("–", "-").encode("latin-1"))

    def test_high_entropy_bytes_are_binary(self):
        assert is_probably_binary(bytes(range(1, 32)) * 100)

    def test_empty_is_not_binary(self):
        assert not is_probably_binary(b"")


class TestDecodeText:
    def test_utf8(self):
        assert decode_text("Pasal 1 — Lingkup".encode()) == "Pasal 1 — Lingkup"

    def test_latin1_fallback(self):
        out = decode_text("Pasal 1 - Café Ketentuan".encode("latin-1"))
        assert "Pasal 1" in out

    def test_binary_rejected_rather_than_mojibake(self):
        with pytest.raises(UploadValidationError) as exc:
            decode_text(b"\x7fELF\x02\x01\x01\x00" + bytes(range(64)))
        assert exc.value.status_code == 422


class TestValidatePayload:
    def test_empty_file_rejected(self):
        with pytest.raises(UploadValidationError) as exc:
            validate_payload(".txt", b"")
        assert exc.value.status_code == 422

    def test_oversize_rejected(self):
        with pytest.raises(UploadValidationError) as exc:
            validate_payload(".txt", b"a" * (MAX_UPLOAD_BYTES + 1))
        assert exc.value.status_code == 413

    def test_pdf_stays_pdf(self):
        assert validate_payload(".pdf", MINIMAL_PDF) == ".pdf"

    def test_pdf_renamed_to_txt_is_routed_to_pdf_extractor(self):
        assert validate_payload(".txt", MINIMAL_PDF) == ".pdf"

    def test_txt_stays_txt(self):
        assert validate_payload(".txt", b"Perjanjian") == ".txt"

    def test_non_pdf_claiming_pdf_extension_rejected(self):
        with pytest.raises(UploadValidationError) as exc:
            validate_payload(".pdf", b"ini bukan pdf")
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
