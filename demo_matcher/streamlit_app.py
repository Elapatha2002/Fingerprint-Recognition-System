"""Hosted/local standalone fingerprint recognition demonstration."""
from __future__ import annotations

import html
import hashlib
import secrets
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

import streamlit as st  # noqa: E402

from demo_matcher import auth, matcher, sdk_matcher  # noqa: E402
from demo_matcher.sensor import (  # noqa: E402
    SensorError, bridge_url, capture_fingerprint, capture_transport,
    from_bridge_payload, is_available, match_templates,
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
  .block-container { max-width: 1180px; padding-top: 5rem; }
  .st-key-frs_sign_out { display:flex; justify-content:flex-end; }
  .st-key-frs_sign_out button { min-height:42px; margin:0; white-space:nowrap; }
  .frs-header { display:flex; justify-content:space-between; align-items:flex-start;
    gap:24px; margin-bottom:24px; }
  .frs-title { font-size:30px; font-weight:750; letter-spacing:-.02em; }
  .frs-subtitle { color:#94a3b8; margin-top:5px; }
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
  .frs-ambiguous { background:#713f12; border-left:4px solid #f59e0b; }
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
    st.markdown(CSS, unsafe_allow_html=True)
    left, right = st.columns([5, 1])
    with left:
        st.markdown(
            "<div class='frs-header'><div><div class='frs-title'>"
            "Fingerprint Recognition Demonstration</div>"
            "<div class='frs-subtitle'>1:N identification without spoof detection</div>"
            "</div></div>",
            unsafe_allow_html=True,
        )
    with right:
        if st.button("Sign out", key="frs_sign_out", use_container_width=True):
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


def _capture_widget(state_key: str, component_key: str, matching=None) -> tuple[object | None, bool]:
    """Render direct or hosted capture and return (capture, newly_captured)."""
    captured = st.session_state.get(state_key)
    if capture_transport() == "bridge":
        from demo_matcher.components.mantra_capture import render_mantra_capture

        payload = render_mantra_capture(bridge_url=bridge_url(), key=component_key, matching=matching)
        if payload and not payload.get("ok"):
            st.session_state.pop(state_key, None)
            st.session_state.pop(f"{state_key}_match_result", None)
            if state_key == "identify_capture":
                st.session_state.pop("identify_result", None)
            if payload.get("error"):
                st.error(payload["error"])
            return None, False
        if payload and payload.get("ok"):
            capture_id = payload.get("capture_id")
            seen_key = f"{state_key}_capture_id"
            if capture_id and capture_id != st.session_state.get(seen_key):
                try:
                    captured = from_bridge_payload(payload)
                    st.session_state[state_key] = captured
                    st.session_state[seen_key] = capture_id
                    st.session_state[f"{state_key}_match_result"] = payload.get("match_result")
                    return captured, True
                except SensorError as error:
                    st.session_state.pop(state_key, None)
                    if state_key == "identify_capture":
                        st.session_state.pop("identify_result", None)
                    st.error(str(error))
                    return None, False
        return captured, False

    available, message = is_available()
    st.caption(message)
    if st.button("Capture fingerprint", type="primary", key=component_key,
                 use_container_width=True, disabled=not available):
        st.session_state.pop(state_key, None)
        if state_key == "identify_capture":
            st.session_state.pop("identify_result", None)
        try:
            with st.spinner("Scanner started — place one finger flat on the sensor."):
                captured = capture_fingerprint()
                if matching:
                    st.session_state[f"{state_key}_match_result"] = match_templates(
                        captured.iso_template, matching["candidates"], matching["request_id"])
            st.session_state[state_key] = captured
            return captured, True
        except (SensorError, ValueError) as error:
            st.error(f"Sensor error: {error}")
            return None, False
    return captured, False


def _enrol_tab() -> None:
    left, right = st.columns([1, 1], gap="large")
    with left:
        st.markdown("<div class='frs-section'>New enrolment</div>", unsafe_allow_html=True)
        try:
            existing = matcher.list_enrolments()
            user_id = matcher.next_user_id()
        except Exception:
            st.error(
                "The enrolment database is unavailable. Check the Streamlit "
                "DATABASE_URL secret and the Supabase project status."
            )
            return
        choice = st.selectbox("Enrolment", ["New person"] + [e.user_id for e in existing],
                              format_func=lambda v: v if v == "New person" else
                              f"Recapture {v} — {next(e.display_name for e in existing if e.user_id == v)}")
        selected = next((e for e in existing if e.user_id == choice), None)
        if selected:
            user_id = selected.user_id
        capture_context = (choice, st.session_state.get("enrol_generation", 0))
        if st.session_state.get("enrol_context") != capture_context:
            st.session_state.pop("enrol_capture", None)
            st.session_state.pop("enrol_capture_capture_id", None)
            st.session_state["enrol_context"] = capture_context
        st.markdown(f"<span class='frs-chip'>{html.escape(user_id)}</span>",
                    unsafe_allow_html=True)
        name = st.text_input("Full name", value=selected.display_name if selected else "",
                             key=f"enrol_name_{choice}")
        finger_options = ["Right index", "Left index", "Right thumb", "Left thumb", "Right middle", "Left middle"]
        if selected and selected.finger_label not in finger_options:
            finger_options.append(selected.finger_label)
        finger = st.selectbox(
            "Finger being enrolled",
            finger_options,
            index=finger_options.index(selected.finger_label) if selected else 0,
            key=f"enrol_finger_{choice}",
        )
        capture, _ = _capture_widget("enrol_capture", f"enrol_sensor_{choice}_{capture_context[1]}")
        confirmed = not selected or st.checkbox("Replace this person's stored template with this new capture?", key=f"replace_{choice}")
        if st.button("Save enrolment", type="primary", use_container_width=True,
                     disabled=not bool(capture and name.strip() and confirmed)):
            try:
                entry = sdk_matcher.enrol(user_id, name, capture.iso_template, finger, replace=bool(selected))
                st.session_state.pop("enrol_capture", None)
                st.session_state["enrol_generation"] = capture_context[1] + 1
                st.session_state["enrol_notice"] = f"Enrolled {entry.display_name} as {entry.user_id} using Mantra ISO."
                st.rerun()
            except Exception as error:
                st.error(f"Enrolment failed: {error}")
    with right:
        st.markdown("<div class='frs-section'>Captured fingerprint</div>",
                    unsafe_allow_html=True)
        _preview(st.session_state.get("enrol_capture"))
    if st.session_state.get("enrol_notice"):
        st.success(st.session_state.pop("enrol_notice"))


def _identify_tab() -> None:
    try:
        current = sdk_matcher.gallery()
        cutoff = sdk_matcher.threshold()
    except Exception as error:
        st.error(f"Could not read the enrolment directory: {error}")
        return
    if current.legacy:
        st.warning(f"{len(current.legacy)} older ORB/correlation enrolment(s) cannot be matched by Mantra. "
                   "They have not been deleted. In Enroll, choose Recapture for each existing user.")
    if not current.candidates:
        st.markdown("<div class='frs-empty'>Enroll at least one person first.</div>",
                    unsafe_allow_html=True)
        return

    st.caption(f"Mantra MatchISO · SDK score threshold: {cutoff} (uncalibrated demonstration setting). "
               "Scores are not percentages. Multiple passing identities are rejected as ambiguous.")
    session_nonce = st.session_state.setdefault("sdk_session_nonce", secrets.token_hex(32))
    request_id = hashlib.sha256(f"{session_nonce}:{current.revision}:{cutoff}".encode()).hexdigest()
    if st.session_state.get("identify_directory") != request_id:
        st.session_state.pop("identify_result", None)
        st.session_state.pop("identify_capture", None)
        st.session_state["identify_directory"] = request_id
    task = {"request_id": request_id, "candidates": current.candidates}

    left, right = st.columns([1, 1], gap="large")
    with left:
        st.markdown("<div class='frs-section'>Identification capture</div>",
                    unsafe_allow_html=True)
        capture, is_new = _capture_widget("identify_capture", f"identify_sensor_{request_id}", task)
        if is_new:
            st.session_state.pop("identify_result", None)
            try:
                st.session_state["identify_result"] = sdk_matcher.decide(
                    st.session_state.get("identify_capture_match_result"), current, request_id)
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
            f"SDK score = {result.score:.0f} · threshold = {result.threshold:.0f}</div></div>",
            unsafe_allow_html=True,
        )
    elif result.reason == "ambiguous":
        st.markdown(
            "<div class='frs-result frs-ambiguous'><div>Ambiguous result</div>"
            "<div class='frs-result-name'>No identity assigned</div>"
            f"<div class='frs-result-meta'>best SDK score = {result.score:.0f} · "
            f"second = {result.second_score:.0f} · multiple identities meet threshold "
            f"{result.threshold:.0f}</div></div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div class='frs-result frs-no-match'><div>No match</div>"
            "<div class='frs-result-name'>Identity not recognized</div>"
            f"<div class='frs-result-meta'>highest SDK score = {result.score:.0f} · "
            f"threshold = {result.threshold:.0f}</div></div>",
            unsafe_allow_html=True,
        )
    with st.expander("Mantra SDK scores against eligible enrolments"):
        for user_id, score in result.ranked:
            st.write(f"{user_id}: {score:.0f}")


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
