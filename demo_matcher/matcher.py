"""Standalone fingerprint enrolment and 1:N feature matching.

This module deliberately contains no presentation-attack detection. It is a
small recognition demonstration used to show that a conventional matcher can
accept a sufficiently similar presentation, including a spoof of an enrolled
finger.

When ``DATABASE_URL`` is configured, enrolment metadata and versioned feature
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
import cv2
from dotenv import load_dotenv
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THIS_DIR = Path(__file__).resolve().parent
ENROL_DIR = THIS_DIR / "enrolments"
INDEX_PATH = ENROL_DIR / "index.json"
load_dotenv(PROJECT_ROOT / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
LEGACY_TEMPLATE_SIZE = (128, 128)
TEMPLATE_VERSION = 2
FEATURE_IMAGE_SIZE = 320
MIN_KEYPOINTS = int(os.environ.get("MIN_FINGERPRINT_KEYPOINTS", "60"))
MIN_GEOMETRIC_INLIERS = int(os.environ.get("MIN_GEOMETRIC_INLIERS", "10"))
MATCH_THRESHOLD = float(os.environ.get("MATCH_THRESHOLD", "0.55"))
MATCH_MARGIN = float(os.environ.get("MATCH_MARGIN", "0.08"))
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
    reason: str = ""
    second_score: float = 0.0
    margin: float = 0.0


@dataclass
class FingerprintTemplate:
    """Versioned local-feature template; no source fingerprint image is stored."""

    points: np.ndarray       # (N, 2) float32 coordinates on a fixed canvas
    descriptors: np.ndarray  # (N, 32) uint8 ORB descriptors


class LegacyTemplateError(ValueError):
    """An enrolment predates the feature-based matcher and must be recaptured."""


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


def _serialize(template: FingerprintTemplate) -> bytes:
    buffer = io.BytesIO()
    np.savez_compressed(
        buffer,
        version=np.asarray([TEMPLATE_VERSION], dtype=np.uint8),
        points=template.points.astype(np.float32, copy=False),
        descriptors=template.descriptors.astype(np.uint8, copy=False),
    )
    payload = buffer.getvalue()
    if not 1024 <= len(payload) <= 131072:
        raise ValueError("Generated fingerprint template has an invalid size.")
    return payload


def _deserialize(payload: bytes) -> FingerprintTemplate:
    loaded = np.load(io.BytesIO(payload), allow_pickle=False)
    if isinstance(loaded, np.ndarray):
        if loaded.shape == LEGACY_TEMPLATE_SIZE:
            raise LegacyTemplateError(
                "Existing enrolments use the retired correlation format. "
                "Clear the directory and re-enrol every fingerprint."
            )
        raise ValueError("Stored fingerprint template has an invalid format.")
    try:
        version = int(loaded["version"][0])
        points = loaded["points"]
        descriptors = loaded["descriptors"]
    finally:
        loaded.close()
    if version != TEMPLATE_VERSION:
        raise LegacyTemplateError(
            "Stored fingerprint template version is unsupported. Re-enrol this user."
        )
    if (points.ndim != 2 or points.shape[1] != 2 or
            descriptors.ndim != 2 or descriptors.shape[1] != 32 or
            len(points) != len(descriptors) or len(points) < MIN_KEYPOINTS):
        raise ValueError("Stored fingerprint template has an invalid format.")
    if points.dtype != np.float32 or descriptors.dtype != np.uint8:
        raise ValueError("Stored fingerprint template has an invalid data type.")
    if not np.isfinite(points).all():
        raise ValueError("Stored fingerprint template contains invalid values.")
    return FingerprintTemplate(points, descriptors)


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


def _prepare_image(image_bytes: bytes) -> tuple[np.ndarray, np.ndarray]:
    """Decode, crop and enhance a capture for feature extraction.

    Cropping removes most scanner background while the square canvas gives the
    feature coordinates a stable reference frame. The returned mask prevents
    ORB from learning the artificial canvas boundary.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            grayscale = np.asarray(image.convert("L"), dtype=np.uint8)
    except Exception as error:
        raise ValueError("The fingerprint image could not be decoded.") from error

    if grayscale.ndim != 2 or min(grayscale.shape) < 80:
        raise ValueError("The fingerprint image is too small for recognition.")

    blurred = cv2.GaussianBlur(grayscale, (5, 5), 0)
    _, foreground = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    foreground = cv2.morphologyEx(
        foreground,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)),
        iterations=2,
    )
    contours, _ = cv2.findContours(
        foreground, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    height, width = grayscale.shape
    if contours:
        largest = max(contours, key=cv2.contourArea)
        x, y, crop_width, crop_height = cv2.boundingRect(largest)
        if crop_width * crop_height >= height * width * 0.08:
            padding = max(8, int(max(crop_width, crop_height) * 0.06))
            x0, y0 = max(0, x - padding), max(0, y - padding)
            x1 = min(width, x + crop_width + padding)
            y1 = min(height, y + crop_height + padding)
            grayscale = grayscale[y0:y1, x0:x1]
            foreground = foreground[y0:y1, x0:x1]

    crop_height, crop_width = grayscale.shape
    side = max(crop_height, crop_width)
    canvas = np.full((side, side), 255, dtype=np.uint8)
    mask_canvas = np.zeros((side, side), dtype=np.uint8)
    offset_y = (side - crop_height) // 2
    offset_x = (side - crop_width) // 2
    canvas[offset_y:offset_y + crop_height, offset_x:offset_x + crop_width] = grayscale
    mask_canvas[offset_y:offset_y + crop_height, offset_x:offset_x + crop_width] = foreground

    canvas = cv2.resize(
        canvas, (FEATURE_IMAGE_SIZE, FEATURE_IMAGE_SIZE), interpolation=cv2.INTER_AREA
    )
    mask = cv2.resize(
        mask_canvas,
        (FEATURE_IMAGE_SIZE, FEATURE_IMAGE_SIZE),
        interpolation=cv2.INTER_NEAREST,
    )
    mask = cv2.erode(
        mask,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
        iterations=1,
    )
    enhanced = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8)).apply(canvas)
    enhanced = cv2.addWeighted(
        enhanced, 1.45, cv2.GaussianBlur(enhanced, (0, 0), 1.2), -0.45, 0
    )
    return enhanced, mask


