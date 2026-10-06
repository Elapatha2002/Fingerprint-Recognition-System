"""Validation shared by the local bridge and the app; no device or database I/O."""
import base64
import binascii
import re

PROTOCOL_VERSION = 2
MAX_TEMPLATE_BYTES = 16384
MAX_CANDIDATES = 100
MAX_REQUEST_BYTES = 2300000


def iso_template(encoded: str) -> bytes:
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_TEMPLATE_BYTES + 2) // 3):
        raise ValueError("Invalid ISO fingerprint template size.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("Invalid ISO fingerprint template encoding.") from error
    # ISO/IEC 19794-2:2005 FMR record, as returned by this MFS100 SDK.
    if not 24 <= len(raw) <= MAX_TEMPLATE_BYTES or raw[:8] != b"FMR\x00 20\x00":
        raise ValueError("Expected a Mantra ISO 19794-2 fingerprint template, not an image or ORB template.")
    if int.from_bytes(raw[8:12], "big") != len(raw):
        raise ValueError("ISO fingerprint template length is inconsistent.")
    return raw


def validate_match_request(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Invalid matching request.")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{64}", request_id):
        raise ValueError("Invalid matching request identifier.")
    iso_template(payload.get("probe"))
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= MAX_CANDIDATES:
        raise ValueError(f"Match between 1 and {MAX_CANDIDATES} enrolled templates at a time.")
    seen = set()
    clean = []
    for item in candidates:
        if not isinstance(item, dict):
            raise ValueError("Invalid enrolled template entry.")
        uid = item.get("user_id")
        if not isinstance(uid, str) or not re.fullmatch(r"USR-\d{3,12}", uid) or uid in seen:
            raise ValueError("Invalid or duplicate enrolment identifier.")
        seen.add(uid)
        iso_template(item.get("template"))
        clean.append({"user_id": uid, "template": item["template"]})
    return {"request_id": request_id, "probe": payload["probe"], "candidates": clean}
