"""Mantra ISO-template enrolment and native-score decisions for the private demo.

Matching runs on the sensor PC, not on the hosted Linux server. The authenticated
browser is trusted to relay SDK scores; these results MUST NOT authorize access.
Legacy ORB templates are preserved, skipped and identified for recapture.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from demo_matcher import matcher as storage
from tools.mantra_bridge.protocol import MAX_CANDIDATES, iso_template

MAGIC = b"FRS-MANTRA-ISO-V1\n"
ENGINE = "Mantra MFS100 MatchISO"


def threshold() -> int:
    value = os.environ.get("FRS_MANTRA_MATCH_THRESHOLD", "1400")
    if not re.fullmatch(r"[1-9]\d{0,5}", value):
        raise ValueError("FRS_MANTRA_MATCH_THRESHOLD must be a positive SDK score (integer), not 0.55.")
    return int(value)


def pack_template(encoded: str) -> bytes:
    iso_template(encoded)
    value = MAGIC + json.dumps({"format": "ISO19794-2:2005", "template": encoded},
                              separators=(",", ":")).encode("ascii")
    # Retain the existing database BYTEA size constraint, without a schema migration.
    return value.ljust(1024, b" ")


def unpack_template(value: bytes) -> str | None:
    if not value.startswith(MAGIC):
        if value.startswith((b"PK\x03\x04", b"\x93NUMPY")):
            return None  # Legacy enrolment: no conversion or silent ORB fallback.
        raise ValueError("Unrecognised stored template format. Recapture the enrolment.")
    if len(value) > 131072:
        raise ValueError("Stored template is too large.")
    record = json.loads(value[len(MAGIC):])
    if record.get("format") != "ISO19794-2:2005":
        raise ValueError("Unsupported stored template format.")
    encoded = record.get("template")
    iso_template(encoded)
    return encoded


def _records():
    if storage.using_supabase():
        with storage._database_connection() as connection:
            rows = connection.execute(
                f"SELECT user_id, display_name, finger_label, enrolled_at, template "
                f"FROM {storage.SCHEMA}.enrolments ORDER BY user_id"
            ).fetchall()
        return [(storage.Enrolment(row["user_id"], row["display_name"], "supabase",
                                  row["enrolled_at"].isoformat(), row["finger_label"]),
                 bytes(row["template"])) for row in rows]
    return [(entry, (storage.ENROL_DIR / entry.template_path).read_bytes())
            for entry in storage.list_enrolments()]


@dataclass
class Gallery:
    entries: dict
    candidates: list
    legacy: list
    revision: str


def gallery() -> Gallery:
    entries, candidates, legacy = {}, [], []
    for entry, value in _records():
        encoded = unpack_template(value)
        if encoded is None:
            legacy.append(entry)
        else:
            entries[entry.user_id] = entry
            candidates.append({"user_id": entry.user_id, "template": encoded})
    if len(candidates) > MAX_CANDIDATES:
        raise ValueError(f"This demonstration supports at most {MAX_CANDIDATES} Mantra enrolments.")
    candidates.sort(key=lambda item: item["user_id"])
    snapshot = {"candidates": candidates, "identities": [
        (uid, entries[uid].display_name, entries[uid].finger_label, entries[uid].enrolled_at)
        for uid in sorted(entries)]}
    revision = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return Gallery(entries, candidates, legacy, revision)


def enrol(user_id: str, name: str, encoded: str, finger: str, *, replace=False):
    if not re.fullmatch(r"USR-\d{3,12}", user_id):
        raise ValueError("Invalid enrolment identifier.")
    name, finger = name.strip(), finger.strip()
    if not name or len(name) > 120 or not finger or len(finger) > 50:
        raise ValueError("Provide a valid name and finger label.")
    value = pack_template(encoded)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = storage.Enrolment(user_id, name, "supabase", timestamp, finger)
    if storage.using_supabase():
        with storage._database_connection() as connection:
            if replace:
                row = connection.execute(
                    f"UPDATE {storage.SCHEMA}.enrolments SET display_name=%s, finger_label=%s, "
                    "template=%s, enrolled_at=%s WHERE user_id=%s RETURNING user_id",
                    (name, finger, value, timestamp, user_id),
                ).fetchone()
                if not row:
                    raise ValueError("This enrolment no longer exists. Refresh the page.")
            else:
                connection.execute(
                    f"INSERT INTO {storage.SCHEMA}.enrolments "
                    "(user_id,display_name,finger_label,template,enrolled_at) VALUES (%s,%s,%s,%s,%s)",
                    (user_id, name, finger, value, timestamp),
                )
    else:
        index = storage._load_local_index()
        if (user_id in index) != replace:
            raise ValueError("Enrolment changed; refresh before saving.")
        storage.ENROL_DIR.mkdir(parents=True, exist_ok=True)
        entry.template_path = f"{user_id}.mantra"
        (storage.ENROL_DIR / entry.template_path).write_bytes(value)
        index[user_id] = entry
        storage._save_local_index(index)
        # Replaced legacy files are intentionally not deleted automatically.
    return entry


def decide(payload: dict, current: Gallery, request_id: str) -> storage.MatchResult:
    if (not isinstance(payload, dict) or payload.get("ok") is not True
            or payload.get("request_id") != request_id or payload.get("engine") != ENGINE):
        raise ValueError("Stale or invalid SDK response. Capture again using the updated bridge.")
    scores = payload.get("scores")
    if not isinstance(scores, list) or len(scores) != len(current.entries) or not scores:
        raise ValueError("SDK results do not cover the current enrolment directory.")
    ranked, seen = [], set()
    for item in scores:
        if not isinstance(item, dict):
            raise ValueError("Invalid SDK score entry.")
        uid, score = item.get("user_id"), item.get("score")
        if uid not in current.entries or uid in seen or type(score) is not int or score < 0:
            raise ValueError("Invalid SDK score entry.")
        ranked.append((uid, score)); seen.add(uid)
    ranked.sort(key=lambda item: (-item[1], item[0]))
    cutoff = threshold()
    best_id, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    # Never resolve two passing identities (including ties) by database order.
    passing = sum(score >= cutoff for _, score in ranked)
    matched = passing == 1
    return storage.MatchResult(
        matched, best_id if matched else None,
        current.entries[best_id].display_name if matched else None,
        best, cutoff, ranked, "matched" if matched else "ambiguous" if passing > 1 else "below_threshold",
        second, best-second,
    )
