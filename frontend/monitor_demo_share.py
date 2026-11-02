"""Serve only the monitor demo page and its local status API for temporary sharing."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PAGE_PATH = Path(__file__).resolve().parent / "public" / "monitor-demo.html"
API_PATH = "/api/monitor-demo/status/"
BACKEND_URL = "http://127.0.0.1:8000" + API_PATH


class DemoShareHandler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/monitor-demo.html"):
            page = PAGE_PATH.read_text(encoding="utf-8")
            page = page.replace(
                '"http://127.0.0.1:8000/api/monitor-demo/status/"',
                '"/api/monitor-demo/status/"',
            )
            return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        if self.path == API_PATH:
            return self._proxy_api("GET")
        return self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self):
        if self.path == API_PATH:
            return self._proxy_api("POST")
        return self._send(404, b"Not found", "text/plain; charset=utf-8")

    def _proxy_api(self, method):
        request = Request(BACKEND_URL, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                body = response.read()
                return self._send(response.status, body, "application/json; charset=utf-8")
        except HTTPError as error:
            return self._send(error.code, error.read(), "application/json; charset=utf-8")
        except (TimeoutError, URLError):
            return self._send(503, b'{"detail":"Demo status service unavailable"}', "application/json")

    def log_message(self, format, *args):
        print("monitor-demo-share:", format % args)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8081), DemoShareHandler)
    print("Serving the monitor demo on http://127.0.0.1:8081 (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
