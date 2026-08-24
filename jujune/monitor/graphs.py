from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psutil

from jujune.monitor.system import _nvidia
from jujune.paths import app_root

CREATE_NO_WINDOW = 0x08000000
IGNORE_APPS = {
    "dwm.exe",
    "explorer.exe",
    "jujune.exe",
    "searchhost.exe",
    "shellexperiencehost.exe",
    "applicationframehost.exe",
    "textinputhost.exe",
    "startmenuexperiencehost.exe",
    "lockapp.exe",
    "systemsettings.exe",
    "runtimebroker.exe",
    "sihost.exe",
    "csrss.exe",
    "winlogon.exe",
    "fontdrvhost.exe",
    "conhost.exe",
    "presentmon.exe",
    "python.exe",
    "pythonw.exe",
    "taskmgr.exe",
    "widgets.exe",
    "widgetservice.exe",
}


def presentmon_exe() -> Optional[Path]:
    names = ("PresentMon.exe", "PresentMon-2.5.1-x64.exe")
    roots = [app_root(), app_root() / "vendor"]
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    for root in roots:
        for name in names:
            path = root / name
            if path.is_file():
                return path
    found = shutil.which("PresentMon") or shutil.which("PresentMon-2.5.1-x64")
    return Path(found) if found else None


def _popen(cmd: list[str]) -> subprocess.Popen:
    kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        kwargs["creationflags"] = CREATE_NO_WINDOW
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startup
    return subprocess.Popen(cmd, **kwargs)


def _app_name(raw: str) -> str:
    name = Path(raw.strip().strip('"')).name.lower()
    return name or raw.lower()


def _win_cpu_mhz() -> tuple[Optional[float], Optional[float]]:
    import ctypes
    from ctypes import wintypes

    class PROCESSOR_POWER_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("Number", wintypes.ULONG),
            ("MaxMhz", wintypes.ULONG),
            ("CurrentMhz", wintypes.ULONG),
            ("MhzLimit", wintypes.ULONG),
            ("MaxIdleState", wintypes.ULONG),
            ("CurrentIdleState", wintypes.ULONG),
        ]

    ALL_PROCESSOR_GROUPS = 0xFFFF
    try:
        nproc = int(ctypes.windll.kernel32.GetActiveProcessorCount(ALL_PROCESSOR_GROUPS))
    except Exception:
        nproc = 0
    if nproc <= 0:
        nproc = os.cpu_count() or 1
    call = ctypes.windll.powrprof.CallNtPowerInformation
    call.restype = ctypes.c_long
    for size in (nproc, nproc * 2):
        buf = (PROCESSOR_POWER_INFORMATION * size)()
        status = call(11, None, 0, ctypes.byref(buf), ctypes.sizeof(buf))
        if status == 0:
            currents = [int(item.CurrentMhz) for item in buf if item.CurrentMhz]
            maxes = [int(item.MaxMhz) for item in buf if item.MaxMhz]
            cur = float(max(currents)) if currents else None
            mx = float(max(maxes)) if maxes else None
            return cur, mx
    return None, None


def read_cpu_mhz() -> tuple[Optional[float], Optional[float]]:
    if sys.platform == "win32":
        try:
            cur, mx = _win_cpu_mhz()
            if cur:
                return cur, mx
        except Exception:
            pass
    try:
        info = psutil.cpu_freq()
    except Exception:
        return None, None
    if not info:
        return None, None
    cur = float(info.current) if info.current else None
    mx = float(info.max) if info.max else None
    if cur is not None and 0 < cur < 20:
        cur *= 1000.0
    if mx is not None and 0 < mx < 20:
        mx *= 1000.0
    if cur is not None and cur <= 0:
        cur = None
    return cur, mx


@dataclass
class GraphSnapshot:
    cpu_history: list[float]
    gpu_history: list[float]
    frametime_history: list[Optional[float]]
    mhz_history: list[Optional[float]]
    cpu_pct: float
    gpu_pct: Optional[float]
    frametime_ms: Optional[float]
    fps: Optional[float]
    frame_app: str
    cpu_mhz: Optional[float]
    cpu_mhz_max: Optional[float]