def _extract_template(image_bytes: bytes) -> FingerprintTemplate:
    image, mask = _prepare_image(image_bytes)
    detector = cv2.ORB_create(
        nfeatures=1200,
        scaleFactor=1.15,
        nlevels=8,
        edgeThreshold=15,
        patchSize=31,
        fastThreshold=7,
    )
    keypoints, descriptors = detector.detectAndCompute(image, mask)
    if descriptors is None or len(keypoints) < MIN_KEYPOINTS:
        found = 0 if descriptors is None else len(keypoints)
        raise ValueError(
            "Fingerprint detail is insufficient for reliable matching "
            f"({found} of {MIN_KEYPOINTS} required features). Clean the sensor, "
            "place the finger flat, and capture again."
        )
    points = np.asarray([point.pt for point in keypoints], dtype=np.float32)
    return FingerprintTemplate(points, descriptors.astype(np.uint8, copy=False))


def _feature_score(
    candidate: FingerprintTemplate, enrolled: FingerprintTemplate
) -> tuple[float, int]:
    """Return a bounded similarity and geometrically verified match count."""
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    pairs = matcher.knnMatch(candidate.descriptors, enrolled.descriptors, k=2)
    good = [
        first for pair in pairs if len(pair) == 2
        for first, second in [pair]
        if first.distance < 0.78 * second.distance
    ]
    if len(good) < 4:
        return 0.0, 0

    source = np.float32([candidate.points[item.queryIdx] for item in good])
    target = np.float32([enrolled.points[item.trainIdx] for item in good])
    _, inlier_mask = cv2.estimateAffinePartial2D(
        source,
        target,
        method=cv2.RANSAC,
        ransacReprojThreshold=6.0,
        maxIters=2000,
        confidence=0.99,
        refineIters=10,
    )
    if inlier_mask is None:
        return 0.0, 0
    inlier_flags = inlier_mask.ravel().astype(bool)
    inlier_count = int(inlier_flags.sum())
    if not inlier_count:
        return 0.0, 0

    inlier_distances = np.asarray(
        [item.distance for item, keep in zip(good, inlier_flags) if keep],
        dtype=np.float32,
    )
    quantity = min(inlier_count / 30.0, 1.0)
    consistency = inlier_count / len(good)
    descriptor_quality = max(0.0, 1.0 - float(np.median(inlier_distances)) / 96.0)
    score = 0.45 * quantity + 0.35 * consistency + 0.20 * descriptor_quality
    return float(np.clip(score, 0.0, 1.0)), inlier_count


