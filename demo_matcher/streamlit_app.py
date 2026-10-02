"""Hosted/local standalone fingerprint recognition demonstration."""
from __future__ import annotations

import html
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import streamlit as st  # noqa: E402

from demo_matcher import auth, matcher  # noqa: E402
from demo_matcher.sensor import (  # noqa: E402
    SensorError, bridge_url, capture_fingerprint, capture_transport,
    from_bridge_payload, is_available,
)


st.set_page_config(
    page_title="Fingerprint Recognition Demonstration",
    page_icon="🔎",
    layout="wide",
    initial_sidebar_state="collapsed",
)


CSS = """
<style>
  :root { color-scheme: dark; }
  .block-container { max-width: 1180px; padding-top: 2rem; }
  .frs-header { display:flex; justify-content:space-between; align-items:flex-start;
    gap:24px; margin-bottom:24px; }
  .frs-title { font-size:30px; font-weight:750; letter-spacing:-.02em; }
  .frs-subtitle { color:#94a3b8; margin-top:5px; }
  .frs-badge { border:1px solid #334155; border-radius:999px; padding:7px 11px;
    color:#cbd5e1; font-size:12px; white-space:nowrap; }
  .frs-section { color:#94a3b8; font-size:12px; font-weight:700;
    letter-spacing:.08em; text-transform:uppercase; margin:4px 0 12px; }
  .frs-chip { display:inline-block; padding:8px 13px; border-radius:8px;
    color:#93c5fd; background:#3b82f61f; border:1px solid #3b82f64d;
    font-family:ui-monospace,monospace; font-weight:650; }
  .frs-empty { border:1px dashed #334155; border-radius:12px; padding:48px 24px;
    text-align:center; color:#94a3b8; }
  .frs-result { border-radius:12px; padding:22px 24px; margin-top:18px; }
  .frs-match { background:#14532d; border-left:4px solid #22c55e; }
  .frs-no-match { background:#7f1d1d; border-left:4px solid #ef4444; }
  .frs-result-name { color:white; font-size:25px; font-weight:750; margin:5px 0; }
  .frs-result-meta { color:#e2e8f0; font:12px ui-monospace,monospace; }
  [data-testid="stForm"] { border-color:#334155; }
</style>
"""


