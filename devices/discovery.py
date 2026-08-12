"""LAN discovery for NVRs and IP cameras.

Two complementary strategies are used:

1. ONVIF WS-Discovery — a UDP multicast probe that most TP-Link VIGI, Hikvision
   and Dahua devices answer with their service address. This requires the host
   to be on the same broadcast domain as the device (see `network_mode: host`
   in docker-compose).
2. TCP port scan — a fallback that sweeps the local subnet for the ports NVRs
   typically expose. Useful when multicast is blocked by the network.
"""

import ipaddress
import logging
import re
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

WS_DISCOVERY_ADDRESS = "239.255.255.250"
WS_DISCOVERY_PORT = 3702

# Ports commonly exposed by NVRs / IP cameras, mapped to a readable role.
CANDIDATE_PORTS = {
    80: "management",
    443: "https",
    554: "rtsp",
    2020: "service",
    8000: "service",
    8080: "management",
    8443: "https",
    8899: "openapi",
    20443: "openapi",
    37777: "service",
}

WS_DISCOVERY_PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>uuid:{message_id}</w:MessageID>
    <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </e:Body>
</e:Envelope>"""


def get_local_networks():
    """Return the IPv4 networks this host appears to be attached to."""
    networks = []
    for _, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        ip = sockaddr[0]
        if ip.startswith("127."):
            continue
        try:
            networks.append(ipaddress.ip_network(f"{ip}/24", strict=False))
        except ValueError:
            continue

    if not networks:
        # Fall back to asking the kernel which interface routes to the internet.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            networks.append(ipaddress.ip_network(f"{ip}/24", strict=False))
        except OSError as exc:
            logger.warning("Could not determine local network: %s", exc)
        finally:
            sock.close()

    return list(dict.fromkeys(networks))


def probe_onvif(timeout=4):
    """Send a WS-Discovery probe and collect responding devices.

    Returns a dict keyed by IP address with the discovered service URLs.
    """
    message = WS_DISCOVERY_PROBE.format(message_id=uuid.uuid4())
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(timeout)

    found = {}
    try:
        sock.sendto(message.encode(), (WS_DISCOVERY_ADDRESS, WS_DISCOVERY_PORT))
        while True:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                break
            ip = addr[0]
            body = data.decode(errors="replace")
            entry = found.setdefault(ip, {"ip_address": ip, "source": "onvif", "service_urls": []})
            for url in re.findall(r"https?://[^\s<>\"]+", body):
                if url not in entry["service_urls"]:
                    entry["service_urls"].append(url)
    except OSError as exc:
        logger.warning("ONVIF discovery failed: %s", exc)
    finally:
        sock.close()

    return found


def _check_port(ip, port, timeout=0.4):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((str(ip), port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def scan_subnet(network, ports=None, timeout=0.4, max_workers=128):
    """Scan a network for hosts exposing NVR-like ports."""
    ports = ports or list(CANDIDATE_PORTS)
    hosts = list(ipaddress.ip_network(network, strict=False).hosts())
    targets = [(ip, port) for ip in hosts for port in ports]

    found = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = pool.map(lambda t: (t, _check_port(t[0], t[1], timeout)), targets)
        for (ip, port), is_open in results:
            if not is_open:
                continue
            key = str(ip)
            entry = found.setdefault(
                key, {"ip_address": key, "source": "scan", "open_ports": [], "service_urls": []}
            )
            entry["open_ports"].append(port)

    return found


def discover_devices(network=None, onvif_timeout=4, scan_timeout=0.4, include_scan=True):
    """Discover NVRs / cameras on the LAN.

    Combines ONVIF WS-Discovery with an optional TCP port sweep and returns a
    list of dicts sorted by IP address, each with suggested port values ready
    to prefill the NVR form.
    """
    devices = probe_onvif(timeout=onvif_timeout)

    if include_scan:
        networks = [network] if network else get_local_networks()
        for net in networks:
            for ip, entry in scan_subnet(net, timeout=scan_timeout).items():
                if ip in devices:
                    existing = devices[ip]
                    existing["open_ports"] = entry["open_ports"]
                    existing["source"] = "onvif+scan"
                else:
                    devices[ip] = entry

    results = []
    for entry in devices.values():
        entry.setdefault("open_ports", [])
        entry.setdefault("service_urls", [])
        entry["suggested"] = suggest_ports(entry["open_ports"])
        results.append(entry)

    results.sort(key=lambda d: ipaddress.ip_address(d["ip_address"]))
    return results


def suggest_ports(open_ports):
    """Map open ports onto the NVR model's port fields."""
    suggested = {}
    for port in sorted(open_ports):
        role = CANDIDATE_PORTS.get(port)
        if not role:
            continue
        field = {
            "management": "management_port",
            "https": "https_port",
            "rtsp": "rtsp_port",
            "service": "service_port",
            "openapi": "openapi_port",
        }[role]
        suggested.setdefault(field, port)
    return suggested
