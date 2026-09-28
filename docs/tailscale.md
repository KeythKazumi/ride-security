# Reaching every site with Tailscale

Ride Security runs on one server but has to talk to NVRs that live behind
different home modems. Each modem hands out private addresses
(`192.168.x.y`) that mean nothing outside its own LAN, so a server at one
residence has no route to an NVR at another — and your laptop has no route to
either once you leave the house.

Tailscale fixes this by putting every machine on one private overlay network
(a *tailnet*). Devices find each other through Tailscale's coordination
server, then talk **directly**, encrypted with WireGuard. Nothing is opened
on any modem and no traffic is relayed through a third party unless a direct
connection is impossible.

## 1. The topology

```text
   Residence A (home)                         Residence B (ride)
   LAN 192.168.15.0/24                        LAN 192.168.1.0/24
   ┌──────────────────────────┐               ┌──────────────────────────┐
   │ mihkaNVR  192.168.15.27  │               │ rideNVR   192.168.1.115  │
   │ home-server 192.168.15.31│               │ ride-server 192.168.1.x  │
   │   ├─ ride-security (app) │               │   (subnet router only)   │
   │   └─ tailscale           │               │   └─ tailscale           │
   │      --advertise-routes  │               │      --advertise-routes  │
   │        192.168.15.0/24   │               │        192.168.1.0/24    │
   └────────────┬─────────────┘               └────────────┬─────────────┘
                │                                          │
                └───────────────  tailnet  ────────────────┘
                                     │
                            ┌────────┴────────┐
                            │  Mac (anywhere) │
                            │  tailscale      │
                            └─────────────────┘
```

Three roles:

| Role | Machines | What Tailscale does for it |
|---|---|---|
| **Subnet router** | one Linux box per residence (`home-server`, `ride-server`) | Advertises its LAN so *other* tailnet members can reach every device on it — including the NVR, which never runs Tailscale itself. |
| **App host** | `home-server` (also a subnet router) | Runs `ride-security`. Reaches `mihkaNVR` locally and `rideNVR` through `ride-server`'s advertised route. |
| **Client** | your Mac | Reaches both servers (SSH, the web UI on `:8000`) and both NVRs from any network. |

The NVRs and cameras keep their normal LAN addresses. **No `NVR` record in
the database changes.**

## 2. One-time setup

You need to be *on the same LAN* as each server (or at its console) exactly
once to install Tailscale. After that everything is remote.

### 2.1 Create the tailnet

Sign up at <https://login.tailscale.com> with a Google/Microsoft/GitHub
account. The free "Personal" plan covers this deployment (up to 100 devices,
3 users).

### 2.2 Each Linux server (subnet router)

Run on **home-server** (Fedora):

```bash
curl -fsSL https://tailscale.com/install.sh | sh

# Let the box forward packets for its LAN.
echo 'net.ipv4.ip_forward = 1' | sudo tee /etc/sysctl.d/99-tailscale.conf
echo 'net.ipv6.conf.all.forwarding = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf
sudo sysctl -p /etc/sysctl.d/99-tailscale.conf

# Fedora ships firewalld; trust the tailscale interface so forwarded traffic is allowed.
sudo firewall-cmd --permanent --zone=trusted --add-interface=tailscale0
sudo firewall-cmd --reload

sudo tailscale up --ssh --advertise-routes=192.168.15.0/24 --hostname=home-server
```

It prints a login URL — open it on any device and approve. Then run the same
on **ride-server**, changing only the route and hostname:

```bash
sudo tailscale up --ssh --advertise-routes=192.168.1.0/24 --hostname=ride-server
```

Flags explained:

- `--ssh` — enables **Tailscale SSH**: authentication uses your tailnet
  identity, so no SSH keys or passwords, and it works even if `sshd` is
  firewalled. Regular `sshd` keeps working too.
- `--advertise-routes=<LAN>/24` — offer this LAN to the rest of the tailnet.
  Use the address range the modem actually hands out; check with `ip -4 addr`.
- `--hostname` — the name you will use everywhere (`ssh mihkahl@home-server`).

### 2.3 Approve the routes

