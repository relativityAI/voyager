from pypdf import PdfReader
import requests
import io

def extract_text_from_pdf(response: requests.Response) -> str:
    if not response or response.status_code != 200:
        raise Exception(f"Failed to download PDF: {getattr(response, 'status_code', 'No Response')}")

    try:
        pdf_stream = io.BytesIO(response.content)
        reader = PdfReader(pdf_stream)
    except Exception as e:
        raise Exception(f"Error reading PDF: {e}")

    all_text = []
    for page in getattr(reader, "pages", []):
        try:
            all_text.append(page.extract_text() or "")
        except Exception:
            # skip problematic pages
            continue

    return "\n\n".join(all_text).strip()

