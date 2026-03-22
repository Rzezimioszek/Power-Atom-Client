# -*- coding: utf-8 -*-
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging

logger = logging.getLogger("PowerAtom")

class AtomClient:
    """HTTP client with retries and cancellation support."""
    def __init__(self, timeout=30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) QGIS PowerAtom Plugin'
        })
        self.is_cancelled = False
        
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def fetch(self, url: str) -> bytes:
        self.is_cancelled = False
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            return response.content
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching {url}: {e}")
            raise e

    def download_to_file(self, url: str, target_path: str, progress_callback=None):
        self.is_cancelled = False
        try:
            with self.session.get(url, stream=True, timeout=self.timeout) as r:
                r.raise_for_status()
                total_size = int(r.headers.get('content-length', 0))
                downloaded_size = 0
                with open(target_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=16384):
                        if self.is_cancelled:
                            logger.info("Download cancelled by user.")
                            break
                        if chunk:
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            if progress_callback:
                                progress_callback(downloaded_size, total_size)
                
                if self.is_cancelled:
                    if os.path.exists(target_path):
                        os.remove(target_path)
                    raise InterruptedError("Download cancelled")
                    
        except Exception as e:
            logger.error(f"Download failed for {url}: {e}")
            raise e

    def cancel(self):
        self.is_cancelled = True
