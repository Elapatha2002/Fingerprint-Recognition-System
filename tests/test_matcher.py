"""Regression checks for the independent matcher without biometric fixtures."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from demo_matcher import matcher


def image_bytes(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(array.astype(np.uint8), mode="L").save(buffer, format="PNG")
    return buffer.getvalue()


def fingerprint_pattern(seed: int) -> np.ndarray:
    """Create a feature-rich synthetic ridge pattern without biometric data."""
    random = np.random.default_rng(seed)
    pattern = np.full((360, 320), 245, dtype=np.uint8)
    centre = (160 + (seed % 3) * 3, 210)
    for radius in range(25, 145, 7):
        start = int(random.integers(0, 25))
        end = int(random.integers(330, 360))
        colour = int(30 + random.integers(0, 30))
        cv2.ellipse(
            pattern, centre, (radius, int(radius * 1.2)),
            0, start, end, colour, 2,
        )
    for _ in range(45):
        point = (int(random.integers(45, 275)), int(random.integers(65, 335)))
        cv2.circle(pattern, point, int(random.integers(1, 3)), 30, -1)
    return cv2.GaussianBlur(pattern, (3, 3), 0.5)


class MatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patchers = [
            patch.object(matcher, "DATABASE_URL", ""),
            patch.object(matcher, "ENROL_DIR", root),
            patch.object(matcher, "INDEX_PATH", root / "index.json"),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_enrol_and_identify_same_pattern(self):
        pattern = fingerprint_pattern(1)
        matcher.enrol("USR-001", "Test Person", image_bytes(pattern), "Right index")
        result = matcher.match(image_bytes(pattern))
        self.assertTrue(result.matched)
        self.assertEqual(result.user_id, "USR-001")
        self.assertAlmostEqual(result.score, 1.0, places=5)

    def test_different_pattern_is_rejected(self):
        matcher.enrol("USR-001", "Test Person", image_bytes(fingerprint_pattern(1)))
        result = matcher.match(image_bytes(fingerprint_pattern(9)))
        self.assertFalse(result.matched)

    def test_small_rotation_and_translation_are_tolerated(self):
        pattern = fingerprint_pattern(3)
        transform = cv2.getRotationMatrix2D((160, 180), 6, 1.0)
        transform[:, 2] += (7, -5)
        moved = cv2.warpAffine(pattern, transform, (320, 360), borderValue=245)
        matcher.enrol("USR-001", "Test Person", image_bytes(pattern))
        result = matcher.match(image_bytes(moved))
        self.assertTrue(result.matched)
        self.assertEqual(result.user_id, "USR-001")

    def test_duplicate_templates_are_rejected_as_ambiguous(self):
        pattern = image_bytes(fingerprint_pattern(5))
        matcher.enrol("USR-001", "One", pattern)
        matcher.enrol("USR-002", "Two", pattern)
        result = matcher.match(pattern)
        self.assertFalse(result.matched)
        self.assertEqual(result.reason, "ambiguous")
        self.assertAlmostEqual(result.margin, 0.0)

    def test_legacy_template_requires_reenrolment(self):
        payload = io.BytesIO()
        np.save(payload, np.zeros(matcher.LEGACY_TEMPLATE_SIZE, dtype=np.float32))
        with self.assertRaisesRegex(matcher.LegacyTemplateError, "re-enrol"):
            matcher._deserialize(payload.getvalue())

    def test_delete_and_clear(self):
        matcher.enrol("USR-001", "One", image_bytes(fingerprint_pattern(2)))
        matcher.enrol("USR-002", "Two", image_bytes(fingerprint_pattern(8)))
        self.assertTrue(matcher.delete_enrolment("USR-001"))
        self.assertEqual(matcher.clear_all(), 1)
        self.assertEqual(matcher.count_enrolled(), 0)

    def test_remote_database_without_sslmode_enforces_tls(self):
        url = "postgresql://runtime:secret@pooler.example.com:5432/postgres"
        self.assertEqual(matcher._remote_ssl_kwargs(url), {"sslmode": "require"})

    def test_remote_database_keeps_secure_sslmode(self):
        url = (
            "postgresql://runtime:secret@pooler.example.com:5432/postgres"
            "?sslmode=verify-full"
        )
        self.assertEqual(matcher._remote_ssl_kwargs(url), {})

    def test_remote_database_rejects_explicit_insecure_sslmode(self):
        url = (
            "postgresql://runtime:secret@pooler.example.com:5432/postgres"
            "?sslmode=disable"
        )
        with self.assertRaisesRegex(ValueError, "sslmode=require"):
            matcher._remote_ssl_kwargs(url)

    def test_local_database_does_not_force_tls(self):
        url = "postgresql://runtime:secret@localhost:5432/postgres"
        self.assertEqual(matcher._remote_ssl_kwargs(url), {})


if __name__ == "__main__":
    unittest.main()