Advertised routes are **off until approved**. In the admin console
(<https://login.tailscale.com/admin/machines>) open each server → the
**…** menu → *Edit route settings* → tick the subnet → Save.

While there, on each server's **…** menu choose **Disable key expiry**.
Otherwise the node logs out after 180 days and a headless server at another
house silently drops off the tailnet.

### 2.4 Your Mac

Install from <https://tailscale.com/download/mac> (App Store or standalone),
sign in with the same account. macOS accepts subnet routes automatically.

Enable **MagicDNS** and **HTTPS certificates** once under *DNS* in the admin
console so that names like `home-server` resolve everywhere.

## 3. Verify

From the Mac, on any network (phone hotspot is a good test):

```bash
tailscale status                       # both servers listed, "-" not "offline"
ping -c 2 home-server                  # tailnet address
ping -c 2 192.168.15.27                # mihkaNVR through home-server's route
ping -c 2 192.168.1.115                # rideNVR through ride-server's route
nc -zv 192.168.1.115 554               # RTSP port on the remote NVR
ssh mihkahl@home-server                # Tailscale SSH, no password prompt
open http://home-server:8000           # the app
```

From **home-server**, the check that matters for the app:

```bash
sudo tailscale up --accept-routes    # Linux does NOT accept routes by default
nc -zv 192.168.1.115 554             # ride NVR reachable from where the app runs
```

`--accept-routes` is what lets the app host use the *other* server's
advertised subnet. It is remembered; you only run it once.

## 4. How it is used day to day

### Running the app

Nothing changes. `./docker/start` on `home-server`; the containers reach
LAN and tailnet addresses through the host's routing table, so the
`motion` worker connects to `rtsp://…@192.168.1.115:554/…` exactly as it
does to the local NVR.

Add `rideNVR` in the UI with its **real LAN address** (`192.168.1.115`) and
the cameras by channel, the same as `mihkaNVR`.

### Reaching the UI from outside

`http://home-server:8000` from the Mac, wherever it is. This replaces the
need for Cloudflare Tunnel for personal use — the tunnel profile is still
there if you want a public URL for people who are not on the tailnet.

### Administering the servers

```bash
ssh mihkahl@home-server
ssh mihkahl@ride-server
```

Updating the deployed app after a push:

```bash
ssh mihkahl@home-server 'cd ~/ride-security \
  && curl -fsSL https://github.com/KeythKazumi/ride-security/archive/refs/heads/main.tar.gz | tar xz --strip-components=1 \
  && docker compose --profile motion build web \
  && docker compose --profile motion up -d'
```

### Adding a third residence

Repeat §2.2–2.3 on one Linux box there with its own `--advertise-routes`.
Then add its NVR in the UI. No changes to the app.

## 5. Things that will bite

**Two residences on the same LAN range.** Routers love `192.168.1.0/24` and
`192.168.0.0/24`. If two sites advertise the same subnet, Tailscale cannot
tell them apart and only one route works. Fix it at the router: change one
site's DHCP range (e.g. to `192.168.2.0/24`) and give the NVR its new
address. Today's sites (`192.168.15.x` and `192.168.1.x`) do not collide.

**Docker on the Mac cannot reach the LAN** even with Tailscale, because
macOS *Local Network* privacy blocks Docker Desktop from private addresses.
That is a macOS setting, not a Tailscale one. Run the app on the Linux
server; keep the Mac for editing code.

**Key expiry.** If a server disappears from `tailscale status` after months,
its key expired. Disable expiry per node (§2.3) or re-run `tailscale up` at
the console.

**Subnet router reboots.** `tailscaled` is enabled as a systemd service by
the installer and comes back on boot. Check with
`systemctl status tailscaled` if a route vanishes.

**Relayed connections.** `tailscale status` shows `relay "xxx"` when a direct
path is not possible (carrier-grade NAT on both ends). RTSP still works but
adds latency; `tailscale netcheck` shows why. Enabling UPnP on one modem
usually restores a direct path.

## 6. Security notes

- Everything on the tailnet is WireGuard-encrypted end to end. RTSP
  credentials that cross between residences never travel in the clear.
- Only devices signed into *your* Tailscale account can join. Remove a lost
  laptop from the admin console and it is out immediately.
- The NVRs remain unreachable from the public internet — no port forwarding
  is configured anywhere.
- Tailscale SSH checks identity against the tailnet; you can further restrict
  who may SSH where with ACLs in the admin console. The default policy allows
  all members to reach everything, which is fine for a single-operator setup.
