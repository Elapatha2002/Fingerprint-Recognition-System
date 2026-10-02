"""Standalone fingerprint enrolment and 1:N similarity matching.

This module deliberately contains no presentation-attack detection. It is a
small recognition demonstration used to show that a conventional matcher can
accept a sufficiently similar presentation, including a spoof of an enrolled
finger.

When ``DATABASE_URL`` is configured, enrolment metadata and normalized
templates are stored in the private ``fingerprint_demo`` PostgreSQL schema in
Supabase. Without it, local files under ``demo_matcher/enrolments`` are used
for an offline viva rehearsal.
"""
from __future__ import annotations

import io
import json
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
from dotenv import load_dotenv
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THIS_DIR = Path(__file__).resolve().parent
ENROL_DIR = THIS_DIR / "enrolments"
INDEX_PATH = ENROL_DIR / "index.json"
load_dotenv(PROJECT_ROOT / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
TEMPLATE_SIZE = (128, 128)
MATCH_THRESHOLD = float(os.environ.get("MATCH_THRESHOLD", "0.55"))
SCHEMA = "fingerprint_demo"


@dataclass
class Enrolment:
    user_id: str
    display_name: str
    template_path: str
    enrolled_at: str
    finger_label: str = "primary"


@dataclass
class MatchResult:
    matched: bool
    user_id: Optional[str]
    display_name: Optional[str]
    score: float
    threshold: float
    ranked: list[tuple[str, float]]


def using_supabase() -> bool:
    if DATABASE_URL and not DATABASE_URL.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL must be a PostgreSQL connection URL.")
    return bool(DATABASE_URL)


def _remote_ssl_kwargs(database_url: str) -> dict[str, str]:
    """Enforce encrypted PostgreSQL transport for every non-loopback host.

    Supabase/Streamlit secret entry can accidentally omit ``sslmode`` from an
    otherwise valid URL. In that case psycopg receives an explicit secure
    default. An explicitly insecure mode is still rejected.
    """
    from psycopg.conninfo import conninfo_to_dict

    options = conninfo_to_dict(database_url)
    if options.get("host") in {"localhost", "127.0.0.1", "::1"}:
        return {}
    sslmode = options.get("sslmode")
    if not sslmode:
        return {"sslmode": "require"}
    if sslmode not in {"require", "verify-ca", "verify-full"}:
        raise ValueError("Remote PostgreSQL requires sslmode=require or stronger.")
    return {}


@contextmanager
def _database_connection():
    import psycopg
    from psycopg.rows import dict_row

    ssl_kwargs = _remote_ssl_kwargs(DATABASE_URL)
    with psycopg.connect(
        DATABASE_URL,
        connect_timeout=10,
        row_factory=dict_row,
        prepare_threshold=None,
        **ssl_kwargs,
    ) as connection:
        connection.execute("SET LOCAL statement_timeout = '30s'")
        yield connection


def _load_local_index() -> dict[str, Enrolment]:
    if not INDEX_PATH.exists():
        return {}
    raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    return {uid: Enrolment(**value) for uid, value in raw.items()}


def _save_local_index(index: dict[str, Enrolment]) -> None:
    ENROL_DIR.mkdir(parents=True, exist_ok=True)
    payload = {uid: asdict(entry) for uid, entry in index.items()}
    INDEX_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _serialize(template: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, template.astype(np.float32, copy=False), allow_pickle=False)
    return buffer.getvalue()


def _deserialize(payload: bytes) -> np.ndarray:
    template = np.load(io.BytesIO(payload), allow_pickle=False)
    if template.shape != TEMPLATE_SIZE or template.dtype != np.float32:
        raise ValueError("Stored fingerprint template has an invalid format.")
    if not np.isfinite(template).all():
        raise ValueError("Stored fingerprint template contains invalid values.")
    return template


def list_enrolments() -> list[Enrolment]:
    if not using_supabase():
        return list(_load_local_index().values())
    with _database_connection() as connection:
        rows = connection.execute(
            f"SELECT user_id, display_name, finger_label, enrolled_at "
            f"FROM {SCHEMA}.enrolments ORDER BY enrolled_at, user_id"
        ).fetchall()
    return [Enrolment(
        user_id=row["user_id"],
        display_name=row["display_name"],
        template_path="supabase",
        enrolled_at=row["enrolled_at"].isoformat(),
        finger_label=row["finger_label"],
    ) for row in rows]


def count_enrolled() -> int:
    if not using_supabase():
        return len(_load_local_index())
    with _database_connection() as connection:
        row = connection.execute(
            f"SELECT COUNT(*) AS count FROM {SCHEMA}.enrolments"
        ).fetchone()
    return int(row["count"])


def next_user_id() -> str:
    max_sequence = 0
    for entry in list_enrolments():
        if entry.user_id.startswith("USR-"):
            try:
                max_sequence = max(max_sequence, int(entry.user_id.split("-", 1)[1]))
            except (ValueError, IndexError):
                continue
    return f"USR-{max_sequence + 1:03d}"


def delete_enrolment(user_id: str) -> bool:
    if using_supabase():
        with _database_connection() as connection:
            cursor = connection.execute(
                f"DELETE FROM {SCHEMA}.enrolments WHERE user_id = %s", (user_id,)
            )
            return cursor.rowcount > 0

    index = _load_local_index()
    if user_id not in index:
        return False
    template_path = ENROL_DIR / index[user_id].template_path
    if template_path.exists():
        template_path.unlink()
    del index[user_id]
    _save_local_index(index)
    return True


def clear_all() -> int:
    if using_supabase():
        with _database_connection() as connection:
            cursor = connection.execute(f"DELETE FROM {SCHEMA}.enrolments")
            return cursor.rowcount

    index = _load_local_index()
    for entry in index.values():
        path = ENROL_DIR / entry.template_path
        if path.exists():
            path.unlink()
    _save_local_index({})
    return len(index)


def _to_template(image_bytes: bytes) -> np.ndarray:
    """Convert an image into a small normalized intensity template."""
    with Image.open(io.BytesIO(image_bytes)) as image:
        image = image.convert("L").resize(TEMPLATE_SIZE)
    array = np.asarray(image, dtype=np.float32)
    array = array - array.mean()
    deviation = array.std()
    if deviation > 1e-6:
        array = array / deviation
    return array


def _score(first: np.ndarray, second: np.ndarray) -> float:
    numerator = float((first * second).sum())
    denominator = float(np.sqrt((first * first).sum() * (second * second).sum()))
    return 0.0 if denominator < 1e-9 else numerator / denominator


def enrol(user_id: str, display_name: str, image_bytes: bytes,
          finger_label: str = "primary") -> Enrolment:
    user_id = user_id.strip()
    display_name = display_name.strip()
    finger_label = finger_label.strip()
    if not user_id or not display_name:
        raise ValueError("User ID and display name are required.")
    if len(user_id) > 32 or len(display_name) > 120 or len(finger_label) > 50:
        raise ValueError("Enrolment information is too long.")

    template = _to_template(image_bytes)
    enrolled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry = Enrolment(user_id, display_name, "supabase", enrolled_at, finger_label)

    if using_supabase():
        with _database_connection() as connection:
            connection.execute(
                f"INSERT INTO {SCHEMA}.enrolments "
                "(user_id, display_name, finger_label, template, enrolled_at) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET "
                "display_name = EXCLUDED.display_name, "
                "finger_label = EXCLUDED.finger_label, "
                "template = EXCLUDED.template, enrolled_at = EXCLUDED.enrolled_at",
                (user_id, display_name, finger_label, _serialize(template), enrolled_at),
            )
        return entry

    ENROL_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{user_id}.npy"
    np.save(ENROL_DIR / filename, template, allow_pickle=False)
    entry.template_path = filename
    index = _load_local_index()
    index[user_id] = entry
    _save_local_index(index)
    return entry


def _entries_with_templates() -> list[tuple[Enrolment, np.ndarray]]:
    if using_supabase():
        with _database_connection() as connection:
            rows = connection.execute(
                f"SELECT user_id, display_name, finger_label, enrolled_at, template "
                f"FROM {SCHEMA}.enrolments ORDER BY user_id"
            ).fetchall()
        return [(
            Enrolment(row["user_id"], row["display_name"], "supabase",
                      row["enrolled_at"].isoformat(), row["finger_label"]),
            _deserialize(bytes(row["template"])),
        ) for row in rows]

    result = []
    for entry in _load_local_index().values():
        template = np.load(ENROL_DIR / entry.template_path, allow_pickle=False)
        result.append((entry, template.astype(np.float32, copy=False)))
    return result


def match(image_bytes: bytes) -> MatchResult:
    candidate = _to_template(image_bytes)
    entries = _entries_with_templates()
    ranked = sorted(
        ((entry.user_id, _score(candidate, template)) for entry, template in entries),
        key=lambda item: item[1],
        reverse=True,
    )
    if not ranked:
        return MatchResult(False, None, None, 0.0, MATCH_THRESHOLD, [])

    best_id, best_score = ranked[0]
    if best_score >= MATCH_THRESHOLD:
        best = next(entry for entry, _ in entries if entry.user_id == best_id)
        return MatchResult(True, best_id, best.display_name, best_score,
                           MATCH_THRESHOLD, ranked)
    return MatchResult(False, None, None, best_score, MATCH_THRESHOLD, ranked)


def migrate_local_enrolments() -> int:
    """Copy legacy local enrolments into configured Supabase storage."""
    if not using_supabase():
        raise RuntimeError("Configure DATABASE_URL before migrating enrolments.")
    index = _load_local_index()
    migrated = 0
    with _database_connection() as connection:
        for entry in index.values():
            path = ENROL_DIR / entry.template_path
            if not path.is_file():
                continue
            template = np.load(path, allow_pickle=False).astype(np.float32, copy=False)
            connection.execute(
                f"INSERT INTO {SCHEMA}.enrolments "
                "(user_id, display_name, finger_label, template, enrolled_at) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING",
                (entry.user_id, entry.display_name, entry.finger_label,
                 _serialize(template), entry.enrolled_at),
            )
            migrated += 1
    return migrated