def enrol(user_id: str, display_name: str, image_bytes: bytes,
          finger_label: str = "primary") -> Enrolment:
    user_id = user_id.strip()
    display_name = display_name.strip()
    finger_label = finger_label.strip()
    if not user_id or not display_name:
        raise ValueError("User ID and display name are required.")
    if len(user_id) > 32 or len(display_name) > 120 or len(finger_label) > 50:
        raise ValueError("Enrolment information is too long.")

    template = _extract_template(image_bytes)
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
    filename = f"{user_id}.npz"
    (ENROL_DIR / filename).write_bytes(_serialize(template))
    entry.template_path = filename
    index = _load_local_index()
    index[user_id] = entry
    _save_local_index(index)
    return entry


def _entries_with_templates() -> list[tuple[Enrolment, FingerprintTemplate]]:
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
        template = _deserialize((ENROL_DIR / entry.template_path).read_bytes())
        result.append((entry, template))
    return result


def match(image_bytes: bytes) -> MatchResult:
    candidate = _extract_template(image_bytes)
    entries = _entries_with_templates()
    scored = sorted(
        (
            (entry, *_feature_score(candidate, template))
            for entry, template in entries
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    ranked = [(entry.user_id, score) for entry, score, _ in scored]
    if not scored:
        return MatchResult(
            matched=False, user_id=None, display_name=None, score=0.0,
            threshold=MATCH_THRESHOLD, ranked=[], reason="no_enrolments",
        )

    best, best_score, best_inliers = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0.0
    margin = best_score - second_score
    meets_quality = (
        best_score >= MATCH_THRESHOLD and best_inliers >= MIN_GEOMETRIC_INLIERS
    )
    if meets_quality and margin >= MATCH_MARGIN:
        return MatchResult(
            matched=True,
            user_id=best.user_id,
            display_name=best.display_name,
            score=best_score,
            threshold=MATCH_THRESHOLD,
            ranked=ranked,
            reason="matched",
            second_score=second_score,
            margin=margin,
        )
    reason = "ambiguous" if meets_quality and margin < MATCH_MARGIN else "below_threshold"
    return MatchResult(
        matched=False,
        user_id=None,
        display_name=None,
        score=best_score,
        threshold=MATCH_THRESHOLD,
        ranked=ranked,
        reason=reason,
        second_score=second_score,
        margin=margin,
    )


def migrate_local_enrolments() -> int:
    """Copy current feature templates into configured Supabase storage."""
    if not using_supabase():
        raise RuntimeError("Configure DATABASE_URL before migrating enrolments.")
    index = _load_local_index()
    migrated = 0
    with _database_connection() as connection:
        for entry in index.values():
            path = ENROL_DIR / entry.template_path
            if not path.is_file():
                continue
            payload = path.read_bytes()
            _deserialize(payload)
            connection.execute(
                f"INSERT INTO {SCHEMA}.enrolments "
                "(user_id, display_name, finger_label, template, enrolled_at) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING",
                (entry.user_id, entry.display_name, entry.finger_label,
                 payload, entry.enrolled_at),
            )
            migrated += 1
    return migrated
