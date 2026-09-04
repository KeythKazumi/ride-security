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
- **OpenCV** pulls frames from RTSP.
- **Docker** containerizes the app.
- **Cloudflare Tunnel** is an optional add-on for remote access.

## Offline use

Once the Docker images and Python packages are cached on the server, you can stop, start, and use the app without internet. Only the Cloudflare Tunnel needs a live internet connection.
