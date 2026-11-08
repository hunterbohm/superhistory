"""A web server on 127.0.0.1 that answers from a function, for tests of programs that talk to Google or Notion."""
import http.server
import json
import threading
from urllib.parse import parse_qs, urlparse


class Seen:
    def __init__(self, method, path, query, body, headers):
        self.method, self.path, self.query, self.body, self.headers = method, path, query, body, headers


class FakeServer:
    """`answer(seen)` returns (status, body) or (status, body, headers), the body a JSON value. Every request is kept
    in `requests`."""

    def __init__(self, answer):
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def handle_any(self):
                url = urlparse(self.path)
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
                body = parse_qs(raw) if "form" in self.headers.get("Content-Type", "") or not raw.startswith("{") \
                    else json.loads(raw)
                seen = Seen(self.command, url.path, parse_qs(url.query), body, self.headers)
                outer.requests.append(seen)
                status, payload, *extra = answer(seen)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for key, value in (extra[0] if extra else {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = handle_any

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def paths(self):
        return [r.path for r in self.requests]
