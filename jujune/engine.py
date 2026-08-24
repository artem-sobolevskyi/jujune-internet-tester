from __future__ import annotations

import csv
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from jujune.config import load_config
from jujune.monitor.diagnose import DiagnoseState, Verdict, diagnose
from jujune.monitor.graphs import GraphSampler, GraphSnapshot
from jujune.monitor.net import NetMonitor, NetSnapshot
from jujune.monitor.system import SystemMonitor, SystemSnapshot
from jujune.paths import data_dir


@dataclass
class Snapshot:
    ts: float
    net: NetSnapshot
    sys: SystemSnapshot
    verdict: Verdict
    events: list[dict]
    graphs: GraphSnapshot


class Engine:
    def __init__(self) -> None:
        cfg = load_config()
        extra = [h for h in cfg.get("extra_hosts", []) if isinstance(h, str) and h.strip()]
        self.net = NetMonitor(
            wan_host=str(cfg.get("wan_host") or "1.1.1.1"),
            wan_alt=str(cfg.get("wan_host_alt") or "8.8.8.8"),
            extra_hosts=extra,
        )
        self.sys = SystemMonitor()
        self.graphs = GraphSampler()
        self.state = DiagnoseState()
        self._lock = threading.Lock()
        self._snap: Optional[Snapshot] = None
        self._events: deque[dict] = deque(maxlen=12)
        self._running = True
        self._thread: Optional[threading.Thread] = None
        self._log_dir = data_dir()
        self._last_logged_cause = ""
        self._last_logged_at = 0.0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="jujune-engine", daemon=True)
        self._thread.start()
        self.graphs.start()

    def stop(self) -> None:
        self._running = False
        self.graphs.stop()

    def snapshot(self) -> Optional[Snapshot]:
        with self._lock:
            return self._snap

    def _loop(self) -> None:
        while self._running:
            t0 = time.perf_counter()
            try:
                self._tick()
            except Exception as exc:
                self._write_crash(exc)
            delay = 1.0 - (time.perf_counter() - t0)
            time.sleep(max(0.05, delay))

    def _tick(self) -> None:
        now = time.time()
        net = self.net.tick()
        sysn = self.sys.tick()
        verdict = diagnose(net, sysn, now, self.state)
        if verdict.level == "lag" and (
            verdict.cause != self._last_logged_cause or now - self._last_logged_at > 8
        ):
            event = {
                "ts": now,
                "when": datetime.now().strftime("%H:%M:%S"),
                "level": verdict.level,
                "cause": verdict.cause,
                "title": verdict.title,
                "detail": verdict.detail,
                "ping": net.ping_ms,
                "disk": sysn.disk_util,
                "cpu": sysn.cpu_pct,
                "temp": max(sysn.temps.values()) if sysn.temps else None,
            }
            self._events.appendleft(event)
            self._append_jsonl(event)
            self._last_logged_cause = verdict.cause
            self._last_logged_at = now
        graphs = self.graphs.snapshot()
        if graphs.gpu_pct is not None:
            sysn.gpu_util = graphs.gpu_pct
        snap = Snapshot(
            ts=now,
            net=net,
            sys=sysn,
            verdict=verdict,
            events=list(self._events),
            graphs=graphs,
        )
        self._append_csv(snap)
        with self._lock:
            self._snap = snap

    def _append_jsonl(self, event: dict) -> None:
        path = self._log_dir / "events.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _append_csv(self, snap: Snapshot) -> None:
        path = self._log_dir / "history.csv"
        new_file = not path.exists()
        if path.exists() and path.stat().st_size > 12_000_000:
            rotated = self._log_dir / "history.prev.csv"
            path.replace(rotated)
            new_file = True
        temps = snap.sys.temps
        cpu_t = temps.get("cpu") or temps.get("GPU Thermal") or None
        gpu_t = temps.get("gpu")
        if cpu_t is None and temps:
            cpu_t = max(temps.values())
        with path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            if new_file:
                writer.writerow(
                    [
                        "ts",
                        "time",
                        "ping_ms",
                        "gateway_ms",
                        "jitter",
                        "loss",
                        "cpu",
                        "ram",
                        "disk_util",
                        "disk_mb",
                        "temp",
                        "gpu_temp",
                        "gpu_util",
                        "frametime_ms",
                        "fps",
                        "cpu_mhz",
                        "level",
                        "cause",
                    ]
                )
            writer.writerow(
                [
                    int(snap.ts),
                    datetime.fromtimestamp(snap.ts).strftime("%Y-%m-%d %H:%M:%S"),
                    snap.net.ping_ms,
                    snap.net.gateway_ms,
                    snap.net.jitter_ms,
                    snap.net.loss_pct,
                    snap.sys.cpu_pct,
                    snap.sys.ram_pct,
                    snap.sys.disk_util,
                    round(snap.sys.disk_read_mb + snap.sys.disk_write_mb, 1),
                    cpu_t,
                    gpu_t,
                    snap.sys.gpu_util,
                    snap.graphs.frametime_ms,
                    snap.graphs.fps,
                    snap.graphs.cpu_mhz,
                    snap.verdict.level,
                    snap.verdict.cause,
                ]
            )

    def _write_crash(self, exc: Exception) -> None:
        path = self._log_dir / "errors.log"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat()} {type(exc).__name__}: {exc}\n")
