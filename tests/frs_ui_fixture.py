"""Isolated browser test app: synthetic metadata only; no database or SDK I/O.

Run only for tests, never use as the hosted entrypoint.
"""
import os
import runpy
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['DATABASE_URL']=''
os.environ['MANTRA_SENSOR_TRANSPORT']='bridge'
os.environ['FRS_MANTRA_MATCH_THRESHOLD']='1400'

import streamlit as st
from demo_matcher import matcher, sdk_matcher as sdk, auth
from test_sdk_matcher import template

matcher.DATABASE_URL=''
entry=matcher.Enrolment('USR-001','Synthetic Evaluator','test','2026-10-06','Right index')
matcher.list_enrolments=lambda:[entry]
matcher.next_user_id=lambda:'USR-002'
sdk.gallery=lambda:sdk.Gallery({'USR-001':entry},[{'user_id':'USR-001','template':template()}],[],'fixture')
auth.configured_hash=lambda:'configured-test-only'
if 'fixture_started' not in st.session_state:
    st.session_state['fixture_started']=True
    st.session_state['frs_authenticated']=True
# Signout clears fixture_started too; preserve test's logged-out mode via query.
if st.query_params.get('signed_out')=='1':st.session_state['frs_authenticated']=False
runpy.run_path(str(ROOT/'demo_matcher/streamlit_app.py'),run_name='__main__')
