"""LAN autodiscovery: mDNS browse plus an optional TCP sweep for open ADB ports.

mDNS finds Chromecasts/Android TVs that advertise themselves (with friendly
name + model, which drives profile suggestions). The subnet sweep catches
devices on VLANs where multicast doesn't reach, or with mDNS disabled.
"""

import ipaddress
import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

from .db import utcnow
from .profiles import GENERIC_PROFILE_ID, suggest_profile

log = logging.getLogger(__name__)

MDNS_TYPES = [
    "_googlecast._tcp.local.",        # Chromecast / Google TV / most Android TVs
    "_androidtvremote2._tcp.local.",  # Android TV remote protocol
    "_adb-tls-connect._tcp.local.",   # Android 11+ wireless debugging
    "_adb._tcp.local.",
]
ADB_PORT = 5555
MAX_SWEEP_HOSTS = 1024
SWEEP_WORKERS = 64


class _Listener(ServiceListener):
    def __init__(self) -> None:
        self.found: list[tuple[str, str, object]] = []

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name, timeout=3000)
        if info is not None:
            self.found.append((type_, name, info))

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        pass

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        pass


def _tcp_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class DiscoveryService:
    """Runs scans in a background thread; the API polls snapshot() for results."""

    def __init__(self, profiles_getter, known_hosts_getter, default_subnet: str = ""):
        self._profiles_getter = profiles_getter
        self._known_hosts_getter = known_hosts_getter
        self.default_subnet = default_subnet
        self._lock = threading.Lock()
        self._scanning = False
        self._results: dict[str, dict] = {}
        self._started_at: str | None = None
        self._finished_at: str | None = None
        self._error = ""

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "scanning": self._scanning,
                "default_subnet": self.default_subnet,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "error": self._error,
                "results": sorted(self._results.values(), key=lambda r: r["host"]),
            }

    def start_scan(self, mdns: bool = True, subnet: str | None = None,
                   duration_s: float = 6.0) -> bool:
        with self._lock:
            if self._scanning:
                return False
            self._scanning = True
            self._results = {}
            self._error = ""
            self._started_at = utcnow()
            self._finished_at = None
        threading.Thread(target=self._scan, args=(mdns, subnet, duration_s),
                         daemon=True, name="warden-discovery").start()
        return True

    # ------------------------------------------------------------ internals

    def _scan(self, mdns: bool, subnet: str | None, duration_s: float) -> None:
        found: dict[str, dict] = {}
        try:
            if mdns:
                self._scan_mdns(found, duration_s)
            if subnet:
                self._sweep_subnet(found, subnet)
            self._probe_adb(found)
            self._annotate(found)
        except Exception as exc:
            log.exception("discovery scan failed")
            with self._lock:
                self._error = str(exc)
        finally:
            with self._lock:
                self._results = found
                self._scanning = False
                self._finished_at = utcnow()

    @staticmethod
    def _entry(found: dict, host: str) -> dict:
        return found.setdefault(host, {
            "host": host, "name": "", "model": "",
            "mdns_types": [], "port_open": False, "adb_port": ADB_PORT,
        })

    def _scan_mdns(self, found: dict, duration_s: float) -> None:
        zc = Zeroconf()
        listener = _Listener()
        browsers = [ServiceBrowser(zc, t, listener) for t in MDNS_TYPES]
        time.sleep(duration_s)
        zc.close()
        del browsers

        for type_, name, info in listener.found:
            addrs = [a for a in info.parsed_addresses() if ":" not in a]
            if not addrs:
                continue
            entry = self._entry(found, addrs[0])
            if type_ not in entry["mdns_types"]:
                entry["mdns_types"].append(type_)
            props: dict[str, str] = {}
            for key, value in (info.properties or {}).items():
                try:
                    props[key.decode()] = value.decode() if isinstance(value, bytes) else ""
                except (UnicodeDecodeError, AttributeError):
                    continue
            instance = name.removesuffix("." + type_)
            if type_ == "_googlecast._tcp.local.":
                entry["name"] = props.get("fn") or entry["name"] or instance
                entry["model"] = props.get("md") or entry["model"]
            else:
                entry["name"] = entry["name"] or instance

    def _sweep_subnet(self, found: dict, subnet: str) -> None:
        network = ipaddress.ip_network(subnet, strict=False)
        hosts = list(network.hosts())[:MAX_SWEEP_HOSTS]
        log.info("Sweeping %d hosts in %s for tcp/%d", len(hosts), network, ADB_PORT)
        with ThreadPoolExecutor(max_workers=SWEEP_WORKERS) as pool:
            for host, is_open in zip(hosts, pool.map(lambda h: _tcp_open(str(h), ADB_PORT), hosts)):
                if is_open:
                    self._entry(found, str(host))["port_open"] = True

    @staticmethod
    def _probe_adb(found: dict) -> None:
        for entry in found.values():
            if not entry["port_open"]:
                entry["port_open"] = _tcp_open(entry["host"], ADB_PORT)

    def _annotate(self, found: dict) -> None:
        profiles = self._profiles_getter()
        known_hosts = self._known_hosts_getter()
        for entry in found.values():
            suggested = suggest_profile(profiles, f"{entry['name']} {entry['model']}",
                                        tuple(entry["mdns_types"]))
            if suggested is None and (entry["port_open"] or entry["mdns_types"]):
                suggested = GENERIC_PROFILE_ID if GENERIC_PROFILE_ID in profiles else None
            entry["suggested_profile"] = suggested
            entry["already_added"] = entry["host"] in known_hosts
