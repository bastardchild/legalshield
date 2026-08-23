import io
import logging

from pypdf import PdfReader

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract plain text from PDF bytes. Returns empty string on failure."""
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            pages.append(text)
            logger.debug(f"PDF page {i+1}: {len(text)} chars extracted")
        full_text = "\n\n".join(pages).strip()
        logger.info(f"PDF extraction complete: {len(full_text)} total chars, {len(reader.pages)} pages")
        return full_text
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
        raise ValueError(f"Could not extract text from PDF: {e}")
