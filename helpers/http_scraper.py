import http.client

DEFAULT_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
    ),
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
}

DEFAULT_TIMEOUT = 15


class HttpScraper:
    """Cliente HTTP reutilizable para scraping vía HTTPS."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT, headers: dict | None = None):
        self.timeout = timeout
        self.headers = headers or DEFAULT_HEADERS

    def get(self, host: str, path: str) -> tuple[int, str]:
        conn = http.client.HTTPSConnection(host, timeout=self.timeout)
        try:
            conn.request("GET", path, headers=self.headers)
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            return resp.status, body
        finally:
            conn.close()
