"""Local web server for the Health Companion UI.

Serves the static UI and proxies chat requests to the local `agentcore dev`
runtime (http://127.0.0.1:8080/invocations), streaming the agent's Server-Sent
Events straight through to the browser. Running the proxy on the same origin as
the page avoids any CORS setup.

Usage:
    python web-ui/server.py                 # serves on http://127.0.0.1:3000
    python web-ui/server.py --port 4000
    python web-ui/server.py --agent http://127.0.0.1:8080

Only the Python standard library is used, so there is nothing extra to install.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 3000
DEFAULT_AGENT = "http://127.0.0.1:8080"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    agent_url = DEFAULT_AGENT
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter console
        sys.stderr.write("  %s\n" % (fmt % args))

    # --- static files ----------------------------------------------------- #
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            self._health()
            return
        if path in ("/", ""):
            path = "/index.html"
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        # keep serving strictly inside the ui directory
        if STATIC_DIR not in target.parents and target != STATIC_DIR:
            self.send_error(403, "Forbidden")
            return
        if not target.is_file():
            self.send_error(404, "Not found")
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # --- chat proxy ------------------------------------------------------- #
    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/chat":
            self.send_error(404, "Not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
            prompt = (payload.get("prompt") or "").strip()
            if not prompt:
                raise ValueError("prompt is required")
        except (json.JSONDecodeError, ValueError) as exc:
            self._json_error(400, f"Bad request: {exc}")
            return

        upstream = {"prompt": prompt}
        for key in ("patient_id", "language", "actor_id"):
            if payload.get(key):
                upstream[key] = payload[key]

        req = urllib.request.Request(
            f"{self.agent_url}/invocations",
            data=json.dumps(upstream).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )

        try:
            resp = urllib.request.urlopen(req, timeout=120)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            self._json_error(
                502,
                "Cannot reach the agent. Is `agentcore dev` running on "
                f"{self.agent_url}? ({reason})",
            )
            return

        # Stream the agent's SSE response straight through to the browser.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                chunk = resp.read(1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client navigated away mid-stream
        finally:
            resp.close()

    def _health(self):
        """Cheap reachability check: open a TCP socket to the agent, no invocation."""
        import socket
        from urllib.parse import urlparse

        parsed = urlparse(self.agent_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8080
        online = False
        try:
            with socket.create_connection((host, port), timeout=2):
                online = True
        except OSError:
            online = False
        self._json({"agent": "online" if online else "offline"}, 200)

    def _json(self, obj: dict, code: int = 200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, code: int, message: str):
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    ap = argparse.ArgumentParser(description="Health Companion web UI server")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--agent", default=DEFAULT_AGENT, help="base URL of the agentcore dev runtime")
    args = ap.parse_args()

    Handler.agent_url = args.agent.rstrip("/")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print("Health Companion UI")
    print(f"  Open:  {url}")
    print(f"  Agent: {Handler.agent_url}/invocations")
    print("  Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
