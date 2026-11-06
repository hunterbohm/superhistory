"""The dashboard's server: one page, the static files it needs, and JSON read from the store.

It binds a loopback address only, answers only GET and HEAD, opens the store read-only, and loads nothing from
outside: every font, script and style is served from this folder, and a content-security policy keeps it that way.

Loopback is open to every account on the computer, so the data is behind a key made for each run. The address the
command prints holds it as `?access=KEY`, good for one visit: that visit's page carries the key in a meta tag, the
page's script keeps it in sessionStorage and removes it from the address, and every request for data then sends it in
the X-Superhistory-Key header. The key is in no cookie, so a program listening on another port of this computer, which
a cookie would reach, never receives it; a second `?access=KEY` is refused, so a key copied from a process list or a
browser's history after the first visit is worthless. The page and its static files hold no data and need no key.
"""
import hmac
import html
import ipaddress
import json
import os
import pathlib
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from kb import store
from kb.dashboard import catalog as cat, views
from kb.mask import mask

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
         ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8", ".html": "text/html; charset=utf-8"}
PAGES = ("projects", "people", "project", "person", "source", "search", "item")
POLICY = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
          "img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


KEY_HEADER = "X-Superhistory-Key"
NEEDS_KEY = "This page needs this run's key. Open the address that `superhistory dashboard` printed."
USED = ("That address has been used. Its key is good for one visit; run `superhistory dashboard` again for a new one, "
        "or reload the tab that opened it.")


class NotLoopback(ValueError):
    pass


