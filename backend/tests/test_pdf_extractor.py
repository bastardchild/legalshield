"""
PDF extraction and page-count tests.

pypdf can write blank pages without a content stream, which is enough to exercise page
counting. Extracting text needs a hand-built content stream with a Type1 font, which is
what `_text_pdf` builds.
"""
import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.services.pdf_extractor import extract_text_from_pdf, pdf_page_count


def _blank_pdf(num_pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _text_pdf(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 10 Tf 72 720 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = stream
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/Type1"),
                            NameObject("/BaseFont"): NameObject("/Helvetica"),
                        }
                    )
                }
            )
        }
    )
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestPdfPageCount:
    def test_counts_pages(self):
        assert pdf_page_count(_blank_pdf(3)) == 3

    def test_counts_single_page(self):
        assert pdf_page_count(_text_pdf("PASAL 1")) == 1

    def test_rejects_non_pdf_bytes(self):
        with pytest.raises(ValueError):
            pdf_page_count(b"ini bukan pdf")

    def test_rejects_empty_bytes(self):
        with pytest.raises(ValueError):
            pdf_page_count(b"")


class TestExtractTextFromPdf:
    def test_extracts_text(self):
        assert extract_text_from_pdf(_text_pdf("PERJANJIAN KERJA SAMA")) == "PERJANJIAN KERJA SAMA"

    def test_blank_pages_yield_empty(self):
        assert extract_text_from_pdf(_blank_pdf(2)) == ""

    def test_raises_on_garbage(self):
        with pytest.raises(ValueError):
            extract_text_from_pdf(b"not a pdf")
