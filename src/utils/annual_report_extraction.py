import json
import os
import re

import pandas as pd
import pytesseract
from dotenv import load_dotenv
from google import genai
from pdf2image import convert_from_path
from rich.console import Console
from rich.progress import track

from src.utils.helpers import load_pdf

load_dotenv()

console = Console()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def extract_first_pages(path_or_url, num_pages=6):

    reader = load_pdf(path_or_url)

    if not reader:
        console.error("PDF Read got messed up")
        return

    text_pages = []
    for i in track(
        range(min(num_pages, len(reader.pages))), description="Processing Pages..."
    ):
        page = reader.pages[i]
        text = page.extract_text().strip()

        # The OCR works as a backup/ fallback here
        # Annual Report pages might have a lotta image based info
        # PyPDF may ignore that

        # if len(text) < 100:
        #     console.print(f"[yellow]Page {i+1} liekly an image. Running OCR...[/yellow]")

        #     images = None
        #     if url and data:
        #         images = convert_from_bytes(data, first_page=i+1, last_page=i+1)
        #     elif pdf_path:
        #         images = convert_from_path(pdf_path, first_page=i+1, last_page=i+1)
        #     else:
        #         console.error("OCR got messed up")
        #         return

        #     if not images:
        #         console.error("OCR got messed up")
        #         return

        #     ocr_text = pytesseract.image_to_string(images[0])
        #     text_pages.append(ocr_text)
        # else:
        text_pages.append(text)

    final_text = "\n\n".join(text_pages)
    num_pages = len(reader.pages)

    return num_pages, final_text


def clean_section_names(toc_data):
    for entry in toc_data:
        if "section" in entry:
            # Remove newlines and double spaces
            entry["section"] = entry["section"].replace("\n", " ").replace("\\n", " ")
            entry["section"] = re.sub(r"\s+", " ", entry["section"]).strip()
    return toc_data


def extract_table_of_contents(text):

    prompt = f"""
    You are extracting the Table of Contents (ToC) from an annual report.

    OUTPUT FORMAT (MANDATORY):
    LEVEL<TAB>SECTION<TAB>PAGE

    RULES:
    - Output one entry per line
    - PAGE must be an integer
    - LEVEL indicates nesting depth (1 = main section)
    - Only include items explicitly listed in the Table of Contents
    - Do NOT infer, rename, or reorder sections
    - If no Table of Contents is present, output NOTHING
    - Do not include headers, explanations, or extra text

    DOCUMENT TEXT:
    {text}
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents={"text": prompt},
        config={
            "temperature": 0,
            "top_p": 0.95,
            "top_k": 20,
        },
    )

    def parse_toc_tsv(text):
        out = []
        for line in text.strip().splitlines():
            try:
                l, s, p = line.split("\t")
                l, p = int(l), int(p)
                if l > 0 and len(s.strip()) > 2:
                    out.append({"level": l, "section": s.strip(), "page": p})
            except ValueError:
                pass
        return out

    toc = parse_toc_tsv(response.text)
    return toc


def extract_toc_from_pdf(pdf_path):
    console.print(f"[bold green]📄 Processing File:[/bold green] {pdf_path}")
    extracted_text = extract_first_pages(pdf_path, num_pages=6)
    toc = extract_table_of_contents(extracted_text)
    return toc


def identify_financial_sections(toc_json):
    console.rule("[bold magenta]💰 Stage 3: Identifying Financial Sections")

    prompt = """
You're an expert in reading annual reports.

Below is a list of sections from a company's table of contents. Identify and return ONLY the sections that contain FINANCIAL data about the company. Prioritize **Consolidated** sections (e.g. "Consolidated Financial Statements") over Standalone sections if both are present.

You're looking for sections related to:
- Profit and Loss / Income Statement
- Balance Sheet
- Cash Flow Statement
- Auditor’s Report (financial-specific)

Return a JSON list like:
[
  {"section": "Consolidated Financial Statements", "page": 194},
  {"section": "Cash Flow Statement", "page": 198}
]