class GraphSampler:
    def __init__(self) -> None:
        self.cpu: deque[float] = deque(maxlen=120)
        self.gpu: deque[Optional[float]] = deque(maxlen=120)
        self.frametime: deque[Optional[float]] = deque(maxlen=180)
        self.mhz: deque[Optional[float]] = deque(maxlen=120)
        self._lock = threading.Lock()
        self._running = True
        self._gpu_pct: Optional[float] = None
        self._cpu_pct = 0.0
        self._cpu_mhz: Optional[float] = None
        self._cpu_mhz_max: Optional[float] = None
        self._ft_ms: Optional[float] = None
        self._fps: Optional[float] = None
        self._frame_app = ""
        self._bucket_max = 0.0
        self._bucket_t = time.perf_counter()
        self._recent: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=90))
        self._pm: Optional[subprocess.Popen] = None
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        psutil.cpu_percent(interval=None)
        self._threads = [
            threading.Thread(target=self._cpu_loop, name="jujune-cpu", daemon=True),
            threading.Thread(target=self._gpu_loop, name="jujune-gpu", daemon=True),
            threading.Thread(target=self._ft_bucket_loop, name="jujune-ft-bucket", daemon=True),
            threading.Thread(target=self._presentmon_loop, name="jujune-presentmon", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._running = False
        proc = self._pm
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass

    def snapshot(self) -> GraphSnapshot:
        with self._lock:
            return GraphSnapshot(
                cpu_history=list(self.cpu),
                gpu_history=list(self.gpu),
                frametime_history=list(self.frametime),
                mhz_history=list(self.mhz),
                cpu_pct=self._cpu_pct,
                gpu_pct=self._gpu_pct,
                frametime_ms=self._ft_ms,
                fps=self._fps,
                frame_app=self._frame_app,
                cpu_mhz=self._cpu_mhz,
                cpu_mhz_max=self._cpu_mhz_max,
            )

    def _cpu_loop(self) -> None:
        while self._running:
            value = float(psutil.cpu_percent(interval=0.25))
            mhz, mhz_max = read_cpu_mhz()
            with self._lock:
                self._cpu_pct = value
                self.cpu.append(value)
                self._cpu_mhz = mhz
                if mhz_max:
                    self._cpu_mhz_max = mhz_max
                self.mhz.append(mhz)

    def _gpu_loop(self) -> None:
        while self._running:
            _temp, util = _nvidia()
            with self._lock:
                if util is not None:
                    self._gpu_pct = util
                self.gpu.append(self._gpu_pct)
            time.sleep(0.45)

    def _ft_bucket_loop(self) -> None:
        while self._running:
            time.sleep(0.1)
            now = time.perf_counter()
            with self._lock:
                sample: Optional[float] = None
                if self._bucket_max > 0:
                    sample = self._bucket_max
                elif self._ft_ms is not None:
                    sample = self._ft_ms
                self.frametime.append(sample)
                self._bucket_max = 0.0
                self._bucket_t = now

    def _note_frame(self, app: str, ms: float) -> None:
        if ms <= 0 or ms > 500:
            return
        with self._lock:
            self._recent[app].append(ms)
            self._bucket_max = max(self._bucket_max, ms)
            best_app = self._pick_app()
            self._frame_app = best_app
            series = list(self._recent.get(best_app, []))
            if series:
                self._ft_ms = series[-1]
                mid = sorted(series)[len(series) // 2]
                if mid > 0:
                    self._fps = 1000.0 / mid

    def _pick_app(self) -> str:
        best = ""
        best_n = 0
        for app, series in self._recent.items():
            if len(series) > best_n:
                best = app
                best_n = len(series)
        return best

    def _presentmon_loop(self) -> None:
        if sys.platform != "win32":
            return
        exe = presentmon_exe()
        if exe is None:
            return
        cmds = [
            [
                str(exe),
                "--stop_existing_session",
                "--output_stdout",
                "--no_console_stats",
                "--session_name",
                "JujunePM",
                "--v1_metrics",
            ],
            [
                str(exe),
                "--stop_existing_session",
                "--output_stdout",
                "--no_console_stats",
                "--session_name",
                "JujunePM",
            ],
        ]
        for cmd in cmds:
            if not self._running:
                return
            try:
                proc = _popen(cmd)
            except OSError:
                continue
            self._pm = proc
            self._read_presentmon(proc)
            try:
                proc.wait(timeout=0.4)
            except Exception:
                try:
                    proc.kill()
                except OSError:
                    pass
            self._pm = None
            if not self._running:
                return

    def _read_presentmon(self, proc: subprocess.Popen) -> None:
        stdout = proc.stdout
        if stdout is None:
            return
        header: Optional[list[str]] = None
        idx_app = 0
        idx_ft = -1
        while self._running:
            line = stdout.readline()
            if line == "" and proc.poll() is not None:
                return
            if not line:
                time.sleep(0.02)
                continue
            raw = line.strip()
            if not raw or raw.startswith("error:") or raw.startswith("warning:"):
                continue
            if header is None:
                if "Application" not in raw and "application" not in raw.lower():
                    continue
                header = next(csv.reader(io.StringIO(raw)))
                lower = [h.strip() for h in header]
                try:
                    idx_app = next(i for i, h in enumerate(lower) if h.lower() == "application")
                except StopIteration:
                    idx_app = 0
                for name in (
                    "MsBetweenPresents",
                    "msBetweenPresents",
                    "MsBetweenDisplayChange",
                    "MsBetweenAppStart",
                    "FrameTime",
                    "msBetweenDisplayChange",
                ):
                    if name in header:
                        idx_ft = header.index(name)
                        break
                    for i, col in enumerate(lower):
                        if col.lower() == name.lower():
                            idx_ft = i
                            break
                    if idx_ft >= 0:
                        break
                if idx_ft < 0:
                    header = None
                continue
            try:
                row = next(csv.reader(io.StringIO(raw)))
            except Exception:
                continue
            if idx_ft >= len(row) or idx_app >= len(row):
                continue
            app = _app_name(row[idx_app])
            if app in IGNORE_APPS:
                continue
            try:
                ms = float(row[idx_ft])
            except ValueError:
                continue
            self._note_frame(app, ms)
