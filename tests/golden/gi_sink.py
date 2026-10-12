"""Recording HTTP sink for the GenericInterface requester golden tests (stdlib only).

Runs inside the golden compose network as ``gisink:8080`` (published on host port 8199).
Every request is stored in memory; nothing here is ever sent anywhere.

* ``GET /_requests``    - the recorded requests as JSON (method, path, query, headers, body).
* ``DELETE /_requests`` - clear the recording and reset the canned response.
* ``PUT /_response``    - JSON ``{"body": <text>, "content_type": <text>}``: the canned answer
  for every later request that is not a ``/status/<code>`` path.
* ``/status/<code>``    - any method; answers that status code with a small JSON body.
* anything else         - recorded, answered with the canned response (default ``{"ok": true}``).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

_LOCK = threading.Lock()
_REQUESTS: list[dict[str, object]] = []
_DEFAULT = {"body": '{"ok": true}', "content_type": "application/json"}
_RESPONSE = dict(_DEFAULT)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib name
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        parts = urlsplit(self.path)
        path = parts.path
        if path == "/_requests" and self.command == "GET":
            with _LOCK:
                payload = json.dumps(_REQUESTS).encode()
            self._send(200, payload, "application/json")
            return
        if path == "/_requests" and self.command == "DELETE":
            with _LOCK:
                _REQUESTS.clear()
                _RESPONSE.clear()
                _RESPONSE.update(_DEFAULT)
            self._send(200, b"{}", "application/json")
            return
        if path == "/_response" and self.command == "PUT":
            data = json.loads(raw or b"{}")
            with _LOCK:
                _RESPONSE["body"] = str(data.get("body", _DEFAULT["body"]))
                _RESPONSE["content_type"] = str(data.get("content_type", "application/json"))
            self._send(200, b"{}", "application/json")
            return
        with _LOCK:
            _REQUESTS.append(
                {
                    "method": self.command,
                    "path": path,
                    "query": parse_qsl(parts.query, keep_blank_values=True),
                    "headers": {k: v for k, v in self.headers.items()},
                    "body": raw.decode("utf-8", errors="replace"),
                }
            )
            canned = dict(_RESPONSE)
        if path.startswith("/status/"):
            try:
                code = int(path.rsplit("/", 1)[1])
            except ValueError:
                code = 400
            self._send(code, json.dumps({"status": code}).encode(), "application/json")
            return
        self._send(200, canned["body"].encode(), canned["content_type"])

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle  # noqa: N815


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()  # noqa: S104 - test sink