Here is the extracted ToC JSON:
""" + json.dumps(toc_json, indent=2)

    model = genai.GenerativeModel(model_name="gemini-2.0-flash")
    with console.status("📡 Sending ToC to Gemini to identify financial sections..."):
        response = model.generate_content(prompt)

    raw_text = response.text.strip()
    if raw_text.startswith("```json"):
        match = re.search(r"```json\s*(.*?)```", raw_text, re.DOTALL)
        if match:
            raw_text = match.group(1)

    try:
        financial_sections = json.loads(raw_text)
        cleaned = clean_section_names(financial_sections)
        return cleaned
    except json.JSONDecodeError as e:
        console.print(
            f"[red]❌ JSON parsing failed in financial section filter: {e}[/red]"
        )
        return None


def estimate_section_page_ranges(financial_sections, total_pages):
    # Append dummy end marker
    sorted_sections = sorted(financial_sections, key=lambda x: x["page"])
    page_ranges = []

    for i, section in enumerate(sorted_sections):
        start = section["page"]
        end = (
            sorted_sections[i + 1]["page"] - 1
            if i + 1 < len(sorted_sections)
            else total_pages  # Last section
        )
        page_ranges.append({"section": section["section"], "start": start, "end": end})
    return page_ranges


def extract_pages_text(pdf_path, start, end):
    doc = fitz.open(pdf_path)
    content = ""
    for i in range(start - 1, end):  # PyMuPDF is 0-indexed
        page = doc[i]
        text = page.get_text()
        if len(text) < 100:
            images = convert_from_path(pdf_path, first_page=i + 1, last_page=i + 1)
            text = pytesseract.image_to_string(images[0])
        content += f"\n--- PAGE {i + 1} ---\n{text}"
    doc.close()
    return content


def extract_financial_data_from_text(section_name, section_text):
    console.rule(f"[bold blue]📊 Extracting Financial Tables: {section_name}")

    prompt = f"""
You are an expert financial analyst.

The following is a full section from a company's annual report (usually the 'Consolidated Financial Statements').

Please extract the following if present, and return as a JSON object:

```json
{{
  "income_statement": [ [ "Header1", "Header2", ... ], [ "Row1Value1", "Row1Value2", ... ], ... ],
  "balance_sheet": [...],
  "cash_flow_statement": [...]
}}
```

Only return valid tables. Leave missing ones as empty lists. Don’t include extra commentary.

--- START OF SECTION TEXT ---
{section_text}
--- END OF SECTION TEXT ---
"""
    model = genai.GenerativeModel(model_name="gemini-2.0-flash")

    with console.status("🔍 Asking Gemini to extract structured financial tables..."):
        response = model.generate_content(prompt)

    raw_text = response.text.strip()

    if raw_text.startswith("```json"):
        match = re.search(r"```json\s*(.*?)```", raw_text, re.DOTALL)
        if match:
            raw_text = match.group(1)

    try:
        data = json.loads(raw_text)
        return data
    except json.JSONDecodeError as e:
        console.print(f"[red]❌ Failed to parse table JSON: {e}[/red]")
        return None


def to_dataframe(name, table_data):
    if not table_data or len(table_data) < 2:
        return None
    df = pd.DataFrame(table_data[1:], columns=table_data[0])
    df.columns.name = name
    return df


if __name__ == "__main__":
    pdf_file = "annual_report.pdf"
    toc_json = extract_toc_from_pdf(pdf_file)

    if toc_json:
        financial_sections = identify_financial_sections(toc_json)

        if financial_sections:
            total_pages = fitz.open(pdf_file).page_count
            section_ranges = estimate_section_page_ranges(
                financial_sections, total_pages
            )

            for sec in section_ranges:
                sec_text = extract_pages_text(pdf_file, sec["start"], sec["end"])
                extracted_data = extract_financial_data_from_text(
                    sec["section"], sec_text
                )

                if extracted_data:
                    for k in [
                        "income_statement",
                        "balance_sheet",
                        "cash_flow_statement",
                    ]:
                        df = to_dataframe(k, extracted_data.get(k, []))
                        if df is not None:
                            console.rule(
                                f"[bold green]📈 {k.replace('_', ' ').title()} ({sec['section']})"
                            )
                            console.print(df)