def _login_gate() -> bool:
    if st.session_state.get("frs_authenticated"):
        return True
    expected = auth.configured_hash()
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown("<div style='max-width:420px;margin:10vh auto 0;'>", unsafe_allow_html=True)
    st.title("Fingerprint Recognition")
    st.caption("Private demonstration access")
    if not expected:
        st.error("Access password is not configured. Run setup_supabase.py first.")
        return False
    with st.form("login_form"):
        password = st.text_input("Access password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
    if submitted:
        if auth.verify_password(password, expected):
            st.session_state.clear()
            st.session_state["frs_authenticated"] = True
            st.rerun()
        st.error("Incorrect access password.")
    st.markdown("</div>", unsafe_allow_html=True)
    return False


def _header() -> None:
    backend = "Supabase" if matcher.using_supabase() else "Local rehearsal"
    st.markdown(CSS, unsafe_allow_html=True)
    left, right = st.columns([5, 1])
    with left:
        st.markdown(
            "<div class='frs-header'><div><div class='frs-title'>"
            "Fingerprint Recognition Demonstration</div>"
            "<div class='frs-subtitle'>1:N identification without spoof detection</div>"
            f"</div><span class='frs-badge'>{backend}</span></div>",
            unsafe_allow_html=True,
        )
    with right:
        if st.button("Sign out", use_container_width=True):
            st.session_state.clear()
            st.rerun()
    st.info(
        "This matcher does not determine whether a fingerprint is live or spoofed. "
        "That limitation is the purpose of this demonstration.",
        icon="ℹ️",
    )


def _preview(capture) -> None:
    if not capture:
        st.markdown("<div class='frs-empty'>Awaiting fingerprint capture</div>",
                    unsafe_allow_html=True)
        return
    st.image(capture.pil_image(), width=280)
    st.caption(
        f"{capture.width} × {capture.height} px · {capture.dpi} DPI · "
        f"quality {capture.quality if capture.quality is not None else 'not reported'}"
    )


def _capture_widget(state_key: str, component_key: str) -> tuple[object | None, bool]:
    """Render direct or hosted capture and return (capture, newly_captured)."""
    captured = st.session_state.get(state_key)
    if capture_transport() == "bridge":
        from demo_matcher.components.mantra_capture import render_mantra_capture

        payload = render_mantra_capture(bridge_url=bridge_url(), key=component_key)
        if payload and payload.get("ok"):
            capture_id = payload.get("capture_id")
            seen_key = f"{state_key}_capture_id"
            if capture_id and capture_id != st.session_state.get(seen_key):
                try:
                    captured = from_bridge_payload(payload)
                    st.session_state[state_key] = captured
                    st.session_state[seen_key] = capture_id
                    return captured, True
                except SensorError as error:
                    st.error(str(error))
        return captured, False

    available, message = is_available()
    st.caption(message)
    if st.button("Capture fingerprint", type="primary", key=component_key,
                 use_container_width=True, disabled=not available):
        try:
            with st.spinner("Scanner started — place one finger flat on the sensor."):
                captured = capture_fingerprint()
            st.session_state[state_key] = captured
            return captured, True
        except SensorError as error:
            st.error(f"Sensor error: {error}")
    return captured, False


def _enrol_tab() -> None:
    left, right = st.columns([1, 1], gap="large")
    with left:
        st.markdown("<div class='frs-section'>New enrolment</div>", unsafe_allow_html=True)
        user_id = matcher.next_user_id()
        st.markdown(f"<span class='frs-chip'>{html.escape(user_id)}</span>",
                    unsafe_allow_html=True)
        name = st.text_input("Full name", key="enrol_name")
        finger = st.selectbox(
            "Finger being enrolled",
            ["Right index", "Left index", "Right thumb", "Left thumb",
             "Right middle", "Left middle"],
            key="enrol_finger",
        )
        capture, _ = _capture_widget("enrol_capture", "enrol_sensor")
        if st.button("Save enrolment", type="primary", use_container_width=True,
                     disabled=not bool(capture and name.strip())):
            try:
                entry = matcher.enrol(user_id, name, capture.image_bytes, finger)
                st.session_state.pop("enrol_capture", None)
                st.success(f"Enrolled {entry.display_name} as {entry.user_id}.")
                st.rerun()
            except Exception as error:
                st.error(f"Enrolment failed: {error}")
    with right:
        st.markdown("<div class='frs-section'>Captured fingerprint</div>",
                    unsafe_allow_html=True)
        _preview(st.session_state.get("enrol_capture"))


def _identify_tab() -> None:
    try:
        enrolment_count = matcher.count_enrolled()
    except Exception as error:
        st.error(f"Could not read the enrolment directory: {error}")
        return
    if enrolment_count == 0:
        st.markdown("<div class='frs-empty'>Enroll at least one person first.</div>",
                    unsafe_allow_html=True)
        return

    left, right = st.columns([1, 1], gap="large")
    with left:
        st.markdown("<div class='frs-section'>Identification capture</div>",
                    unsafe_allow_html=True)
        capture, is_new = _capture_widget("identify_capture", "identify_sensor")
        if is_new:
            try:
                st.session_state["identify_result"] = matcher.match(capture.image_bytes)
            except Exception as error:
                st.error(f"Identification failed: {error}")
    with right:
        st.markdown("<div class='frs-section'>Captured fingerprint</div>",
                    unsafe_allow_html=True)
        _preview(st.session_state.get("identify_capture"))

    result = st.session_state.get("identify_result")
    if not result:
        return
    if result.matched:
        name = html.escape(result.display_name or "Unknown")
        user_id = html.escape(result.user_id or "")
        st.markdown(
            "<div class='frs-result frs-match'><div>Match found</div>"
            f"<div class='frs-result-name'>{name}</div>"
            f"<div class='frs-result-meta'>user_id = {user_id} · "
            f"similarity = {result.score:.3f} · threshold = {result.threshold:.2f}</div></div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div class='frs-result frs-no-match'><div>No match</div>"
            "<div class='frs-result-name'>Identity not recognized</div>"
            f"<div class='frs-result-meta'>highest similarity = {result.score:.3f} · "
            f"threshold = {result.threshold:.2f}</div></div>",
            unsafe_allow_html=True,
        )
    with st.expander("Similarity against every enrolled person"):
        for user_id, score in result.ranked:
            st.write(f"{user_id}: {score:.3f}")


def _directory_tab() -> None:
    try:
        enrolments = matcher.list_enrolments()
    except Exception as error:
        st.error(f"Could not read the enrolment directory: {error}")
        return
    if not enrolments:
        st.markdown("<div class='frs-empty'>No enrolled people.</div>",
                    unsafe_allow_html=True)
        return
    st.caption(f"{len(enrolments)} enrolled person(s)")
    for entry in enrolments:
        with st.container(border=True):
            columns = st.columns([2, 1.5, 1.5, 1])
            columns[0].write(f"**{entry.display_name}**  \n`{entry.user_id}`")
            columns[1].write(entry.finger_label)
            columns[2].write(entry.enrolled_at.replace("T", " ")[:19])
            if columns[3].button("Remove", key=f"remove_{entry.user_id}"):
                matcher.delete_enrolment(entry.user_id)
                st.rerun()
    with st.expander("Danger zone"):
        st.warning("This permanently removes all enrolled templates.")
        confirmation = st.text_input("Type DELETE ALL", key="delete_confirmation")
        if st.button("Clear directory", disabled=confirmation != "DELETE ALL"):
            removed = matcher.clear_all()
            st.warning(f"Removed {removed} enrolment(s).")
            st.rerun()


def main() -> None:
    if not _login_gate():
        return
    _header()
    enrol, identify, directory = st.tabs(["Enroll", "Identify", "Directory"])
    with enrol:
        _enrol_tab()
    with identify:
        _identify_tab()
    with directory:
        _directory_tab()


main()
