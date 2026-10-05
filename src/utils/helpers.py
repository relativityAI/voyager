import os
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import urlparse

import requests
from pypdf import PdfReader
from rich.console import Console

from src.utils.web import generate_fake_headers

console = Console()


def utcnow() -> datetime:
    """Naive UTC now (matches the rest of the codebase's naive datetimes)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def load_pdf(file_path: str) -> PdfReader:
    url, pdf_path = None, None
    parsed = urlparse(file_path)
    if parsed.scheme and parsed.netloc:
        url = file_path
        _, extension = os.path.splitext(url)
        if not extension or extension not in [".pdf", ".zip"]:
            console.error("URL parse error")
            return

        response = requests.get(url, headers=generate_fake_headers(), timeout=10)
        response.raise_for_status()
        data = BytesIO(response.content)

        if extension == ".pdf":
            reader = PdfReader(data)
            return reader

        elif extension == ".zip":
            import zipfile

            with zipfile.ZipFile(data) as z:
                pdf_name = next(
                    name for name in z.namelist() if name.lower().endswith(".pdf")
                )
                pdf_bytes = BytesIO(z.read(pdf_name))
                reader = PdfReader(pdf_bytes)
            return reader
    else:
        pdf_path = file_path
        reader = PdfReader(pdf_path)
        return reader


def read_pdf(path_or_url: str, start: int = None, end: int = None):
    reader = load_pdf(path_or_url)

    if not start:
        start = 0
    if not end:
        end = len(reader.pages) - 1

    full_text = """"""

    for i in range(start, end + 1):
        full_text += reader.pages[i].extract_text()
        full_text += "\n"

    return full_text