def check_host(host):
    """The address to bind, or NotLoopback. Only an IPv4 loopback address is served: a name could resolve elsewhere."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise NotLoopback(f"{host!r} is not an address; the dashboard serves 127.0.0.1 only") from None
    if address.version != 4 or not address.is_loopback:
        raise NotLoopback(f"{host} is not a loopback address; the dashboard serves 127.0.0.1 only")
    return str(address)


def number(values, name, default=0):
    value = (values.get(name) or [""])[0]
    return int(value) if value.isdigit() and len(value) < 10 else default


class Dashboard:
    """The store's path and config, and the catalog built from it, rebuilt when the store's files change."""

    def __init__(self, paths, config):
        self.paths, self.config = paths, config
        self._lock = threading.Lock()
        self._cached = (None, None)

    def connect(self):
        db = store.connect(self.paths.db, readonly=True)
        db.execute("PRAGMA query_only = ON")
        return db

    def _signature(self):
        sig = []
        for suffix in ("", "-wal"):
            try:
                st = os.stat(str(self.paths.db) + suffix)
                sig.append((st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append(None)
        return tuple(sig)

    def catalog(self, db):
        sig = self._signature()
        with self._lock:
            if self._cached[0] != sig:
                self._cached = (sig, cat.build(db))
            return self._cached[1]

    def api(self, name, values):
        """The JSON for /api/<name>, or None for a name that is not one. Raises LookupError for a missing record."""
        if name not in ("overview", "projects", "people", "project", "person", "source", "search", "item"):
            return None
        db = self.connect()
        try:
            if name == "search":
                query = (values.get("q") or [""])[0]
                return views.search_hits(db, query, (values.get("source") or [""])[0][:100] or None) if query.strip() else []
            catalog = self.catalog(db)
            offset = number(values, "offset")
            key = (values.get("key") or values.get("name") or [""])[0][:300]
            if name == "overview":
                return views.overview(db, catalog, self.paths, self.config)
            if name == "projects":
                return views.projects(catalog)
            if name == "people":
                return views.people(catalog)
            if name == "item":
                page = (values.get("page") or [""])[0]
                found = views.item(db, catalog, number(values, "id"), int(page) if page.isdigit() else None,
                                   (values.get("q") or [""])[0][:200])
            else:
                found = {"project": views.project, "person": views.person, "source": views.source}[name](
                    catalog, key, offset)
            if found is None:
                raise LookupError(name)
            return found
        finally:
            db.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "Superhistory"
    sys_version = ""

    def _send(self, code, body, ctype, head=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", POLICY)
        if code == 405:
            self.send_header("Allow", "GET, HEAD")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _json(self, code, data, head=False):
        self._send(code, json.dumps(data).encode(), "application/json", head)

    def _has_key(self):
        return self.server.is_key(self.headers.get(KEY_HEADER, ""))

    def _page(self, key, head):
        """The page. With a key to hand over, a meta tag carries it to the page's script."""
        with open(os.path.join(STATIC, "index.html"), "rb") as fh:
            body = fh.read()
        if key:
            body = body.replace(b'<meta charset="utf-8">', b'<meta charset="utf-8">\n<meta name="superhistory-key" '
                                b'content="' + key.encode("ascii") + b'">', 1)
        return self._send(200, body, TYPES[".html"], head)

    def do_GET(self, head=False):
        host = self.headers.get("Host", "")
        if host not in self.server.allowed_hosts:
            return self._send(403, b"This address is not served.", "text/plain; charset=utf-8", head)
        url = urlparse(self.path)
        query = parse_qs(url.query)
        parts = [p for p in url.path.split("/") if p]
        if "access" in query:
            if not head and (not parts or parts[0] in PAGES) and self.server.spend((query["access"] or [""])[0]):
                return self._page(self.server.key, head)
            return self._send(403, USED.encode(), "text/plain; charset=utf-8", head)
        if parts[:1] == ["api"] and not self._has_key():
            return self._json(403, {"error": NEEDS_KEY}, head)
        try:
            if parts[:1] == ["api"] and len(parts) == 2:
                data = self.server.dashboard.api(parts[1], parse_qs(url.query))
                if data is None:
                    return self._json(404, {"error": "not found"}, head)
                return self._json(200, data, head)
            if parts[:1] == ["static"] and len(parts) == 2:
                ext = os.path.splitext(parts[1])[1]
                if ext in TYPES and parts[1] in os.listdir(STATIC):
                    with open(os.path.join(STATIC, parts[1]), "rb") as fh:
                        return self._send(200, fh.read(), TYPES[ext], head)
                return self._send(404, b"Not found", "text/plain; charset=utf-8", head)
            if not parts or parts[0] in PAGES:
                return self._page(None, head)
            return self._send(404, b"Not found", "text/plain; charset=utf-8", head)
        except LookupError:
            return self._json(404, {"error": "not found"}, head)
        except (store.NeedsRebuild, sqlite3.OperationalError) as exc:
            return self._json(503, {"error": mask(str(exc))}, head)
        except Exception:
            sys.stderr.write(mask(traceback.format_exc()))
            return self._json(500, {"error": "Something went wrong reading the knowledge base."}, head)

    def do_HEAD(self):
        self.do_GET(head=True)

    def _refuse(self):
        self._send(405, b"The dashboard only reads.", "text/plain; charset=utf-8")

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, dashboard):
        super().__init__(address, Handler)
        self.dashboard = dashboard
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.key = secrets.token_hex(16)
        self.spent = threading.Event()
        self._lock = threading.Lock()

    def is_key(self, value):
        return hmac.compare_digest(str(value).encode(), self.key.encode())

    def spend(self, value):
        """True for the first visit with the key, and only that one."""
        with self._lock:
            if self.spent.is_set() or not self.is_key(value):
                return False
            self.spent.set()
            return True

    @property
    def url(self):
        """The address to open: it holds this run's key, good for one visit."""
        return f"http://127.0.0.1:{self.server_address[1]}/?access={self.key}"


def make_server(paths, config, port=0, host="127.0.0.1"):
    """A dashboard server bound to a loopback address, on `port` or a free one, not yet serving."""
    return Server((check_host(host), port), Dashboard(paths, config))


class Handoff:
    """Points a browser at the dashboard without putting its key in the browser's command line, where any account on
    the computer can read it. The browser is given a file only this user can read, in a folder only this user can
    enter, that redirects to the address. The file is removed as soon as the address has been used, since its key is
    then spent, and in any case when the dashboard stops."""

    def __init__(self, dashboard):
        self.dashboard = dashboard
        self.folder = tempfile.mkdtemp(prefix="superhistory-")  # mode 0700
        self.path = os.path.join(self.folder, "open.html")
        address = html.escape(dashboard.url, quote=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={address}">'
                     f'<title>Superhistory</title><p><a href="{address}">Open the Superhistory dashboard</a>\n')
        threading.Thread(target=self._remove_when_spent, daemon=True).start()

    def open(self):
        return webbrowser.open(pathlib.Path(self.path).as_uri())

    def _remove_when_spent(self):
        self.dashboard.spent.wait()
        self.remove()

    def remove(self):
        shutil.rmtree(self.folder, ignore_errors=True)
