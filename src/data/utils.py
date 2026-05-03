from pypdf import PdfReader
import requests
import io

def get_exchange_from_url(url: str):
	
	if "nseindia" in url or "nsearchive" in url:
		return "nse"
	elif "bseindia" in url:
		return "bse"
	else:
		return None

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


# LLM Extraction

def extract_text_with_gemini(pdf_response: requests.Response, api_key: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=api_key)

    uploaded_file = genai.upload_file(
        pdf_response.content,
        mime_type="application/pdf"
    )

    model = genai.GenerativeModel("gemini-2.0-flash")

    prompt = "Extract and return all readable text content, as it is, from this PDF."
    result = model.generate_content([uploaded_file, prompt])

    return result.text

