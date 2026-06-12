"""Document parser for extracting text from various formats."""
import logging
from typing import Optional, Union


logger = logging.getLogger(__name__)


def parse_text(content: str) -> str:
    """Parse plain text."""
    return content.strip()


def parse_pdf(file_bytes: bytes) -> str:
    """Parse PDF using PyMuPDF."""
    try:
        import fitz
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text()
        return text.strip()
    except Exception as e:
        logger.error(f"PDF parsing error: {e}")
        raise


def parse_html(content: str) -> str:
    """Parse HTML using BeautifulSoup."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(content, 'html.parser')
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return " ".join(text.split())
    except Exception as e:
        logger.error(f"HTML parsing error: {e}")
        raise


def parse_document(content: Union[bytes, str], file_type: str) -> str:
    """Parse document based on file type."""
    if file_type.lower() == "pdf":
        return parse_pdf(content if isinstance(content, bytes) else content.encode())
    elif file_type.lower() in ["html", "htm"]:
        return parse_html(content if isinstance(content, str) else content.decode())
    else:
        return parse_text(content if isinstance(content, str) else content.decode())
