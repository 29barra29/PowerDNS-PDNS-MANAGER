"""Webhook-Empfaenger fuer die E2E-Tests (nur Standardbibliothek, laeuft in python:3.12-slim).

Nimmt beliebige POST/PUT-Requests unter /hook/<name> an, speichert Header und Rohkoerper und
antwortet je nach Query-Parametern:

  POST /hook/<name>                 -> 200 {"ok": true, "attempt": n}
  POST /hook/<name>?status=410      -> antwortet immer mit diesem Status (z. B. 4xx/5xx-Tests)
  POST /hook/<name>?fail=N          -> die ersten N Versuche je Zustellungs-Schluessel mit 500
                                       (Schluessel: Header X-DNS-Manager-Delivery, sonst
                                       X-DNS-Manager-Event-Id, sonst SHA-256 des Koerpers)
  POST /hook/<name>?delay=S         -> wartet S Sekunden (max. 60) vor der Antwort (Timeout-Tests)

  GET    /deliveries[?path=/hook/x] -> alle gespeicherten Requests (aelteste zuerst) als JSON-Liste
  DELETE /deliveries                -> Speicher und Versuchszaehler leeren
  GET    /health                    -> {"status": "ok"}

Jeder gespeicherte Eintrag:
  {"seq", "received_at", "method", "path", "query", "headers", "body_text", "body_b64", "json",
   "attempt", "key", "response_status"}
"body_b64" ist der exakte Rohkoerper (fuer HMAC-Pruefungen), "json" der geparste Koerper oder null.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

_LOCK = threading.Lock()
_RECEIVED: list[dict] = []
_ATTEMPTS: dict[str, int] = {}
_SEQ = 0
_MAX_BODY = 4 * 1024 * 1024
_MAX_ENTRIES = 5000


class Handler(BaseHTTPRequestHandler):
    server_version = "pdnsmgr-e2e-receiver/1"

    def _send(self, code: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _store(self) -> None:
        global _SEQ
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length") or 0)
        if length > _MAX_BODY:
            self._send(413, {"ok": False, "error": "body too large"})
            return
        raw = self.rfile.read(length) if length else b""
        headers = {k: v for k, v in self.headers.items()}
        key = (
            self.headers.get("X-DNS-Manager-Delivery")
            or self.headers.get("X-DNS-Manager-Event-Id")
            or hashlib.sha256(raw).hexdigest()[:24]
        )
        try:
            parsed_json = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, ValueError):
            parsed_json = None

        def _int(name: str, default: int) -> int:
            try:
                return int((qs.get(name) or [str(default)])[0])
            except ValueError:
                return default

        fail_n = _int("fail", 0)
        forced = _int("status", 0)
        delay = min(max(_int("delay", 0), 0), 60)

        with _LOCK:
            attempt = _ATTEMPTS.get(key, 0) + 1
            _ATTEMPTS[key] = attempt
            if forced:
                code = forced
            elif attempt <= fail_n:
                code = 500
            else:
                code = 200
            _SEQ += 1
            entry = {
                "seq": _SEQ,
                "received_at": time.time(),
                "method": self.command,
                "path": parsed.path,
                "query": parsed.query,
                "headers": headers,
                "body_text": raw.decode("utf-8", errors="replace"),
                "body_b64": base64.b64encode(raw).decode("ascii"),
                "json": parsed_json,
                "attempt": attempt,
                "key": key,
                "response_status": code,
            }
            _RECEIVED.append(entry)
            if len(_RECEIVED) > _MAX_ENTRIES:
                del _RECEIVED[: len(_RECEIVED) - _MAX_ENTRIES]
        if delay:
            time.sleep(delay)
        self._send(code, {"ok": 200 <= code < 300, "attempt": attempt})

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path.startswith("/hook"):
            self._store()
        else:
            self._send(404, {"ok": False, "error": "unknown path"})

    do_PUT = do_POST  # noqa: N815

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/deliveries":
            want = (parse_qs(parsed.query).get("path") or [None])[0]
            with _LOCK:
                items = [e for e in _RECEIVED if want is None or e["path"] == want]
            self._send(200, items)
        elif parsed.path == "/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"ok": False, "error": "unknown path"})

    def do_DELETE(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/deliveries":
            with _LOCK:
                _RECEIVED.clear()
                _ATTEMPTS.clear()
            self._send(200, {"ok": True})
        else:
            self._send(404, {"ok": False, "error": "unknown path"})

    def log_message(self, fmt, *args) -> None:  # noqa: D401 - Zugriffslog nur bei Bedarf
        if os.environ.get("RECEIVER_VERBOSE"):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    port = int(os.environ.get("RECEIVER_PORT", "8080"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    print(f"webhook-receiver lauscht auf :{port}", flush=True)
    srv.serve_forever()
