"""Independent Mantra MFS100 capture adapter for the recognition demo."""

import base64
import io
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image
from tools.mantra_bridge.protocol import iso_template, validate_match_request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

CAPTURE_HELPER = PROJECT_ROOT / "tools" / "mantra_bridge" / "capture.ps1"
DEFAULT_SDK_DIR = Path(r"C:\Program Files\Mantra\MFS100\Driver\MFS100Test")


class SensorError(RuntimeError):
    pass


@dataclass
class CaptureResult:
    image_bytes: bytes
    width: int
    height: int
    dpi: int
    quality: int | None
    captured_at: str
    serial: str | None = None
    capture_id: str | None = None
    iso_template: str | None = None

    def pil_image(self) -> Image.Image:
        return Image.open(io.BytesIO(self.image_bytes))


def capture_transport() -> str:
    configured = os.environ.get("MANTRA_SENSOR_TRANSPORT", "auto").strip().lower()
    if configured in {"direct", "bridge"}:
        return configured
    sdk_dir = Path(os.environ.get("MFS100_SDK_DIR", str(DEFAULT_SDK_DIR)))
    return "direct" if os.name == "nt" and (sdk_dir / "MANTRA.MFS100.dll").is_file() else "bridge"


def bridge_url() -> str:
    # Keep the standalone demonstration separate from FSD-XAI's port 8765.
    return os.environ.get("MANTRA_BRIDGE_URL", "http://127.0.0.1:8766").rstrip("/")


def _powershell32() -> Path:
    if os.name != "nt":
        raise SensorError("Direct capture requires the Windows sensor computer.")
    windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for candidate in (
        windows / "SysWOW64" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        windows / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
    ):
        if candidate.is_file():
            return candidate
    raise SensorError("32-bit Windows PowerShell was not found.")


def _run_helper(action: str, timeout_seconds: int, request: dict | None = None) -> dict:
    if not CAPTURE_HELPER.is_file():
        raise SensorError(f"Sensor helper is missing: {CAPTURE_HELPER}")
    sdk_dir = os.environ.get("MFS100_SDK_DIR", str(DEFAULT_SDK_DIR))
    command = [
        str(_powershell32()), "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass", "-File", str(CAPTURE_HELPER),
        "-Action", action, "-TimeoutSeconds", str(timeout_seconds),
        "-SdkDirectory", sdk_dir,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds + 12,
            input=json.dumps(request) if request is not None else None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as error:
        raise SensorError("The sensor did not respond before the timeout.") from error
    except OSError as error:
        raise SensorError(f"Could not start the sensor helper: {error}") from error

    for line in reversed(completed.stdout.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "ok" in payload:
            if not payload.get("ok"):
                raise SensorError(payload.get("error") or "MFS100 capture failed.")
            return payload
    raise SensorError(completed.stderr.strip() or "The sensor returned no valid response.")


def is_available() -> tuple[bool, str]:
    if capture_transport() == "bridge":
        return True, "Pair with the local bridge in the browser."
    try:
        payload = _run_helper("status", 5)
        device = " ".join(filter(None, [payload.get("make"), payload.get("model")])).strip()
        return True, f"{device or 'Mantra MFS100'} ready"
    except SensorError as error:
        return False, str(error)


def from_bridge_payload(payload: dict) -> CaptureResult:
    try:
        if payload.get("protocol_version") != 2 or not payload.get("iso_template"):
            raise SensorError("Restart the updated FRS bridge: this capture has no Mantra ISO template.")
        iso_template(payload["iso_template"])
        image_bytes = base64.b64decode(payload["image_base64"], validate=True)
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.verify()
        return CaptureResult(
            image_bytes=image_bytes,
            width=int(payload["width"]),
            height=int(payload["height"]),
            dpi=int(payload.get("dpi") or 500),
            quality=(int(payload["quality"]) if payload.get("quality") is not None else None),
            captured_at=str(payload.get("captured_at") or datetime.now(timezone.utc).isoformat()),
            serial=payload.get("serial"),
            capture_id=payload.get("capture_id"),
            iso_template=payload["iso_template"],
        )
    except (KeyError, ValueError, TypeError, OSError) as error:
        raise SensorError("The bridge returned an invalid fingerprint image.") from error


def capture_fingerprint(timeout_seconds: int = 20) -> CaptureResult:
    if capture_transport() != "direct":
        raise SensorError("Hosted capture must use the browser-side bridge controls.")
    return from_bridge_payload(_run_helper("capture", max(5, min(timeout_seconds, 120))))


def match_templates(probe: str, candidates: list, request_id: str) -> dict:
    if capture_transport() != "direct":
        raise SensorError("Hosted matching must use the local browser bridge.")
    request = validate_match_request({"probe": probe, "candidates": candidates, "request_id": request_id})
    return _run_helper("match", 60, request)
