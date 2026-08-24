from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Optional

from jujune.monitor.net import NetSnapshot
from jujune.monitor.system import SystemSnapshot


@dataclass
class Verdict:
    level: str
    cause: str
    title: str
    detail: str
    quote: str
    pose: str
    score: int
    sticky_until: float = 0.0


@dataclass
class DiagnoseState:
    spike_times: list[float] = field(default_factory=list)
    last: Optional[Verdict] = None


def _max_temp(temps: dict[str, float]) -> Optional[float]:
    real = [
        v
        for k, v in temps.items()
        if v and v > 20 and "pressure" not in k.lower()
    ]
    return max(real) if real else None


def _periodic_note(times: list[float], now: float) -> str:
    recent = [t for t in times if now - t <= 40 * 60]
    if len(recent) < 3:
        return ""
    intervals = [recent[i] - recent[i - 1] for i in range(1, len(recent))]
    avg = mean(intervals)
    if avg < 90 or avg > 12 * 60:
        return ""
    spread = pstdev(intervals) if len(intervals) >= 3 else 0.0
    if spread > avg * 0.35:
        return ""
    minutes = avg / 60.0
    return f" Repeats about every {minutes:.1f} min."


def diagnose(
    net: NetSnapshot,
    sysn: SystemSnapshot,
    now: float,
    state: DiagnoseState,
) -> Verdict:
    reasons: list[Verdict] = []
    base = net.baseline_ms or 25.0
    spike_lim = max(70.0, base * 2.4, base + 35.0)
    max_t = _max_temp(sysn.temps)
    disk_mb = sysn.disk_read_mb + sysn.disk_write_mb
    disk_util = sysn.disk_util
    disk_load = f"{disk_util:.0f}%" if disk_util is not None else f"{disk_mb:.0f} MB/s"

    loc = ""
    if net.ping_ms is not None and net.gateway_ms is not None:
        if net.gateway_ms >= 35 and net.ping_ms >= net.gateway_ms * 0.6:
            loc = " Lag is local: Wi-Fi or the router."
        elif net.gateway_ms <= 12 and net.ping_ms >= 80:
            loc = " Router is fine — WAN/ISP is the bottleneck."
    if net.wifi and not loc:
        loc = " You are on Wi-Fi; brief stutters are common."

    if net.loss_pct >= 8:
        reasons.append(
            Verdict(
                "lag",
                "loss",
                "PACKET LOSS",
                f"Lost {net.loss_pct:.0f}% of packets in the last seconds.{loc}",
                "Packets are dropping. Teleports are the network, not FPS.",
                "lag",
                100,
            )
        )
    elif net.loss_pct >= 3:
        reasons.append(
            Verdict(
                "warn",
                "loss",
                "UNSTABLE PACKETS",
                f"Loss {net.loss_pct:.0f}%. If hitreg feels off, this is why.{loc}",
                "Channel is dirty. Not dead yet, but not fair.",
                "warn",
                72,
            )
        )

    if net.ping_ms is None and net.loss_pct >= 20:
        reasons.append(
            Verdict(
                "lag",
                "ping",
                "NO REPLY",
                f"{net.ping_mode.upper()} ping is failing.{loc}",
                "The channel went silent. This is not FPS — the net dropped.",
                "lag",
                98,
            )
        )
    elif net.ping_ms is not None:
        if net.ping_ms >= 220 or net.ping_ms >= spike_lim * 1.5:
            reasons.append(
                Verdict(
                    "lag",
                    "ping",
                    "PING SPIKE",
                    f"{net.ping_ms:.0f} ms now, baseline {base:.0f} ms.{loc}",
                    "Not FPS — ping just jumped.",
                    "lag",
                    96,
                )
            )
        elif net.ping_ms >= spike_lim:
            reasons.append(
                Verdict(
                    "warn",
                    "ping",
                    "PING RISING",
                    f"{net.ping_ms:.0f} ms vs baseline {base:.0f} ms.{loc}",
                    "The pipe is getting heavy. If it stuttered, this is it.",
                    "warn",
                    74,
                )
            )

    extra_bad = [
        (host, ms)
        for host, ms in net.extra_ms.items()
        if ms is not None and ms >= max(90, (net.baseline_ms or 30) * 3)
    ]
    if extra_bad:
        host, ms = extra_bad[0]
        reasons.append(
            Verdict(
                "lag",
                "game",
                "GAME SERVER",
                f"{host}: {ms:.0f} ms. WAN {net.ping_ms:.0f} ms."
                if net.ping_ms is not None
                else f"{host}: {ms:.0f} ms.",
                "Internet looks fine. The game server path does not.",
                "lag",
                90,
            )
        )

    if net.jitter_ms >= 35:
        reasons.append(
            Verdict(
                "lag",
                "jitter",
                "JITTER",
                f"Ping spread {net.jitter_ms:.0f} ms. Feels like micro-stutters.{loc}",
                "Ping is bouncing. Unstable pipe.",
                "lag",
                86,
            )
        )
    elif net.jitter_ms >= 16:
        reasons.append(
            Verdict(
                "warn",
                "jitter",
                "PING BOUNCE",
                f"Jitter {net.jitter_ms:.0f} ms.{loc}",
                "Nervous channel. Playable, but I'm watching it.",
                "warn",
                58,
            )
        )

    if (disk_util is not None and disk_util >= 88) or disk_mb >= 380:
        reasons.append(
            Verdict(
                "lag",
                "disk",
                "DISK SATURATED",
                f"Load {disk_load}. The game is waiting on files.",
                "Disk is slammed. These are hitches, not ping.",
                "disk",
                84,
            )
        )
    elif (disk_util is not None and disk_util >= 65) or disk_mb >= 180:
        reasons.append(
            Verdict(
                "warn",
                "disk",
                "DISK BUSY",
                f"{disk_load} · {sysn.disk_read_mb:.0f}R {sysn.disk_write_mb:.0f}W MB/s.",
                "Something is chewing the disk. If it froze, blame that.",
                "disk",
                55,
            )
        )

    if sysn.ram_pct >= 96 or sysn.swap_pct >= 40:
        reasons.append(
            Verdict(
                "lag",
                "ram",
                "RAM FULL",
                f"RAM {sysn.ram_pct:.0f}% · pagefile {sysn.swap_pct:.0f}%. Top: {sysn.top_ram}.",
                "Memory is gone. Windows is paging to disk — expect hitches.",
                "disk",
                80,
            )
        )

    if sysn.cpu_pct >= 99:
        reasons.append(
            Verdict(
                "lag",
                "cpu",
                "CPU MAXED",
                f"CPU {sysn.cpu_pct:.0f}%. Top process: {sysn.top_cpu}.",
                "CPU is pegged. Frames can tear even with clean ping.",
                "warn",
                70,
            )
        )
    elif sysn.cpu_pct >= 92:
        reasons.append(
            Verdict(
                "warn",
                "cpu",
                "CPU HIGH",
                f"CPU {sysn.cpu_pct:.0f}% · {sysn.top_cpu}",
                "CPU is gasping. If the picture stutters, it may be this.",
                "warn",
                48,
            )
        )

    if max_t is not None and max_t >= 92:
        reasons.append(
            Verdict(
                "lag",
                "temp",
                "OVERHEAT",
                f"{max_t:.0f}°C. Clocks may have dropped — that's FPS lag, not hitreg.",
                "Too hot. Thermal throttle is more likely than the network.",
                "hot",
                78,
            )
        )
    elif max_t is not None and max_t >= 85:
        reasons.append(
            Verdict(
                "warn",
                "temp",
                "HOT",
                f"{max_t:.0f}°C. If the lag is FPS, watch temperature.",
                "Temps are biting. Still holding.",
                "hot",
                50,
            )
        )

    gpu_t = sysn.temps.get("gpu", max_t)
    if sysn.gpu_util is not None and sysn.gpu_util >= 99 and (gpu_t or 0) >= 83:
        reasons.append(
            Verdict(
                "warn",
                "gpu",
                "GPU LIMIT",
                f"GPU {sysn.gpu_util:.0f}% · {gpu_t:.0f}°C",
                "GPU is pinned. That's FPS drops, not ping.",
                "hot",
                52,
            )
        )

    if not reasons:
        verdict = Verdict(
            "ok",
            "ok",
            "CHANNEL CLEAR",
            "Ping, disk and temps look fine. If it stutters, watch what flashes here.",
            "Channel clear. You can push.",
            "ok",
            0,
        )
    else:
        reasons.sort(key=lambda v: v.score, reverse=True)
        verdict = reasons[0]

    if verdict.cause in {"ping", "loss", "jitter", "game"} and verdict.level == "lag":
        state.spike_times.append(now)
        state.spike_times = [t for t in state.spike_times if now - t <= 45 * 60]
        extra = _periodic_note(state.spike_times, now)
        if extra:
            verdict.detail += extra
            if "every" in extra:
                verdict.title = "PERIODIC LAG"

    sticky = 12.0
    prev = state.last
    if (
        prev
        and prev.level == "lag"
        and now < prev.sticky_until
        and verdict.score < prev.score
    ):
        prev.detail = prev.detail.split(" · holding")[0] + " · holding the last spike."
        state.last = prev
        return prev

    if verdict.level == "lag":
        verdict.sticky_until = now + sticky
    else:
        verdict.sticky_until = 0.0
    state.last = verdict
    return verdict
