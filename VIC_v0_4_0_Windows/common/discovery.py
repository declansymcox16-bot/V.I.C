from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

try:
    import psutil
except ImportError:  # setup GUI can still use the generic broadcast path
    psutil = None

DISCOVERY_MAGIC = b"VIC_DISCOVER_V1"
DISCOVERY_PRODUCT = "VIC Video Ingest Cluster"


def normalise_url(url: str) -> str:
    text = str(url or "").strip().rstrip("/")
    if text and "://" not in text:
        text = "http://" + text
    return text


def probe_dashboard(url: str, timeout: float = 1.0) -> dict[str, Any] | None:
    base = normalise_url(url)
    if not base:
        return None
    try:
        request = Request(
            base + "/api/discovery",
            headers={"User-Agent": "VIC-Discovery/1"},
        )
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("product") != DISCOVERY_PRODUCT:
            return None
        payload["url"] = base
        return payload
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return None


def _interface_networks() -> list[ipaddress.IPv4Network]:
    networks: list[ipaddress.IPv4Network] = []
    if psutil is not None:
        try:
            for entries in psutil.net_if_addrs().values():
                for entry in entries:
                    if entry.family != socket.AF_INET:
                        continue
                    address = str(entry.address or "")
                    if not address or address.startswith("127."):
                        continue
                    ip = ipaddress.ip_address(address)
                    if not ip.is_private:
                        continue
                    netmask = str(entry.netmask or "255.255.255.0")
                    try:
                        network = ipaddress.ip_network(
                            f"{address}/{netmask}", strict=False
                        )
                    except ValueError:
                        network = ipaddress.ip_network(
                            f"{address}/24", strict=False
                        )
                    # Avoid scanning huge corporate/VPN networks. VIC is meant
                    # to find nearby home/LAN workers, so cap discovery to /24.
                    if network.prefixlen < 24:
                        network = ipaddress.ip_network(
                            f"{address}/24", strict=False
                        )
                    networks.append(network)
        except Exception:
            pass
    if not networks:
        try:
            address = socket.gethostbyname(socket.gethostname())
            if address and not address.startswith("127."):
                networks.append(
                    ipaddress.ip_network(f"{address}/24", strict=False)
                )
        except OSError:
            pass
    unique: list[ipaddress.IPv4Network] = []
    seen: set[str] = set()
    for network in networks:
        key = str(network)
        if key not in seen:
            seen.add(key)
            unique.append(network)
    return unique


def udp_discover(
    discovery_port: int = 8766,
    timeout: float = 1.5,
) -> list[str]:
    results: list[str] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.25)
        targets = {"255.255.255.255"}
        for network in _interface_networks():
            targets.add(str(network.broadcast_address))
        for target in targets:
            try:
                sock.sendto(DISCOVERY_MAGIC, (target, discovery_port))
            except OSError:
                pass
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data, address = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                payload = json.loads(data.decode("utf-8"))
                if payload.get("product") != DISCOVERY_PRODUCT:
                    continue
                port = int(payload.get("port", 8765))
                url = f"http://{address[0]}:{port}"
                if url not in results:
                    results.append(url)
            except (ValueError, json.JSONDecodeError):
                continue
    finally:
        sock.close()
    return results


def subnet_scan(
    dashboard_port: int = 8765,
    timeout: float = 0.35,
) -> list[str]:
    candidates: list[str] = []
    addresses: list[str] = []
    for network in _interface_networks():
        addresses.extend(str(host) for host in network.hosts())
    addresses = list(dict.fromkeys(addresses))[:1024]

    def check(address: str) -> str | None:
        url = f"http://{address}:{dashboard_port}"
        return url if probe_dashboard(url, timeout=timeout) else None

    with ThreadPoolExecutor(max_workers=64) as executor:
        futures = {executor.submit(check, address): address for address in addresses}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception:
                result = None
            if result and result not in candidates:
                candidates.append(result)
    return candidates


def discover_dashboards(
    dashboard_port: int = 8765,
    discovery_port: int = 8766,
    include_scan: bool = True,
) -> list[dict[str, Any]]:
    urls = udp_discover(discovery_port=discovery_port)
    if include_scan and not urls:
        urls.extend(subnet_scan(dashboard_port=dashboard_port))
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        details = probe_dashboard(url, timeout=1.0)
        if details:
            found.append(details)
    return found


def start_discovery_responder(
    dashboard_port: int = 8765,
    discovery_port: int = 8766,
) -> threading.Thread:
    payload = json.dumps(
        {
            "product": DISCOVERY_PRODUCT,
            "hostname": socket.gethostname(),
            "port": int(dashboard_port),
        }
    ).encode("utf-8")

    def serve() -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", int(discovery_port)))
            while True:
                try:
                    data, address = sock.recvfrom(4096)
                    if data.strip() == DISCOVERY_MAGIC:
                        sock.sendto(payload, address)
                except OSError:
                    time.sleep(1)
        except OSError as exc:
            print("VIC network discovery responder could not start:", exc)
        finally:
            sock.close()

    thread = threading.Thread(
        target=serve,
        daemon=True,
        name="VIC-Network-Discovery",
    )
    thread.start()
    return thread
