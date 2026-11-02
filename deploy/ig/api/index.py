"""Vercel Python entry: wraps the stdlib server (app/server.py) as a BaseHTTPRequestHandler.
Data lives in /tmp (ephemeral per instance). Secrets come only from Vercel env vars."""
import os, sys, urllib.parse
os.environ.pop("AGENTS_API_TOKEN", None)  # public demo: no access password (rate limiting stays on via VERCEL)
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
sys.path.insert(0, ROOT)
os.environ.setdefault("DATA_DIR", "/tmp/ops-data")
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)
import server  # noqa: E402

server.migrate_legacy()
if hasattr(server, "seed_default_account"):
    server.seed_default_account()
server.bench.init(server)


class handler(server.Handler):
    def _fix_path(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
        orig = [v for k, v in q if k == "__p"]
        if orig:
            rest = urllib.parse.urlencode([(k, v) for k, v in q if k != "__p"])
            self.path = "/" + orig[0].lstrip("/") + ("?" + rest if rest else "")

    def do_GET(self):
        self._fix_path(); return super().do_GET()

    def do_POST(self):
        self._fix_path(); return super().do_POST()

    def do_OPTIONS(self):
        self._fix_path(); return super().do_OPTIONS()
