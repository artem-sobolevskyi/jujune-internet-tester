from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from statistics import median, pstdev
from typing import Optional


CREATE_NO_WINDOW = 0x08000000
TIME_RE = re.compile(
    r"(?:time|час|время|zeit)\s*[=<]\s*(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
GW_RE = re.compile(r"gateway:\s*(\d+\.\d+\.\d+\.\d+)", re.IGNORECASE)
WIN_ROUTE_RE = re.compile(
    r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)\s+",
)
LINUX_GW_RE = re.compile(r"default via (\d+\.\d+\.\d+\.\d+)")


def _run(cmd: list[str], timeout: float = 2.0) -> str:
    kwargs: dict = {
        "capture_output": True,
        "timeout": timeout,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startup
    try:
        proc = subprocess.run(cmd, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    raw = proc.stdout or b""
    text = raw.decode("utf-8", errors="replace")
    if TIME_RE.search(text) is None:
        alt = raw.decode("cp866", errors="replace") if os.name == "nt" else ""
        if alt:
            text = alt
    return text


def ping_host(host: str, timeout: float = 0.9) -> Optional[float]:
    if sys.platform == "win32":
        cmd = ["ping", "-n", "1", "-4", "-w", str(int(timeout * 1000)), host]
    elif sys.platform == "darwin":
        cmd = ["ping", "-c", "1", "-W", str(int(timeout * 1000)), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(round(timeout)))), host]
    text = _run(cmd, timeout=timeout + 0.8)
    match = TIME_RE.search(text)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def tcp_ping(host: str, port: int = 443, timeout: float = 0.9) -> Optional[float]:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return (time.perf_counter() - t0) * 1000.0
    except OSError:
        return None


def dns_lookup_ms(host: str = "www.cloudflare.com", timeout: float = 1.2) -> Optional[float]:
    socket.setdefaulttimeout(timeout)
    t0 = time.perf_counter()
    try:
        socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        return (time.perf_counter() - t0) * 1000.0
    except OSError:
        return None
    finally:
        socket.setdefaulttimeout(None)


def default_gateway() -> Optional[str]:
    try:
        if sys.platform == "win32":
            text = _run(["route", "print", "-4"], timeout=2.5)
            for line in text.splitlines():
                match = WIN_ROUTE_RE.search(line)
                if match and match.group(1) != "0.0.0.0":
                    return match.group(1)
        elif sys.platform == "darwin":
            text = _run(["route", "-n", "get", "default"], timeout=2.0)
            match = GW_RE.search(text)
            return match.group(1) if match else None
        else:
            text = _run(["ip", "route", "show", "default"], timeout=2.0)
            match = LINUX_GW_RE.search(text)
            return match.group(1) if match else None
    except OSError:
        return None
    return None


def wifi_hint() -> tuple[str, bool]:
    """Return (interface_or_label, is_wifi). Windows-first."""
    if sys.platform == "win32":
        text = _run(["netsh", "wlan", "show", "interfaces"], timeout=2.5)
        connected = False
        ssid = ""
        for raw in text.splitlines():
            if ":" not in raw:
                continue
            key, val = raw.split(":", 1)
            name = key.strip().lower()
            value = val.strip()
            if name in {"state", "стан", "состояние"}:
                connected = value.lower() in {"connected", "підключено", "подключено"}
            elif name == "ssid" and value:
                ssid = value
        if connected:
            return ssid or "Wi-Fi", True
        return "Ethernet", False
    if sys.platform == "darwin":
        text = _run(["networksetup", "-listallhardwareports"], timeout=2.0)
        wifi_devs: set[str] = set()
        current_port = ""
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("Hardware Port:"):
                current_port = line.split(":", 1)[1].strip()
            elif line.startswith("Device:") and current_port:
                dev = line.split(":", 1)[1].strip()
                if "wi-fi" in current_port.lower() or "airport" in current_port.lower():
                    wifi_devs.add(dev)
        gw_if = _mac_default_iface()
        if gw_if:
            return gw_if, gw_if in wifi_devs
        return "unknown", False
    return "net", False


