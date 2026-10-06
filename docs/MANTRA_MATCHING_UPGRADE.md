# Mantra matching upgrade — FRS only

FRS now captures ISO/IEC 19794-2:2005 fingerprint templates and calls the
installed Windows `MANTRA.MFS100.MatchISO(byte[], byte[], ref int)` method.
It does not use ORB similarity for identification and does not perform PAD.
FSD-XAI is unchanged.

## Deploy these changes

1. Commit and push the changed files in **Fingerprint Recognition System**,
   not the FSD-XAI repository. Let Streamlit Cloud rebuild the FRS app.
2. Keep the existing `DATABASE_URL` and `FRS_ACCESS_PASSWORD_HASH` secrets.
   No database migration or rerun of `setup_supabase.py` is necessary.
3. Set `MANTRA_SENSOR_TRANSPORT = "bridge"` in the hosted app's secrets.
   Set `FRS_MANTRA_MATCH_THRESHOLD = "1400"` if explicitly configuring the
   default demonstration cutoff. Do not use `0.55`: native SDK scores are
   different. Old `MATCH_THRESHOLD` / `MATCH_MARGIN` are unused by the new UI.
4. On the scanner PC, update the local repository too. Stop the old **FRS**
   bridge with Ctrl+C and restart from this repository:

   ```powershell
   cd "D:\Projects\University\Fingerprint Recognition System"
   powershell -ExecutionPolicy Bypass -File .\tools\mantra_bridge\start_bridge.ps1 `
     -HostedOrigin "https://fingerprint-recognition-system.streamlit.app"
   ```

5. Keep the bridge window open. Sign in, open **Enroll**, and Connect using
   the bridge's pairing code. Close the Mantra Test Application if it holds
   the scanner. The bridge continues to use port 8766, separate from FSD-XAI.
6. For each old user, choose **Recapture USR-...** in the Enrolment selector.
   Capture the same named genuine finger, tick the replacement confirmation,
   and Save enrolment. This keeps the user's ID and updates the stored template.
   Until recaptured, legacy enrolments are preserved and excluded from matching.
7. In **Identify**, capture again. FRS sends eligible templates to the paired
   local bridge for comparison and displays native SDK scores. The cloud does
   not load Windows DLLs. An old bridge is rejected with an upgrade message.

For local Windows use, `MANTRA_SENSOR_TRANSPORT=direct` invokes the same
32-bit helper through stdin without requiring the browser bridge.

## Interpretation and testing

- **1400 is a provisional demonstration setting, not a calibrated threshold or
  a claim of a vendor-certified operating point.** Keep it fixed while testing
  repeated genuine captures and unrelated fingers. Choose any later operating
  threshold from a separate validation set, not to force a spoof to pass.
- A match is reported only when exactly one enrolled identity meets the cutoff.
  Two or more passing identities produce **Ambiguous result**. An SDK error is
  an error, not a zero score. Below-threshold scores are not spoof verdicts.
- Test same-finger recaptures, unrelated fingers, duplicates, offline bridge,
  expired/incorrect pairing code, old bridge version, and old ORB enrolments.
- A matching SDK does not guarantee spoof acceptance or establish PAD accuracy.
  Record the actual outcome of genuine and spoof trials in the viva.
- The demonstration gallery is bounded to 100 eligible enrolments per request.
  It is not a production 1:N identification service.

## Data and trust boundaries

ISO templates are sensitive biometric data. They are kept in the existing
private PostgreSQL BYTEA column (or local rehearsal directory), in a versioned
envelope padded to retain the existing schema size constraint. Images remain
session-only. Old local template files are not automatically deleted during
recapture; retain or remove them according to participant consent.

Only the authenticated application session receives gallery templates. The
browser relays them to loopback, protected by an exact origin allowlist and
pairing token; templates are not placed in URLs or SDK command arguments, and
the bridge does not write captures/templates to disk. Never expose port 8766
publicly. Use only trusted evaluator PCs and participants' consent.

The browser and local PC are trusted parts of this private demonstration.
Server-side response checks detect stale/incomplete results, but do **not**
cryptographically attest a remote SDK execution. Do not use these results to
grant access, mark official attendance, or make forensic identity decisions.

Mantra's SDK overview: https://servico.mantratecapp.com/Home/SDK.html
Native API availability was checked against the installed SDK 9.0.2.5.
