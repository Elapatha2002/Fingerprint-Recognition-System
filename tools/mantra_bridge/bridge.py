"""Loopback-only bridge between a hosted browser and the Mantra MFS100 SDK.

The hosted Streamlit process cannot access USB devices on an examiner's PC.
This companion runs on that PC, accepts authenticated requests only from
configured web origins, and invokes the 32-bit SDK helper.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if __package__:
    from .protocol import MAX_REQUEST_BYTES, validate_match_request
else:
    # Also support copying the complete bridge folder to an evaluator PC.
    from protocol import MAX_REQUEST_BYTES, validate_match_request
HELPER = Path(__file__).with_name("capture.ps1")
DEFAULT_SDK = Path(r"C:\Program Files\Mantra\MFS100\Driver\MFS100Test")
CAPTURE_LOCK = threading.Lock()


def _powershell32() -> Path:
    windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for path in (
        windows / "SysWOW64" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        windows / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
    ):
        if path.is_file():
            return path
    raise RuntimeError("32-bit Windows PowerShell was not found.")


def run_sdk(action: str, timeout_seconds: int, request: dict | None = None) -> tuple[int, dict]:
    sdk = os.environ.get("MFS100_SDK_DIR", str(DEFAULT_SDK))
    command = [
        str(_powershell32()), "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(HELPER),
        "-Action", action, "-TimeoutSeconds", str(timeout_seconds),
        "-SdkDirectory", sdk,
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout_seconds + 12,
            input=json.dumps(request) if request is not None else None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return HTTPStatus.GATEWAY_TIMEOUT, {
            "ok": False, "error": "The sensor did not respond before the timeout."
        }
    except OSError as exc:
        return HTTPStatus.SERVICE_UNAVAILABLE, {
            "ok": False, "error": f"Could not start the MFS100 SDK: {exc}"
        }

    payload = None
    for line in reversed(result.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "ok" in candidate:
            payload = candidate
            break
    if payload is None:
        return HTTPStatus.BAD_GATEWAY, {
            "ok": False,
            "error": result.stderr.strip() or "The MFS100 helper returned no response.",
        }
    return (HTTPStatus.OK if payload.get("ok") else
            HTTPStatus.SERVICE_UNAVAILABLE), payload


def load_or_create_token(explicit: str | None) -> str:
    if explicit:
        return explicit
    configured = os.environ.get("FRS_BRIDGE_TOKEN", "").strip()
    if configured:
        return configured
    base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Fingerprint-Recognition-System"
    token_file = base / "mantra-bridge-token.txt"
    try:
        if token_file.is_file():
            token = token_file.read_text(encoding="utf-8").strip()
            if token:
                return token
        base.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(24)
        token_file.write_text(token, encoding="utf-8")
        return token
    except OSError:
        return secrets.token_urlsafe(24)


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, *, token: str, origins: set[str], timeout: int):
        super().__init__(address, handler)
        self.bridge_token = token
        self.allowed_origins = origins
        self.capture_timeout = timeout


class Handler(BaseHTTPRequestHandler):
    server_version = "Fingerprint-Recognition-Mantra-Bridge/2.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    @property
    def bridge(self) -> BridgeServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt: str, *args) -> None:
        # Keep logs useful without ever printing pairing tokens or image data.
        sys.stderr.write(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}\n")

    def _origin_allowed(self) -> bool:
        return self.headers.get("Origin", "") in self.bridge.allowed_origins

    def _token_allowed(self) -> bool:
        supplied = self.headers.get("X-FRS-Bridge-Token", "")
        return bool(supplied) and hmac.compare_digest(supplied, self.bridge.bridge_token)

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin in self.bridge.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not self._origin_allowed():
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Web origin is not allowed."})
            return False
        if not self._token_allowed():
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Pairing code is incorrect."})
            return False
        return True

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._origin_allowed():
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Web origin is not allowed."})
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-FRS-Bridge-Token")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(HTTPStatus.OK, {"ok": True, "bridge": "ready"})
            return
        if self.path != "/status":
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})
            return
        if not self._authorized():
            return
        # Both Streamlit tabs can initialise their controls together. Serialize
        # short status checks instead of making the second tab fail immediately.
        if not CAPTURE_LOCK.acquire(timeout=6):
            self._json(HTTPStatus.CONFLICT, {"ok": False, "error": "The scanner is busy."})
            return
        try:
            status, payload = run_sdk("status", 5)
            self._json(status, payload)
        finally:
            CAPTURE_LOCK.release()

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/capture", "/match"}:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})
            return
        if not self._authorized():
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid body length."})
            return
        limit = MAX_REQUEST_BYTES if self.path == "/match" else 1024
        if length < 0 or length > limit or self.headers.get("Transfer-Encoding"):
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                       {"ok": False, "error": "Request body is too large."})
            return
        try:
            body = self.rfile.read(length) if length else b"{}"
            request = validate_match_request(json.loads(body)) if self.path == "/match" else None
        except (ValueError, OSError):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid matching request or ISO template."})
            return
        if not CAPTURE_LOCK.acquire(blocking=False):
            self._json(HTTPStatus.CONFLICT,
                       {"ok": False, "error": "The scanner is busy capturing or matching."})
            return
        try:
            status, payload = (run_sdk("match", 60, request) if self.path == "/match"
                               else run_sdk("capture", self.bridge.capture_timeout))
            self._json(status, payload)
        finally:
            CAPTURE_LOCK.release()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local browser bridge for Mantra MFS100")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--token", help="Pairing code; generated and persisted when omitted")
    parser.add_argument("--allow-origin", action="append", default=[],
                        help="Exact Streamlit origin, e.g. https://fsd.example.com")
    parser.add_argument("--timeout", type=int, default=20,
                        help="Fingerprint placement timeout in seconds")
    return parser.parse_args()


def main() -> int:
    if os.name != "nt":
        print("The MFS100 bridge must run on the Windows PC connected to the scanner.",
              file=sys.stderr)
        return 2
    args = parse_args()
    defaults = {"http://localhost:8501", "http://127.0.0.1:8501",
                "http://localhost:8502", "http://127.0.0.1:8502"}
    env_origins = {item.strip().rstrip("/") for item in
                   os.environ.get("FRS_ALLOWED_ORIGINS", "").split(",") if item.strip()}
    origins = defaults | env_origins | {item.rstrip("/") for item in args.allow_origin}
    token = load_or_create_token(args.token)
    server = BridgeServer(("127.0.0.1", args.port), Handler, token=token,
                          origins=origins, timeout=max(5, min(args.timeout, 120)))
    print("Fingerprint Recognition Mantra bridge is running.")
    print(f"Address: http://127.0.0.1:{args.port}")
    print(f"Pairing code: {token}")
    print("Allowed web origins:")
    for origin in sorted(origins):
        print(f"  {origin}")
    print("Keep this window open while using the fingerprint system. Press Ctrl+C to stop.")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nStopping bridge...")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
