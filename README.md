# Ride Security

Local-first Django app to discover, manage, and stream IP cameras and NVRs over the LAN.

## What it does

- Discovers NVRs and cameras on your local network.
- Lets you add and manage NVRs, cameras, and sensors from the web UI.
- Pulls snapshots and live video from the NVR so you can view it in a browser.
- Runs on a local server with Docker.
- Cloudflare Tunnel is optional; you can enable it later for remote access.

## Requirements

- Docker and Docker Compose installed.
- Linux/macOS machine on the same LAN as the NVR.
- The NVR and cameras must be reachable from the server.

## Quick start — local only

1. Clone or copy the project to the server.
2. Copy `.env.example` to `.env` and set a real `DJANGO_SECRET_KEY`:

   ```bash
   cp .env.example .env
   ```

3. Start the stack:

   - On a **Linux server** with the NVR on the same LAN:

     ```bash
     ./docker/local
     ```

     This uses host networking, which lets the discovery scanner see the real LAN.

   - On **macOS Docker Desktop** (host networking is not available):

     ```bash
     docker compose up -d
     ```

     Then open `http://localhost:8000`.

4. Log in with the default admin user created by `./entrypoint.sh`.

## Finding the CIDR

When you click **Discover on LAN**, the app asks for a **Network (CIDR)**.

CIDR is a way of describing an IP range. A home network usually looks like this:

```text
192.168.0.0/24
192.168.1.0/24
192.168.68.0/24
```

The `/24` means the network uses the first three numbers as the network part and the last number for each device.

### Where to find it

**Option 1 — look at your own IP**

On macOS run:

```bash
ifconfig | grep "inet 192.168" | head -n 1
```

If your IP is `192.168.68.123`, the CIDR is `192.168.68.0/24`.

On Linux run:

```bash
ip -4 route show | grep "src" | head -n 1
```

**Option 2 — check the router app**

- Open the **TP-Link Deco** app.
- Look for a connected device like the NVR or your PC.
- It will show the IP, for example `192.168.68.101`.
- Replace the last number with `0/24`.

**Option 3 — the app will try to detect it**

If you leave the CIDR field blank and click **Scan network**, the app tries to detect the local network. On a Linux server with host networking this usually works. On Docker Desktop for macOS it may only see the Docker internal network, so entering the CIDR manually is more reliable.

## Discovering the NVR

1. Go to **NVRs → Discover on LAN**.
2. Enter the CIDR or leave it blank.
3. Click **Scan network**.
4. When the NVR appears, click **Add as NVR**.
5. Fill in the username and password the NVR uses for RTSP streams.

You can also run discovery from the command line:

```bash
docker compose run --rm --entrypoint python web manage.py discover_nvrs --network 192.168.68.0/24
```

## Adding and managing devices

From the web UI you can:

- Add NVRs manually or from discovery.
- Add cameras and link them to an NVR.
- Add sensors for motion, doors, smoke, etc.
- Edit or remove any entity.

For VIGI NVRs, the RTSP and snapshot URLs are built from the NVR IP, channel, and credentials.

## Motion capture and face recognition

The app can watch a camera's RTSP stream, capture a frame when something moves, and tell
you whether that frame is good enough for face recognition.

### 1. Start the watcher

```bash
docker compose --profile motion up -d
```

That starts three workers: `watch_motion` (detects motion, saves frames),
`process_events --loop` (runs face detection on saved frames) and `cleanup_events
--loop` (purges confirmed no-face captures every 6 hours). Detection and analysis are
separate on purpose — recognition takes hundreds of milliseconds and must not stall
detection.

To watch a single camera with custom sensitivity:

```bash
docker compose run --rm --entrypoint python web manage.py watch_motion \
  --camera 1 --min-area 0.01 --cooldown 30
```

- `--min-area` — fraction of the frame that must change (default `0.005`). Raise it if
  you get false triggers from trees or shadows.
- `--cooldown` — seconds between captures per camera (default `15`).
- `--sample-fps` — frames analysed per second (default `5`). Lower it to save CPU.

### 2. Review the captures

Open **Events** in the sidebar. Each row shows the frame, how much of it changed, how many
faces were found, the largest face in pixels, a sharpness score, and a verdict:

| Verdict | Meaning |
| --- | --- |
| Usable for recognition | A face at least 80px on its short side and sharp enough to embed |
| Person seen, no usable face | A human shape was detected but no face — back turned, cap, too far. Kept for review, never auto-cleaned |
| Face too small | Detected, but too few pixels — move closer, zoom, or raise resolution |
| Face too blurry | Motion blur or focus problem — needs faster shutter or more light |
| No face detected | Motion happened, but nothing person-shaped was in frame |

