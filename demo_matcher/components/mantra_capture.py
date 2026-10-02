"""Browser-side Streamlit component for the local MFS100 bridge."""
from pathlib import Path

import streamlit.components.v1 as components


FRONTEND = Path(__file__).with_name("mantra_capture_frontend")
component = components.declare_component(
    "fingerprint_recognition_mantra_capture", path=str(FRONTEND)
)


def render_mantra_capture(*, bridge_url: str, key: str):
    """Return a captured bridge payload while keeping its token in the browser."""
    return component(bridge_url=bridge_url, key=key, default=None)