def _mac_default_iface() -> Optional[str]:
    text = _run(["route", "-n", "get", "default"], timeout=2.0)
    for line in text.splitlines():
        if "interface:" in line.lower():
            return line.split(":", 1)[1].strip()
    return None


@dataclass
class NetSnapshot:
    ping_ms: Optional[float]
    ping_mode: str
    gateway_ms: Optional[float]
    tcp_ms: Optional[float]
    dns_ms: Optional[float]
    extra_ms: dict[str, Optional[float]]
    jitter_ms: float
    loss_pct: float
    baseline_ms: Optional[float]
    history: list[Optional[float]]
    iface: str
    wifi: bool
    gateway_ip: Optional[str]


@dataclass
class NetMonitor:
    wan_host: str = "1.1.1.1"
    wan_alt: str = "8.8.8.8"
    extra_hosts: list[str] = field(default_factory=list)
    window: int = 90

    def __post_init__(self) -> None:
        self._samples: deque[Optional[float]] = deque(maxlen=self.window)
        self._tick = 0
        self._mode = "icmp"
        self._icmp_fail = 0
        self._gateway_ip = default_gateway()
        self._iface, self._wifi = wifi_hint()
        self._gateway_ms: Optional[float] = None
        self._tcp_ms: Optional[float] = None
        self._dns_ms: Optional[float] = None
        self._extra: dict[str, Optional[float]] = {}
        self._alt_ms: Optional[float] = None

    def tick(self) -> NetSnapshot:
        self._tick += 1
        ping_ms: Optional[float] = None
        if self._mode == "icmp":
            ping_ms = ping_host(self.wan_host)
            if ping_ms is None:
                self._icmp_fail += 1
                tcp = tcp_ping(self.wan_host)
                if tcp is not None:
                    ping_ms = tcp
                    self._tcp_ms = tcp
                    if self._icmp_fail >= 4:
                        self._mode = "tcp"
            else:
                self._icmp_fail = 0
        else:
            ping_ms = tcp_ping(self.wan_host)
            self._tcp_ms = ping_ms
            if self._tick % 12 == 0:
                icmp = ping_host(self.wan_host)
                if icmp is not None:
                    self._mode = "icmp"
                    ping_ms = icmp
                    self._icmp_fail = 0

        self._samples.append(ping_ms)

        if self._tick % 2 == 0:
            if self._gateway_ip is None:
                self._gateway_ip = default_gateway()
            if self._gateway_ip:
                self._gateway_ms = ping_host(self._gateway_ip, timeout=0.6)

        if self._tick % 3 == 0:
            self._tcp_ms = tcp_ping(self.wan_host)

        if self._tick % 4 == 0:
            self._alt_ms = ping_host(self.wan_alt)

        if self._tick % 5 == 0:
            self._dns_ms = dns_lookup_ms()

        if self.extra_hosts and self._tick % 2 == 0:
            host = self.extra_hosts[(self._tick // 2) % len(self.extra_hosts)]
            self._extra[host] = ping_host(host)

        if self._tick == 1:
            self._iface, self._wifi = wifi_hint()

        successful = [x for x in self._samples if x is not None]
        recent = list(self._samples)[-30:]
        loss = 100.0 * sum(1 for x in recent if x is None) / max(1, len(recent))
        jitter = pstdev(successful[-10:]) if len(successful) >= 4 else 0.0
        baseline = median(successful[-50:]) if successful else None

        return NetSnapshot(
            ping_ms=ping_ms,
            ping_mode=self._mode,
            gateway_ms=self._gateway_ms,
            tcp_ms=self._tcp_ms,
            dns_ms=self._dns_ms,
            extra_ms=dict(self._extra),
            jitter_ms=round(jitter, 1),
            loss_pct=round(loss, 1),
            baseline_ms=round(baseline, 1) if baseline is not None else None,
            history=list(self._samples),
            iface=self._iface,
            wifi=self._wifi,
            gateway_ip=self._gateway_ip,
        )