Detection runs OpenCV first and falls back to MTCNN when it misses (profiles, caps,
small faces). Person detection unions MobileNet-SSD and HOG pedestrian detectors —
it is deliberately permissive, so the occasional wall or shadow may be marked as a
person. That bias is on purpose: it only decides which captures are *kept*, never
who someone is.

### 3. Confirm verdicts and reclaim storage

A no-face frame is only ever deleted after *you* agree with the verdict — nothing is
purged automatically on the system's word alone.

- On a capture's detail page, click **Confirm verdict** (or **Withdraw confirmation** to
  keep it).
- On the Events list, **Confirm all "no face"** confirms every analyzed no-face verdict
  at once, and **Clean up now** runs the purge immediately instead of waiting for the
  next pass.
- The `cleanup` worker repeats the purge every 6 hours. Confirmed no-face events lose
  both their database row and their stored frame. Re-analyzing an event clears its
  confirmation, so a changed verdict is never deleted on a stale one.

Run it by hand with:

```bash
docker compose run --rm --entrypoint python web manage.py cleanup_events --dry-run
```

### 4. Test without waiting for motion

Open a camera and click **Capture & analyze**. That grabs a frame now and analyses it
immediately, which is the quickest way to check whether a camera's framing and lighting
can support recognition at all.

The 80px and sharpness-40 thresholds live in `devices/face_recognition.py`
(`MIN_FACE_PX`, `MIN_FACE_SHARPNESS`) and are starting heuristics — expect to calibrate
them against your own cameras.

## The face database

You do not have to enroll anyone up front. The system builds its own database of faces
from what the cameras see, then asks you who they are.

### How a face becomes a person

1. A capture contains a face that matches nobody in the database.
2. If that face is good enough to recognise later (at least 80px, sharp), it is stored as
   a **candidate**.
3. When the *same* face turns up in a second, separate capture, the candidate is promoted
   to an unnamed **person** and you get a notification.
4. You open the notification and give them a name. Every later sighting is labelled.

The two-sighting rule is deliberate. A single detection is not evidence — one false
positive would otherwise leave a permanent phantom in your database. Candidates are
visible at the bottom of the **People** page with their progress (`1 / 2`).

### Notifications

The sidebar shows a **Notifications** item on every page with a count of unread messages.
Click it to read them inline, click a message to jump to what it is about, and it is
marked read. Naming a person also clears the prompt about them automatically.

### Naming and duplicates

Open **People** to see the database: faces needing a name first, then known people, then
pending candidates.

Expect duplicates. Face matching compares a face to a reference and accepts it below a
distance threshold, so the same person at a different angle or in different light will
sometimes read as a new identity. That is a property of the technique, not a bug we can
configure away — so the naming form doubles as a merge tool:

- **Give a name** — labels this identity.
- **Merge into an existing person** — moves this face's reference images and sightings
  onto someone already named, then deletes the duplicate.

You can also enroll someone manually from **People → Enroll manually** if you have a good
photo and do not want to wait for the cameras to find them.

Reference images are stored under `MEDIA_ROOT/persons/<code>/`, keyed on an immutable
identity code rather than the name, so renaming somebody never breaks their recognition.

## Enabling Cloudflare Tunnel later

When you want external access:

1. Get a token from your Cloudflare Zero Trust dashboard.
2. Set `TUNNEL_TOKEN` in `.env`.
3. Start with the tunnel profile:

   ```bash
   docker compose --profile tunnel up -d
   ```

## Architecture

```text

┌─────────────┐
│   Browser   │
└──────┬──────┘
       │
       ▼
┌─────────────────────┐
│  Django app (web)   │
│  runs in Docker     │
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│  NVR on the LAN     │
│  RTSP / HTTP        │
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│  Cameras (PoE/WiFi) │
└─────────────────────┘
```

- **Django** serves the web UI.
- **OpenCV** pulls frames from RTSP and detects motion.
- **DeepFace** detects and recognizes faces in captured frames.
- **Docker** containerizes the app.
- **Cloudflare Tunnel** is an optional add-on for remote access.

Detailed component, sequence, and class diagrams live in [`docs/architecture.md`](docs/architecture.md).

## Offline use

Once the Docker images and Python packages are cached on the server, you can stop, start, and use the app without internet. Only the Cloudflare Tunnel needs a live internet connection.
