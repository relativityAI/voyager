from src.exchange.base import StockExchangeBase


class BSEIndia(StockExchangeBase):
    def __init__(self):
        super(BSEIndia, self).__init__()

        self.exchange = "bse"
        self._check_exchange()
        self.exchange_base_url = "https://www.bseindia.com"
        self.share_base_url_format = ""

        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.bseindia.com/'
        }


    def extract(url):
        """
        Downloads a PDF from a URL using a requests.Session and reads its content.

        Args:
            url (str): The URL of the PDF file.

        Returns:
            str: The extracted text content of the PDF.
        """
        # Create a requests session object
        s = requests.Session()

        # Add headers to mimic a web browser
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.bseindia.com/'
        }

        try:
            # Make the request to the URL using the session and headers
            response = s.get(url, headers=headers)
            response.raise_for_status()  # Check for HTTP errors

            # Create a BytesIO object from the response content
            pdf_file = io.BytesIO(response.content)

            # Create a PDF reader object with pypdf
            pdf_reader = pypdf.PdfReader(pdf_file)

            # Extract text from each page
            text = ""
            for page in pdf_reader.pages:
                text += page.extract_text() or ""
            return text

        except requests.exceptions.RequestException as e:
            print(f"Error downloading the PDF: {e}")
            return None
        except Exception as e:
            print(f"Error processing the PDF: {e}")
            return None




class BSEIndia_old:
    def __init__(self, useful_desc_path=None):
        self.curr_dir = os.path.dirname(os.path.abspath(__file__))
        self.useful_desc_path = useful_desc_path or os.path.join(self.curr_dir, "ann_desc.json")
        self.useful_descriptions = self._load_useful_descriptions()
        self.session = self._init_session()

    def _load_useful_descriptions(self):
        """Load useful announcement descriptions from JSON."""
        try:
            with open(self.useful_desc_path, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Failed to load useful descriptions: {e}")
            return []

    def _init_session(self):
        """Initialize a requests session with headers and cookies."""
        session = requests.Session()
        homepage_url = "https://www.bseindia.com"
        headers = self._get_headers()
        session.get(homepage_url, headers=headers)
        return session

    def _get_headers(self):
        """Return headers for HTTP requests."""
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Connection": "keep-alive",
            "Referer": "https://www.bseindia.com/",
            "X-Requested-With": "XMLHttpRequest"
        }

    def _construct_api_params(self, ticker, start_date, end_date):
        """Prepare API query parameters."""
        return {
            "pageno": 1,
            "strCat": "-1",
            "strPrevDate": start_date.strftime("%Y%m%d"),
            "strScrip": str(ticker),
            "strSearch": "P",
            "strToDate": end_date.strftime("%Y%m%d"),
            "strType": "C",
            "subcategory": "-1"
        }

    def fetch_announcements(self, ticker, end_date=None, num_years=1):
        """
        Fetch all announcements for a given ticker.

        :param ticker: Stock script code (e.g., "532406")
        :param end_date: End date (datetime). Default is today.
        :param num_years: How many years of data to fetch.
        :return: List of announcements (raw).
        """
        end_date = end_date or datetime.today()
        start_date = end_date - timedelta(days=365 * num_years)

        url = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
        params = self._construct_api_params(ticker, start_date, end_date)
        all_data = []

        while True:
            response = self.session.get(url, params=params, headers=self._get_headers())

            if response.status_code == 200:
                data = response.json()
                if not data.get("Table"):
                    break
                all_data.extend(data["Table"])
                params["pageno"] += 1
            else:
                print(f"Failed to fetch data. Status code: {response.status_code} on page {params['pageno']}.")
                break

        return all_data

    def filter_useful_announcements(self, announcements, threshold=75):
        """
        Filter announcements by fuzzy matching with useful descriptions.

        :param announcements: List of raw announcements.
        :param threshold: Minimum score to consider an announcement useful.
        :return: Dict of {headline: download_url}.
        """
        filtered = {}
        for ann in announcements:
            ratio = process.extractOne(ann.get("NEWSSUB", ""), self.useful_descriptions, scorer=fuzz.token_set_ratio)
            if ratio and ratio[1] > threshold:
                headline = ann.get("HEADLINE")
                file_name = ann.get("ATTACHMENTNAME")
                if headline and file_name:
                    filtered[headline] = f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/{file_name}"
        return filtered

    def get_filtered_announcements(self, ticker, end_date=None, num_years=1, threshold=75):
        """
        Full pipeline: fetch and filter useful announcements.

        :param ticker: Stock script code.
        :param end_date: Latest date (datetime). Default is today.
        :param num_years: Lookback window.
        :param threshold: Fuzzy matching threshold.
        :return: Filtered announcements dict.
        """
        announcements = self.fetch_announcements(ticker, end_date=end_date, num_years=num_years)
        return self.filter_useful_announcements(announcements, threshold=threshold)
