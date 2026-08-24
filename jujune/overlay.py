from __future__ import annotations

import math
import time
import tkinter as tk
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageTk

from jujune.art import load_pose
from jujune.engine import Snapshot
from jujune.paths import assets_dir

C_BG = (12, 6, 20, 255)
C_PANEL = (24, 12, 36, 255)
C_STROKE = (255, 77, 141, 255)
C_CYAN = (108, 255, 233, 255)
C_GOLD = (255, 211, 106, 255)
C_OK = (92, 255, 196, 255)
C_WARN = (255, 194, 74, 255)
C_LAG = (255, 75, 120, 255)
C_TEXT = (244, 234, 248, 255)
C_MUTED = (179, 155, 184, 255)
C_DIM = (40, 22, 56, 255)
C_GPU = (186, 140, 255, 255)

COMPACT = (680, 304)
EXPANDED = (780, 940)
MINI = (188, 188)


_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    key = (name, max(9, int(size)))
    cached = _FONT_CACHE.get(key)
    if cached is None:
        path = assets_dir() / "fonts" / name
        cached = ImageFont.truetype(str(path), key[1])
        _FONT_CACHE[key] = cached
    return cached


def _shadow_text(draw: ImageDraw.ImageDraw, xy, text, font, fill, shadow=(0, 0, 0, 150)):
    x, y = xy
    draw.text((x, y + 1), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=fill)


