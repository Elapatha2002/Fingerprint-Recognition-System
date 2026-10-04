# Fingerprint Recognition Demonstration

This is a standalone viva demonstration. It is intentionally **not** a spoof
detection system and has no runtime connection to FSD-XAI.

The demonstration enrols a fingerprint and performs 1:N feature matching.
Its purpose is to show that an ordinary recognition workflow can
accept a sufficiently similar spoof of the enrolled finger, motivating the
need for presentation-attack detection.

## What is stored

- Captured raw images are kept only in the active Streamlit session.
- Enrolment creates a versioned ORB local-feature template. Raw fingerprint
  pixels are not stored in the template.
- With Supabase configured, the template and metadata are stored in the
  private `fingerprint_demo.enrolments` PostgreSQL table.
- Without Supabase, local rehearsal data is stored in
  `demo_matcher/enrolments`; this directory is ignored by Git.

Matching combines descriptor similarity with RANSAC geometric verification,
then rejects results that are too close to the runner-up. This improves
translation/rotation tolerance and prevents database-order tie resolution.
It remains a controlled demonstration, not an AFIS, identity product,
security control, or forensic identification method. Thresholds must be
validated on a representative genuine/impostor dataset before any operational
use.

## 1. Install locally

Open PowerShell in this folder:

```powershell
cd "D:\Projects\University\Fingerprint Recognition System"
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Create the new Supabase project

1. Create a new Supabase project dedicated to this demonstration.
2. Open **Connect** in that project.
3. Select the **Session pooler** connection on port `5432`.
4. Copy its administrator connection string.
5. Keep the database password private.

No Storage bucket is required: only feature templates are persisted, not
raw captures.

## 3. Provision the private schema

Run:

```powershell
.\.venv\Scripts\python.exe setup_supabase.py
```

Enter the new project's Session-pooler administrator connection, choose an
application access password of at least 12 characters, and type `SETUP` when
asked. The script:

- creates the private `fingerprint_demo` schema and `enrolments` table;
- creates a restricted `fingerprint_demo_runtime` database role;
- grants only the permissions required by the application;
- stores the restricted runtime URL and a password hash in `.env`;
- never stores the Supabase administrator password.

Only current feature templates can be copied from local storage after setup:

```powershell
.\.venv\Scripts\python.exe -c "from demo_matcher.matcher import migrate_local_enrolments; print(migrate_local_enrolments())"
```

The retired 128 x 128 correlation templates cannot be converted into reliable
local features. Clear those enrolments and recapture each enrolled finger.
Delete local files after confirming the Supabase copy if the participant did
not consent to continued local retention.

## 4. Run locally with the MFS100

1. Install the Mantra MFS100 driver/SDK on Windows.
2. Connect the scanner.
3. Close **MFS100 Test Application**; two programs cannot control the device.
4. Start the app:

```powershell
$env:MANTRA_SENSOR_TRANSPORT="direct"
.\.venv\Scripts\python.exe -m streamlit run demo_matcher/streamlit_app.py --server.port 8502
```

Open `http://localhost:8502`, sign in with the application access password,
then use **Enroll**, **Identify**, and **Directory**.

## 5. Put this project in its own GitHub repository

Never commit `.env` or anything in `demo_matcher/enrolments`.

The local repository and first commit are already prepared. Create an empty
GitHub repository named `fingerprint-recognition-demo`, then run:

```powershell
git status
git remote add origin https://github.com/YOUR_USERNAME/fingerprint-recognition-demo.git
git push -u origin main
```

Before committing, verify that neither `.env`, `index.json`, `.npy`, nor `.npz` files
appear in `git status`.

## 6. Host on Render

The included `render.yaml` describes a lightweight web service. Unlike
FSD-XAI, this project does not load PyTorch or XAI packages, so it is suitable
for an initial small demonstration deployment.

1. Open the Render dashboard.
2. Choose **New > Blueprint**.
3. Connect the new GitHub repository.
4. Select `render.yaml`.
5. Enter these secret values from local `.env` when Render asks:
   - `DATABASE_URL`
   - `FRS_ACCESS_PASSWORD_HASH`
6. Create the service and wait for `/_stcore/health` to become healthy.
7. Open the assigned HTTPS `onrender.com` address.

Do not upload the `.env` file. Render environment variables replace it.

## 7. Connect the locally attached sensor to the hosted page

The cloud server cannot access a USB device on the examiner's computer. The
browser therefore talks to the supplied loopback-only bridge.

On every Windows computer that uses the MFS100, keep a local copy of the
`tools/mantra_bridge` folder. Start the bridge with the exact hosted origin:

```powershell
powershell -ExecutionPolicy Bypass -File tools/mantra_bridge/start_bridge.ps1 `
  -HostedOrigin "https://YOUR-SERVICE.onrender.com"
```

Keep that window open. It displays a pairing code.

In the hosted app:

1. Open **Enroll** or **Identify** in current Edge or Chrome.
2. Enter the bridge pairing code.
3. Select **Connect**.
4. Allow local-network access if the browser requests it.
5. Select **Capture fingerprint** and place the finger on the scanner.

The bridge binds only to `127.0.0.1`, checks the exact hosted web origin and
requires the pairing code. It uses port `8766` so it can run beside the
FSD-XAI bridge on port `8765`. Never forward port `8766` through a router or expose
it through a public reverse proxy.

## Viva flow

1. Enrol a real finger.
2. Identify the same real finger and show the positive match.
3. Present a spoof made from the same finger.
4. Show that the recognition-only matcher can still return the enrolled user.
5. Explain that similarity is not evidence of liveness.
6. Demonstrate FSD-XAI separately as the PAD system.

Use the same named finger and obtain a clear, flat capture. The matcher permits
modest translation and rotation, but it is an ORB/RANSAC demonstration rather
than a production minutiae matcher.
