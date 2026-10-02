import os
import unittest
from unittest.mock import patch

from demo_matcher import sensor


class SensorConfigurationTests(unittest.TestCase):
    def test_standalone_bridge_uses_separate_default_port(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MANTRA_BRIDGE_URL", None)
            self.assertEqual(sensor.bridge_url(), "http://127.0.0.1:8766")

    def test_configured_bridge_url_is_normalized(self):
        with patch.dict(
            os.environ,
            {"MANTRA_BRIDGE_URL": "http://127.0.0.1:9876/"},
        ):
            self.assertEqual(sensor.bridge_url(), "http://127.0.0.1:9876")


if __name__ == "__main__":
    unittest.main()
