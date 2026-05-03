# The OCR class communicates with LLM powered VoyagerOCR running on Colab

import requests
import io
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.console import Console

# =======================
console = Console()
# =======================


class OCR(object):
    """
    - This class is probably a bit resource intensive. 
    - 
    """
    def __init__(self):
        pass



class OCR_API(object):
    """docstring for OCR_API."""
    def __init__(self, api_base=None):
        super(OCR_API, self).__init__()
        self.api_base=api_base
    
        #  Check if Colab running
        if self._check():
            success_panel = Panel(
                f"[bold green]✅ OCR API is running successfully on [cyan]{api_base}[/cyan][/bold green]",
                title="🟢 Status",
                border_style="green"
            )
            console.print(success_panel)
        else:
            error_panel = Panel(
                "[bold red]❌ OCR API failed to start. Check logs for more details.[/bold red]",
                title="🔴 Error",
                border_style="red"
            )

            console.print(error_panel)
            # raise Exception("[OCR Colab ]VoyagerOCR API not running.")

    def _check(self):
        try:
            return requests.get(self.api_base + "/check").json()['status'] == 'ok'
        except:
            return False

    def pdf2text(self, pdf_bytes : io.BytesIO):
        url = self.api_base+"/ocr/pdf"
        pdf_bytes.seek(0)

        # Send POST request with in-memory file
        files = {"file": ("yourfile.pdf", pdf_bytes, "application/pdf")}
        
        with Progress(
            SpinnerColumn(style="bold magenta"),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            task = progress.add_task("VoyagerOCR-Colab content extraction running...", total=None)
            response = requests.post(url, files=files, timeout=60*10) # 10 mins timeout
            progress.remove_task(task)

        if response.status_code == 200:
            return response.json()['text']
        else:
            print(response.json()['error'])
            print("[OCR] Couldn't extract.")
            return ""




    
