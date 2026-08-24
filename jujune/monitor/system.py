from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Optional

import psutil

CREATE_NO_WINDOW = 0x08000000

_NVIDIA_CANDIDATES = (
    "nvidia-smi",
    r"C:\Windows\System32\nvidia-smi.exe",
    r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
)

_WIN_HW_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$temps = @{}
function Add-Temp([string]$name, $value) {
  if ($null -eq $value -or $name -eq '') { return }
  try { $c = [double]$value } catch { return }
  if ($c -gt 15 -and $c -lt 125) { $temps[$name] = [Math]::Round($c, 1) }
}
try {
  Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor |
    Where-Object { $_.SensorType -eq 'Temperature' } |
    ForEach-Object { Add-Temp $_.Name $_.Value }
} catch {}
try {
  Get-CimInstance -Namespace root/OpenHardwareMonitor -ClassName Sensor |
    Where-Object { $_.SensorType -eq 'Temperature' } |
    ForEach-Object { Add-Temp $_.Name $_.Value }
} catch {}
try {
  Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature |
    ForEach-Object { Add-Temp 'acpi' (($_.CurrentTemperature / 10) - 273.15) }
} catch {}
try {
  Get-CimInstance Win32_PerfFormattedData_Counters_ThermalZoneInformation |
    ForEach-Object {
      $hi = $_.HighPrecisionTemperature
      if ($hi -and $hi -gt 1000) { Add-Temp 'zone' (($hi / 10) - 273.15) }
      elseif ($_.Temperature) { Add-Temp 'zone' ($_.Temperature - 273) }
    }
} catch {}
$gpu = $null
try {
  $load = Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor |
    Where-Object { $_.SensorType -eq 'Load' -and $_.Name -match 'GPU Core' } |
    Select-Object -First 1
  if ($load) { $gpu = [Math]::Round([double]$load.Value, 1) }
} catch {}
$disk = $null
try {
  $pd = Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk -Filter "Name='_Total'"
  if ($pd -and $null -ne $pd.PercentDiskTime) {
    $disk = [Math]::Min(100, [Math]::Round([double]$pd.PercentDiskTime, 1))
  }
} catch {}
@{ temps = $temps; gpu = $gpu; disk = $disk } | ConvertTo-Json -Compress
"""


def _run(cmd: list[str], timeout: float = 2.0) -> str:
    kwargs: dict = {"capture_output": True, "timeout": timeout, "check": False}
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startup
    try:
        proc = subprocess.run(cmd, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or b"").decode("utf-8", errors="replace")


def _nvidia_smi() -> Optional[str]:
    for candidate in _NVIDIA_CANDIDATES:
        if candidate == "nvidia-smi":
            found = shutil.which(candidate)
            if found:
                return found
        elif os.path.isfile(candidate):
            return candidate
    return None


def _nvidia() -> tuple[Optional[float], Optional[float]]:
    exe = _nvidia_smi()
    if not exe:
        return None, None
    text = _run(
        [
            exe,
            "--query-gpu=temperature.gpu,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        timeout=1.6,
    )
    line = text.strip().splitlines()[0] if text.strip() else ""
    if not line:
        return None, None
    parts = [p.strip() for p in line.split(",")]
    try:
        temp = float(parts[0]) if parts else None
        util = float(parts[1]) if len(parts) > 1 else None
        return temp, util
    except ValueError:
        return None, None


def _windows_hw() -> tuple[dict[str, float], Optional[float], Optional[float]]:
    """Return (temps, gpu_util, disk_pct) from Windows WMI / CIM."""
    if sys.platform != "win32":
        return {}, None, None
    text = _run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            _WIN_HW_PS,
        ],
        timeout=5.0,
    ).strip()
    if not text:
        return {}, None, None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}, None, None
    temps: dict[str, float] = {}
    raw_temps = data.get("temps") if isinstance(data, dict) else None
    if isinstance(raw_temps, dict):
        for key, value in raw_temps.items():
            try:
                temps[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    gpu = None
    disk = None
    if isinstance(data, dict):
        try:
            if data.get("gpu") is not None:
                gpu = float(data["gpu"])
        except (TypeError, ValueError):
            pass
        try:
            if data.get("disk") is not None:
                disk = float(data["disk"])
        except (TypeError, ValueError):
            pass
    return temps, gpu, disk


def _psutil_temps() -> dict[str, float]:
    result: dict[str, float] = {}
    fn = getattr(psutil, "sensors_temperatures", None)
    if fn is None:
        return result
    try:
        data = fn() or {}
    except Exception:
        return result
    for name, entries in data.items():
        for entry in entries:
            current = getattr(entry, "current", None)
            if current is None:
                continue
            label = (entry.label or name or "temp").strip() or name
            result[label] = float(current)
    return result


@dataclass
class SystemSnapshot:
    cpu_pct: float
    ram_pct: float
    swap_pct: float
    disk_util: Optional[float]
    disk_read_mb: float
    disk_write_mb: float
    temps: dict[str, float]
    gpu_util: Optional[float]
    top_cpu: str
    top_ram: str


@dataclass
class SystemMonitor:
    def __post_init__(self) -> None:
        self._prev_disk = psutil.disk_io_counters()
        self._prev_t = time.perf_counter()
        self._temps: dict[str, float] = {}
        self._gpu_util: Optional[float] = None
        self._win_disk: Optional[float] = None
        self._tick = 0
        self._top_cpu = "—"
        self._top_ram = "—"
        psutil.cpu_percent(interval=None)
        for proc in psutil.process_iter(["cpu_percent"]):
            try:
                proc.cpu_percent(interval=None)
            except (psutil.Error, OSError):
                continue

    def tick(self) -> SystemSnapshot:
        self._tick += 1
        now = time.perf_counter()
        disk = psutil.disk_io_counters()
        dt = max(0.001, now - self._prev_t)
        read_mb = 0.0
        write_mb = 0.0
        util: Optional[float] = None
        sample_ok = dt >= 0.35
        if sample_ok and disk and self._prev_disk:
            read_mb = max(0.0, (disk.read_bytes - self._prev_disk.read_bytes) / dt / 1e6)
            write_mb = max(0.0, (disk.write_bytes - self._prev_disk.write_bytes) / dt / 1e6)
            busy_now = getattr(disk, "busy_time", None)
            busy_prev = getattr(self._prev_disk, "busy_time", None)
            if busy_now is not None and busy_prev is not None:
                busy_s = (busy_now - busy_prev) / 1000.0
                if busy_s >= 0:
                    util = min(100.0, max(0.0, busy_s / dt * 100.0))
        if disk:
            self._prev_disk = disk
            self._prev_t = now

        if self._tick % 3 == 0:
            temps = _psutil_temps()
            win_temps, win_gpu, win_disk = {}, None, None
            if sys.platform == "win32":
                win_temps, win_gpu, win_disk = _windows_hw()
            temps.update(win_temps)
            self._win_disk = win_disk
            gpu_temp, gpu_util = _nvidia()
            if gpu_temp is not None:
                temps["gpu"] = gpu_temp
            if gpu_util is None:
                gpu_util = win_gpu
            self._gpu_util = gpu_util
            self._temps = temps
            self._refresh_top()

        if util is None and self._win_disk is not None:
            util = self._win_disk

        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return SystemSnapshot(
            cpu_pct=round(psutil.cpu_percent(interval=None), 1),
            ram_pct=round(mem.percent, 1),
            swap_pct=round(swap.percent, 1),
            disk_util=round(util, 1) if util is not None else None,
            disk_read_mb=round(max(0.0, read_mb), 1),
            disk_write_mb=round(max(0.0, write_mb), 1),
            temps=dict(self._temps),
            gpu_util=self._gpu_util,
            top_cpu=self._top_cpu,
            top_ram=self._top_ram,
        )

    def _refresh_top(self) -> None:
        cpu_best = ("—", -1.0)
        ram_best = ("—", -1.0)
        for proc in psutil.process_iter(["name", "cpu_percent", "memory_percent"]):
            try:
                info = proc.info
                name = (info.get("name") or "?").strip() or "?"
                cpu = float(info.get("cpu_percent") or 0.0)
                ram = float(info.get("memory_percent") or 0.0)
            except (psutil.Error, OSError, TypeError):
                continue
            if cpu > cpu_best[1]:
                cpu_best = (name, cpu)
            if ram > ram_best[1]:
                ram_best = (name, ram)
        self._top_cpu = cpu_best[0]
        self._top_ram = ram_best[0]
