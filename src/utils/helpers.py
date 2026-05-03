# from pypdf import PdfReader
import pandas as pd
import numpy as np
import requests
import json
import os
import io

from datetime import datetime, timedelta, date
from urllib.parse import urlparse
from typing import Dict, Any
import hashlib

from rich.console import Console
from rich.progress import track

from src.utils.web import generate_fake_headers
from urllib.parse import urlparse
from pypdf import PdfReader
from io import BytesIO
import requests


console = Console()


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


def get_file_type_from_extension(url):
    parsed_url = urlparse(url)
    path = parsed_url.path
    _, extension = os.path.splitext(path)
    if extension:
        return extension.lower()
    return None


def is_url_urllib(url_string):
    try:
        result = urlparse(url_string)
        # Check if scheme and netloc (network location/domain) are present
        return all([result.scheme, result.netloc])
    except ValueError:
        return False


def write_json(data, file_path, indent=4):
    with open(file_path, "w") as f:
        json.dump(data, f, indent=4)


def load_json(file_path):
    with open(file_path, "r") as f:
        data = json.load(f)
        return data


def write_file(data, file_path):
    with open(file_path, "w") as f:
        f.write(data)
        f.close()


def read_file(file_path):
    with open(file_path, "r") as f:
        return f.read()


def k_days_before(date_str: str, k: int, date_format: str = "%Y-%m-%d") -> str:
    date_obj = datetime.strptime(date_str, date_format)
    new_date = date_obj - timedelta(days=k)
    return new_date.strftime(date_format)


def k_days_before_2(date_, k: int, date_format: str = "%Y-%m-%d") -> str:
    new_date = date_ - timedelta(days=k)
    return new_date.strftime(date_format)


def str_to_date(date_str: str) -> date:
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def read_json(file_path: str):
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(file_path: str, data: dict):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def get_list_size(lst):
    import sys

    size_bytes = sys.getsizeof(lst)
    size_kb = size_bytes / 1024
    size_mb = size_bytes / (1024**2)

    return {"b": size_bytes, "kb": size_kb, "mb": size_mb}


def hash_doc(doc: Dict[str, Any]) -> str:
    # ensure deterministic ordering
    doc_str = json.dumps(doc, sort_keys=True)
    return hashlib.sha256(doc_str.encode("utf-8")).hexdigest()


def get_curr_date_str(date_format="%Y-%m-%d"):
    return datetime.now().strftime(date_format)


def get_date_after_k_days(date_str: str, k: int, date_format="%Y-%m-%d"):
    date_ = datetime.strptime(date_str, date_format) + timedelta(days=k)
    return date_.strftime(date_format)


def get_date_shifted(date_str: str, k: int, date_format: str = "%Y-%m-%d") -> str:
    date_ = datetime.strptime(date_str, date_format)
    shifted_date = date_ + timedelta(days=k)
    return shifted_date.strftime(date_format)