def _wrap(text: str, font, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = (cur + " " + word).strip()
        if font.getlength(trial) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _level_color(level: str) -> tuple[int, int, int, int]:
    if level == "lag":
        return C_LAG
    if level == "warn":
        return C_WARN
    return C_OK


def _fmt_ms(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if value >= 100:
        return f"{value:.0f}"
    return f"{value:.0f}" if value >= 10 else f"{value:.1f}"


def _temp_label(temps: dict[str, float]) -> str:
    if not temps:
        return "temp n/a"
    gpu = temps.get("gpu")
    cpu = None
    for key, value in temps.items():
        low = key.lower()
        if any(token in low for token in ("cpu", "package", "tctl", "ccd", "core")):
            cpu = value
            break
    if cpu is not None and gpu is not None:
        return f"{cpu:.0f}/{gpu:.0f}°"
    if gpu is not None:
        return f"GPU {gpu:.0f}°"
    if cpu is not None:
        return f"CPU {cpu:.0f}°"
    real = [v for k, v in temps.items() if "pressure" not in k.lower() and v > 20]
    if not real:
        return "temp n/a"
    return f"{max(real):.0f}°"


class Overlay:
    def __init__(self, on_close, on_quit, get_snapshot, scale: float = 1.0) -> None:
        self.on_close = on_close
        self.on_quit = on_quit
        self.get_snapshot = get_snapshot
        self.scale = max(0.85, min(1.35, scale))
        self.mode = "compact"
        self.hits: list[tuple[str, tuple[int, int, int, int]]] = []
        self._drag = None
        self._poses: dict[str, Image.Image] = {}
        self._photo = None
        self._alive = True
        self._t0 = time.time()

        self.root = tk.Tk()
        self.root.title("Jujune")
        self.root.configure(bg="#100818")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", 0.97)
        except tk.TclError:
            pass
        self._set_icon()
        self.label = tk.Label(self.root, bd=0, highlightthickness=0, bg="#100818")
        self.label.pack()
        self.label.bind("<ButtonPress-1>", self._down)
        self.label.bind("<B1-Motion>", self._move)
        self.label.bind("<ButtonRelease-1>", self._up)
        self.root.bind("<Escape>", lambda _e: self.toggle_compact())
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self._load_poses()
        self._apply_geometry()
        self._tick()

    def _set_icon(self) -> None:
        icon = assets_dir() / "jujune_icon.png"
        if not icon.exists():
            return
        try:
            img = Image.open(icon).resize((64, 64), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.root.iconphoto(True, photo)
            self._icon_photo = photo
        except OSError:
            pass

    def _load_poses(self) -> None:
        folder = assets_dir()
        for name in ("ok", "warn", "lag", "hot", "disk"):
            path = folder / f"jujune_{name}.png"
            if path.exists():
                self._poses[name] = load_pose(path, int(420 * self.scale))

    def _size(self) -> tuple[int, int]:
        if self.mode == "expanded":
            w, h = EXPANDED
        elif self.mode == "mini":
            w, h = MINI
        else:
            w, h = COMPACT
        return int(w * self.scale), int(h * self.scale)

    def _apply_geometry(self) -> None:
        w, h = self._size()
        self.root.geometry(f"{w}x{h}")

    def place(self, x: int, y: int) -> None:
        w, h = self._size()
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def position(self) -> tuple[int, int]:
        return self.root.winfo_x(), self.root.winfo_y()

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)

    def hide(self) -> None:
        self.root.withdraw()
        self.on_close()

    def destroy(self) -> None:
        self._alive = False
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def toggle_compact(self) -> None:
        self.mode = "compact" if self.mode == "expanded" else "expanded"
        self._apply_geometry()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._apply_geometry()

    def _down(self, event) -> None:
        action = self._hit(event.x, event.y)
        if action:
            self._pending = action
            self._drag = None
            return
        self._pending = None
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _move(self, event) -> None:
        if not self._drag:
            return
        x = event.x_root - self._drag[0]
        y = event.y_root - self._drag[1]
        w, h = self._size()
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _up(self, event) -> None:
        if self._drag:
            self._drag = None
            self._pending = None
            return
        action = getattr(self, "_pending", None)
        self._pending = None
        if action and action == self._hit(event.x, event.y):
            self._act(action)

    def _hit(self, x: int, y: int) -> Optional[str]:
        for name, (x1, y1, x2, y2) in self.hits:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return name
        return None

    def _act(self, action: str) -> None:
        if action == "close":
            if self.mode == "expanded":
                self.set_mode("compact")
            else:
                self.hide()
        elif action == "mini":
            self.set_mode("mini" if self.mode != "mini" else "compact")
        elif action == "expand":
            self.toggle_compact()
        elif action == "quit":
            self.on_quit()

    def _tick(self) -> None:
        if not self._alive:
            return
        try:
            snap = self.get_snapshot()
            img = self._render(snap)
            photo = ImageTk.PhotoImage(img)
            self._photo = photo
            self.label.configure(image=photo)
        except Exception as exc:
            if not getattr(self, "_ui_error", False):
                self._ui_error = True
                try:
                    from jujune.paths import data_dir

                    (data_dir() / "ui.log").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
                except OSError:
                    pass
        self.root.after(120, self._tick)

    def _render(self, snap: Optional[Snapshot]) -> Image.Image:
        w, h = self._size()
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        pulse = 0.5 + 0.5 * math.sin((time.time() - self._t0) * 4.2)
        level = snap.verdict.level if snap else "ok"
        accent = _level_color(level)
        if level == "lag":
            glow = tuple(min(255, int(c + 40 * pulse)) if i < 3 else 255 for i, c in enumerate(accent))
            accent = glow  # type: ignore[assignment]
        draw.rounded_rectangle((1, 1, w - 2, h - 2), radius=int(22 * self.scale), fill=C_BG, outline=accent, width=2)
        inner = C_CYAN if level == "ok" else accent
        draw.rounded_rectangle((5, 5, w - 6, h - 6), radius=int(18 * self.scale), outline=inner, width=1)
        self.hits = []
        if self.mode == "mini":
            self._draw_mini(img, draw, snap)
        elif self.mode == "expanded":
            self._draw_expanded(img, draw, snap)
        else:
            self._draw_compact(img, draw, snap)
        return img

    def _pose(self, snap: Optional[Snapshot], height: int) -> Image.Image:
        name = snap.verdict.pose if snap else "ok"
        src = self._poses.get(name) or self._poses.get("ok")
        if src is None:
            return Image.new("RGBA", (height // 2, height), (0, 0, 0, 0))
        im = src.copy()
        im.thumbnail((int(height * 0.85), height), Image.Resampling.LANCZOS)
        return im

    def _btn(self, draw, xy, label, action, w=28, h=22):
        x, y = xy
        box = (x, y, x + w, y + h)
        draw.rounded_rectangle(box, 8, fill=C_DIM, outline=C_STROKE)
        font = _font("Nunito-Bold.ttf", max(10, int(11 * self.scale)))
        tw = font.getlength(label)
        draw.text((x + (w - tw) / 2, y + 1), label, font=font, fill=C_TEXT)
        self.hits.append((action, box))

    def _draw_compact(self, img: Image.Image, draw: ImageDraw.ImageDraw, snap: Optional[Snapshot]) -> None:
        s = self.scale
        w, _h = img.size
        pose = self._pose(snap, int(132 * s))
        img.alpha_composite(pose, (int(8 * s), int(8 * s)))
        self.hits.append(("expand", (int(8 * s), int(8 * s), int(8 * s) + pose.width, int(8 * s) + pose.height)))
        x0 = int(128 * s)
        font_xs = _font("Nunito-Bold.ttf", int(11 * s))
        font_sm = _font("Nunito-SemiBold.ttf", int(12 * s))
        font_n = _font("Nunito-ExtraBold.ttf", int(28 * s))
        font_t = _font("Nunito-ExtraBold.ttf", int(13 * s))
        _shadow_text(draw, (x0, int(8 * s)), "JUJUNE", font_t, C_CYAN)
        title = snap.verdict.title if snap else "STARTING"
        color = _level_color(snap.verdict.level if snap else "ok")
        _shadow_text(draw, (x0 + int(78 * s), int(8 * s)), title, font_xs, color)
        self._btn(draw, (w - int(64 * s), int(8 * s)), "–", "mini", int(22 * s), int(20 * s))
        self._btn(draw, (w - int(36 * s), int(8 * s)), "×", "close", int(22 * s), int(20 * s))

        ping = _fmt_ms(snap.net.ping_ms if snap else None)
        _shadow_text(draw, (x0, int(26 * s)), ping, font_n, color)
        _shadow_text(draw, (x0 + int(font_n.getlength(ping) + 6), int(38 * s)), "ms", font_xs, C_MUTED)
        jit = snap.net.jitter_ms if snap else 0
        loss = snap.net.loss_pct if snap else 0
        graphs = snap.graphs if snap else None
        gpu = graphs.gpu_pct if graphs and graphs.gpu_pct is not None else (snap.sys.gpu_util if snap else None)
        cpu = graphs.cpu_pct if graphs else (snap.sys.cpu_pct if snap else 0)
        fps = graphs.fps if graphs else None
        ft = graphs.frametime_ms if graphs else None
        gpu_s = f"{gpu:.0f}%" if gpu is not None else "n/a"
        ft_s = f"{ft:.1f}ms" if ft is not None else "n/a"
        fps_s = f"{fps:.0f} FPS" if fps is not None else ""
        _shadow_text(
            draw,
            (x0 + int(96 * s), int(28 * s)),
            f"jit {jit:.0f}  loss {loss:.0f}%  CPU {cpu:.0f}%  GPU {gpu_s}",
            font_sm,
            C_TEXT,
        )
        _shadow_text(draw, (x0 + int(96 * s), int(46 * s)), f"frame {ft_s}  {fps_s}".strip(), font_xs, C_GOLD)

        hist = snap.net.history if snap else []
        self._graph(draw, x0, int(66 * s), w - x0 - int(14 * s), int(44 * s), hist, color, "ping", "collecting ping...")
        gap = int(8 * s)
        gw = (w - x0 - int(14 * s) - 2 * gap) // 3
        gy = int(116 * s)
        gh = int(118 * s)
        cpu_hist = graphs.cpu_history if graphs else []
        gpu_hist = graphs.gpu_history if graphs else []
        ft_hist = graphs.frametime_history if graphs else []
        self._graph(draw, x0, gy, gw, gh, cpu_hist, C_CYAN, f"CPU {cpu:.0f}%", "cpu...", 0, 100)
        self._graph(draw, x0 + gw + gap, gy, gw, gh, gpu_hist, C_GPU, f"GPU {gpu_s}", "gpu...", 0, 100)
        self._graph(
            draw,
            x0 + 2 * (gw + gap),
            gy,
            gw,
            gh,
            ft_hist,
            C_GOLD,
            f"FT {ft_s} {fps_s}".strip(),
            "waiting for game...",
            0,
            50,
            guides=[(16.67, "60", (92, 255, 196, 140)), (33.33, "30", (255, 75, 120, 140))],
        )
        quote = snap.verdict.quote if snap else "Warming up the channel..."
        _shadow_text(draw, (x0, int(242 * s)), quote[:62], font_xs, C_GOLD)
        if snap and snap.sys.disk_util is not None:
            disk_s = f"DISK {snap.sys.disk_util:.0f}%"
        elif snap:
            disk_s = f"DISK {snap.sys.disk_read_mb + snap.sys.disk_write_mb:.0f}MB/s"
        else:
            disk_s = "DISK —"
        temp = _temp_label(snap.sys.temps if snap else {})
        app = graphs.frame_app if graphs and graphs.frame_app else ""
        _shadow_text(draw, (x0, int(262 * s)), f"{disk_s}   {temp}   {app}"[:70], font_sm, C_MUTED)

    def _draw_mini(self, img: Image.Image, draw: ImageDraw.ImageDraw, snap: Optional[Snapshot]) -> None:
        s = self.scale
        pose = self._pose(snap, int(118 * s))
        img.alpha_composite(pose, ((img.width - pose.width) // 2, 4))
        color = _level_color(snap.verdict.level if snap else "ok")
        ping = _fmt_ms(snap.net.ping_ms if snap else None)
        font = _font("Nunito-ExtraBold.ttf", int(22 * s))
        tw = font.getlength(f"{ping} ms")
        _shadow_text(draw, ((img.width - tw) / 2, int(128 * s)), f"{ping} ms", font, color)
        self.hits.append(("expand", (0, 0, img.width, img.height - 28)))
        self._btn(draw, (img.width - int(28 * s), 8), "×", "close", int(22 * s), int(20 * s))

    def _draw_expanded(self, img: Image.Image, draw: ImageDraw.ImageDraw, snap: Optional[Snapshot]) -> None:
        s = self.scale
        w, _h = img.size
        font_xs = _font("Nunito-Bold.ttf", int(11 * s))
        font_sm = _font("Nunito-SemiBold.ttf", int(13 * s))
        font_n = _font("Nunito-ExtraBold.ttf", int(36 * s))
        font_h = _font("Nunito-ExtraBold.ttf", int(16 * s))
        _shadow_text(draw, (int(18 * s), int(12 * s)), "JUJUNE · LAG WATCH", font_h, C_CYAN)
        self._btn(draw, (w - int(96 * s), int(12 * s)), "–", "mini", int(22 * s), int(22 * s))
        self._btn(draw, (w - int(66 * s), int(12 * s)), "▾", "expand", int(22 * s), int(22 * s))
        self._btn(draw, (w - int(36 * s), int(12 * s)), "×", "close", int(22 * s), int(22 * s))

        pose = self._pose(snap, int(220 * s))
        img.alpha_composite(pose, (int(16 * s), int(42 * s)))
        color = _level_color(snap.verdict.level if snap else "ok")
        bx, by = int(200 * s), int(44 * s)
        draw.rounded_rectangle((bx, by, w - int(18 * s), int(200 * s)), 16, fill=C_PANEL, outline=color)
        title = snap.verdict.title if snap else "STARTING"
        _shadow_text(draw, (bx + 14, by + 10), title, font_h, color)
        ping = _fmt_ms(snap.net.ping_ms if snap else None)
        _shadow_text(draw, (bx + 14, by + 34), ping, font_n, color)
        _shadow_text(draw, (bx + 14 + font_n.getlength(ping) + 8, by + 54), "ms ping", font_sm, C_MUTED)
        graphs = snap.graphs if snap else None
        gpu = graphs.gpu_pct if graphs and graphs.gpu_pct is not None else (snap.sys.gpu_util if snap else None)
        cpu = graphs.cpu_pct if graphs else (snap.sys.cpu_pct if snap else 0)
        fps = graphs.fps if graphs else None
        ft = graphs.frametime_ms if graphs else None
        gpu_s = f"{gpu:.0f}%" if gpu is not None else "n/a"
        ft_s = f"{ft:.1f} ms" if ft is not None else "n/a"
        fps_s = f"{fps:.0f} FPS" if fps is not None else "FPS n/a"
        _shadow_text(draw, (bx + 14, by + 88), f"CPU {cpu:.0f}%   GPU {gpu_s}   {ft_s}   {fps_s}", font_sm, C_TEXT)
        quote = snap.verdict.quote if snap else "Warming up the channel..."
        y = by + 112
        for line in _wrap(quote, font_sm, w - bx - int(40 * s))[:3]:
            _shadow_text(draw, (bx + 14, y), line, font_sm, C_GOLD)
            y += int(16 * s)

        detail = snap.verdict.detail if snap else "Collecting first samples of network, disk and temps."
        y = int(212 * s)
        for line in _wrap(detail, font_sm, w - int(36 * s))[:2]:
            _shadow_text(draw, (int(18 * s), y), line, font_xs, C_TEXT)
            y += int(15 * s)

        hist = snap.net.history if snap else []
        gx = int(18 * s)
        gw = w - int(36 * s)
        self._graph(draw, gx, int(248 * s), gw, int(88 * s), hist, color, "PING · last ~90s", "collecting ping...")
        cpu_hist = graphs.cpu_history if graphs else []
        gpu_hist = graphs.gpu_history if graphs else []
        ft_hist = graphs.frametime_history if graphs else []
        half = (gw - int(10 * s)) // 2
        self._graph(draw, gx, int(344 * s), half, int(120 * s), cpu_hist, C_CYAN, f"CPU {cpu:.0f}%", "cpu...", 0, 100)
        self._graph(
            draw,
            gx + half + int(10 * s),
            int(344 * s),
            half,
            int(120 * s),
            gpu_hist,
            C_GPU,
            f"GPU {gpu_s}",
            "gpu...",
            0,
            100,
        )
        app = graphs.frame_app if graphs and graphs.frame_app else "game"
        self._graph(
            draw,
            gx,
            int(472 * s),
            gw,
            int(140 * s),
            ft_hist,
            C_GOLD,
            f"FRAME TIME  {ft_s}   {fps_s}   {app}",
            "waiting for game presents...",
            0,
            50,
            guides=[(16.67, "60", (92, 255, 196, 140)), (33.33, "30", (255, 75, 120, 140))],
        )

        net = snap.net if snap else None
        sysn = snap.sys if snap else None
        disk_mb = (sysn.disk_read_mb + sysn.disk_write_mb) if sysn else 0.0
        if sysn and sysn.disk_util is not None:
            disk_value = f"{sysn.disk_util:.0f}%  {sysn.disk_read_mb:.0f}↓{sysn.disk_write_mb:.0f}↑"
            disk_metric = sysn.disk_util
        else:
            disk_value = f"{disk_mb:.0f} MB/s"
            disk_metric = min(100.0, disk_mb / 2.0)
        left_rows = [
            ("WAN", f"{_fmt_ms(net.ping_ms if net else None)} ms", net.ping_ms if net else 0, 80),
            ("Router", f"{_fmt_ms(net.gateway_ms if net else None)} ms", net.gateway_ms if net else 0, 40),
            ("Jitter", f"{(net.jitter_ms if net else 0):.0f} ms", net.jitter_ms if net else 0, 25),
            ("Loss", f"{(net.loss_pct if net else 0):.0f}%", net.loss_pct if net else 0, 5),
        ]
        right_rows = [
            ("CPU", f"{cpu:.0f}%", cpu, 90),
            ("GPU", gpu_s, gpu if gpu is not None else 0, 90),
            ("RAM", f"{(sysn.ram_pct if sysn else 0):.0f}%", sysn.ram_pct if sysn else 0, 92),
            ("Disk", disk_value, disk_metric, 80),
            ("Temp", _temp_label(sysn.temps if sysn else {}), self._temp_pct(sysn.temps if sysn else {}), 88),
        ]
        y = int(622 * s)
        col_w = half
        for i, row in enumerate(left_rows):
            self._meter_row(draw, gx, y + i * int(32 * s), col_w, *row, s)
        for i, row in enumerate(right_rows):
            self._meter_row(draw, gx + half + int(10 * s), y + i * int(32 * s), col_w, *row, s)

        extra = ""
        if net and net.extra_ms:
            bits = [f"{host} {_fmt_ms(ms)}" for host, ms in net.extra_ms.items()]
            extra = " · game: " + ", ".join(bits)
        wifi = "Wi-Fi" if net and net.wifi else "Ethernet"
        mode = net.ping_mode.upper() if net else "—"
        _shadow_text(
            draw,
            (gx, int(790 * s)),
            f"{wifi} · {mode} · DNS {_fmt_ms(net.dns_ms if net else None)} ms{extra}"[:88],
            font_xs,
            C_MUTED,
        )
        if sysn:
            _shadow_text(
                draw,
                (gx, int(806 * s)),
                f"top CPU: {sysn.top_cpu[:28]} · RAM: {sysn.top_ram[:22]}",
                font_xs,
                C_MUTED,
            )

        _shadow_text(draw, (gx, int(830 * s)), "LAST HITS", font_h, C_STROKE)
        events = snap.events if snap else []
        y = int(852 * s)
        if not events:
            _shadow_text(draw, (gx, y), "Quiet for now. Spikes land here.", font_xs, C_MUTED)
        for event in events[:4]:
            line = f"{event.get('when', '')}  {event.get('title', '')}"
            _shadow_text(draw, (gx, y), line[:72], font_xs, C_WARN)
            y += int(16 * s)

    def _temp_pct(self, temps: dict[str, float]) -> float:
        real = [v for k, v in temps.items() if "pressure" not in k.lower() and v > 20]
        if not real:
            return 0.0
        return max(real)

    def _meter_row(self, draw, x, y, w, label, value, metric, warn_at, s):
        font = _font("Nunito-Bold.ttf", int(12 * s))
        _shadow_text(draw, (x, y), label, font, C_MUTED)
        tw = font.getlength(str(value))
        _shadow_text(draw, (x + w - tw, y), str(value), font, C_TEXT)
        bar_y = y + int(16 * s)
        draw.rounded_rectangle((x, bar_y, x + w, bar_y + int(7 * s)), 4, fill=C_DIM)
        pct = 0.0 if metric is None else max(0.0, min(1.0, float(metric) / 100.0))
        if warn_at and metric is not None:
            pct = max(0.0, min(1.0, float(metric) / max(warn_at * 1.15, 1)))
        fw = int(w * pct)
        color = C_OK
        if metric is not None and warn_at:
            if metric >= warn_at:
                color = C_LAG
            elif metric >= warn_at * 0.72:
                color = C_WARN
        if fw > 6:
            draw.rounded_rectangle((x, bar_y, x + fw, bar_y + int(7 * s)), 4, fill=color)

    def _graph(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        w: int,
        h: int,
        history: list,
        color,
        title: str,
        empty: str,
        y_min: Optional[float] = None,
        y_max: Optional[float] = None,
        guides: Optional[list] = None,
    ) -> None:
        draw.rounded_rectangle((x, y, x + w, y + h), 10, fill=C_PANEL, outline=C_DIM)
        font = _font("Nunito-Bold.ttf", 11)
        _shadow_text(draw, (x + 8, y + 4), title, font, C_MUTED)
        vals: list[Optional[float]] = []
        for item in history[-120:]:
            if item is None:
                vals.append(None)
            else:
                try:
                    vals.append(float(item))
                except (TypeError, ValueError):
                    vals.append(None)
        present = [v for v in vals if v is not None]
        if len(present) < 2:
            draw.text((x + 10, y + h / 2 - 6), empty, font=font, fill=C_MUTED)
            return
        lo = min(present) if y_min is None else y_min
        hi = max(present) if y_max is None else y_max
        if y_min is None:
            lo = min(lo, min(present))
        if y_max is None:
            hi = max(max(present), lo + 1)
        if hi <= lo:
            hi = lo + 1
        plot_top = y + 20
        plot_bot = y + h - 6
        plot_h = max(8, plot_bot - plot_top)
        if guides:
            for gval, glabel, gcolor in guides:
                if gval < lo or gval > hi:
                    continue
                gy = plot_bot - (gval - lo) / (hi - lo) * plot_h
                draw.line([(x + 6, gy), (x + w - 6, gy)], fill=gcolor, width=1)
                draw.text((x + w - 28, gy - 8), glabel, font=font, fill=gcolor)
        pts = []
        for i, v in enumerate(vals):
            if v is None:
                continue
            clamped = min(hi, max(lo, v))
            px = x + 6 + i * (w - 12) / max(1, len(vals) - 1)
            py = plot_bot - (clamped - lo) / (hi - lo) * plot_h
            pts.append((px, py))
        if len(pts) >= 2:
            fill = list(pts) + [(pts[-1][0], plot_bot), (pts[0][0], plot_bot)]
            draw.polygon(fill, fill=(color[0], color[1], color[2], 50))
            draw.line(pts, fill=color, width=2)

    def _sparkline(self, draw, x, y, w, h, history: list, color) -> None:
        self._graph(draw, x, y, w, h, history, color, "", "collecting ping graph...")

    def loop(self) -> None:
        self.root.mainloop()
