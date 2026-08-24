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

COMPACT = (456, 136)
EXPANDED = (456, 688)
MINI = (168, 168)


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
        pose = self._pose(snap, int(124 * s))
        img.alpha_composite(pose, (int(8 * s), int(6 * s)))
        self.hits.append(("expand", (int(8 * s), int(6 * s), int(8 * s) + pose.width, int(6 * s) + pose.height)))
        x0 = int(118 * s)
        font_xs = _font("Nunito-Bold.ttf", int(11 * s))
        font_sm = _font("Nunito-SemiBold.ttf", int(12 * s))
        font_n = _font("Nunito-ExtraBold.ttf", int(28 * s))
        font_t = _font("Nunito-ExtraBold.ttf", int(13 * s))
        _shadow_text(draw, (x0, int(8 * s)), "JUJUNE", font_t, C_CYAN)
        title = snap.verdict.title if snap else "STARTING"
        color = _level_color(snap.verdict.level if snap else "ok")
        _shadow_text(draw, (x0 + int(78 * s), int(8 * s)), title, font_xs, color)
        self._btn(draw, (int(392 * s), int(8 * s)), "–", "mini", int(22 * s), int(20 * s))
        self._btn(draw, (int(420 * s), int(8 * s)), "×", "close", int(22 * s), int(20 * s))

        ping = _fmt_ms(snap.net.ping_ms if snap else None)
        _shadow_text(draw, (x0, int(26 * s)), ping, font_n, color)
        _shadow_text(draw, (x0 + int(font_n.getlength(ping) + 6), int(38 * s)), "ms", font_xs, C_MUTED)
        jit = snap.net.jitter_ms if snap else 0
        loss = snap.net.loss_pct if snap else 0
        _shadow_text(draw, (x0 + int(96 * s), int(32 * s)), f"jit {jit:.0f}   loss {loss:.0f}%", font_sm, C_TEXT)

        hist = snap.net.history if snap else []
        self._sparkline(draw, int(x0), int(66 * s), int(250 * s), int(28 * s), hist, color)
        cpu = snap.sys.cpu_pct if snap else 0
        if snap and snap.sys.disk_util is not None:
            disk_s = f"DISK {snap.sys.disk_util:.0f}%"
        elif snap:
            disk_s = f"DISK {snap.sys.disk_read_mb + snap.sys.disk_write_mb:.0f}MB/s"
        else:
            disk_s = "DISK —"
        temp = _temp_label(snap.sys.temps if snap else {})
        line = f"CPU {cpu:.0f}%   {disk_s}   {temp}"
        _shadow_text(draw, (x0, int(98 * s)), line, font_sm, C_MUTED)
        quote = snap.verdict.quote if snap else "Warming up the channel..."
        _shadow_text(draw, (x0, int(114 * s)), quote[:54], font_xs, C_GOLD)

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
        font_xs = _font("Nunito-Bold.ttf", int(11 * s))
        font_sm = _font("Nunito-SemiBold.ttf", int(13 * s))
        font_n = _font("Nunito-ExtraBold.ttf", int(36 * s))
        font_h = _font("Nunito-ExtraBold.ttf", int(16 * s))
        _shadow_text(draw, (int(18 * s), int(12 * s)), "JUJUNE · LAG WATCH", font_h, C_CYAN)
        self._btn(draw, (int(360 * s), int(12 * s)), "–", "mini", int(22 * s), int(22 * s))
        self._btn(draw, (int(392 * s), int(12 * s)), "▾", "expand", int(22 * s), int(22 * s))
        self._btn(draw, (int(424 * s), int(12 * s)), "×", "close", int(22 * s), int(22 * s))

        pose = self._pose(snap, int(248 * s))
        img.alpha_composite(pose, (int(16 * s), int(40 * s)))
        color = _level_color(snap.verdict.level if snap else "ok")
        bx, by = int(168 * s), int(48 * s)
        draw.rounded_rectangle((bx, by, int(438 * s), int(168 * s)), 16, fill=C_PANEL, outline=color)
        title = snap.verdict.title if snap else "STARTING"
        _shadow_text(draw, (bx + 14, by + 10), title, font_h, color)
        ping = _fmt_ms(snap.net.ping_ms if snap else None)
        _shadow_text(draw, (bx + 14, by + 34), ping, font_n, color)
        _shadow_text(draw, (bx + 14 + font_n.getlength(ping) + 8, by + 54), "ms ping", font_sm, C_MUTED)
        quote = snap.verdict.quote if snap else "Warming up the channel..."
        y = by + 92
        for line in _wrap(quote, font_sm, int(240 * s))[:3]:
            _shadow_text(draw, (bx + 14, y), line, font_sm, C_GOLD)
            y += int(16 * s)

        detail = snap.verdict.detail if snap else "Collecting first samples of network, disk and temps."
        y = int(176 * s)
        for line in _wrap(detail, font_sm, int(420 * s))[:3]:
            _shadow_text(draw, (int(18 * s), y), line, font_xs, C_TEXT)
            y += int(15 * s)

        hist = snap.net.history if snap else []
        self._sparkline(draw, int(18 * s), int(228 * s), int(420 * s), int(56 * s), hist, color)
        _shadow_text(draw, (int(18 * s), int(286 * s)), "ping, last ~90s", font_xs, C_MUTED)

        net = snap.net if snap else None
        sysn = snap.sys if snap else None
        disk_mb = (sysn.disk_read_mb + sysn.disk_write_mb) if sysn else 0.0
        if sysn and sysn.disk_util is not None:
            disk_value = f"{sysn.disk_util:.0f}%  {sysn.disk_read_mb:.0f}↓{sysn.disk_write_mb:.0f}↑"
            disk_metric = sysn.disk_util
        else:
            disk_value = f"{disk_mb:.0f} MB/s"
            disk_metric = min(100.0, disk_mb / 2.0)
        rows = [
            ("WAN", f"{_fmt_ms(net.ping_ms if net else None)} ms", net.ping_ms if net else 0, 80),
            ("Router", f"{_fmt_ms(net.gateway_ms if net else None)} ms", net.gateway_ms if net else 0, 40),
            ("Jitter", f"{(net.jitter_ms if net else 0):.0f} ms", net.jitter_ms if net else 0, 25),
            ("Loss", f"{(net.loss_pct if net else 0):.0f}%", net.loss_pct if net else 0, 5),
            ("CPU", f"{(sysn.cpu_pct if sysn else 0):.0f}%", sysn.cpu_pct if sysn else 0, 90),
            ("RAM", f"{(sysn.ram_pct if sysn else 0):.0f}%", sysn.ram_pct if sysn else 0, 92),
            ("Disk", disk_value, disk_metric, 80),
            ("Temp", _temp_label(sysn.temps if sysn else {}), self._temp_pct(sysn.temps if sysn else {}), 88),
        ]
        y = int(308 * s)
        for label, value, metric, warn_at in rows:
            self._meter_row(draw, int(18 * s), y, int(420 * s), label, value, metric, warn_at, s)
            y += int(32 * s)

        extra = ""
        if net and net.extra_ms:
            bits = [f"{host} {_fmt_ms(ms)}" for host, ms in net.extra_ms.items()]
            extra = " · game: " + ", ".join(bits)
        wifi = "Wi-Fi" if net and net.wifi else "Ethernet"
        mode = net.ping_mode.upper() if net else "—"
        _shadow_text(
            draw,
            (int(18 * s), int(566 * s)),
            f"{wifi} · {mode} · DNS {_fmt_ms(net.dns_ms if net else None)} ms{extra}"[:62],
            font_xs,
            C_MUTED,
        )
        if sysn:
            _shadow_text(
                draw,
                (int(18 * s), int(582 * s)),
                f"top CPU: {sysn.top_cpu[:28]} · RAM: {sysn.top_ram[:22]}",
                font_xs,
                C_MUTED,
            )

        _shadow_text(draw, (int(18 * s), int(606 * s)), "LAST HITS", font_h, C_STROKE)
        events = snap.events if snap else []
        y = int(628 * s)
        if not events:
            _shadow_text(draw, (int(18 * s), y), "Quiet for now. Spikes land here.", font_xs, C_MUTED)
        for event in events[:3]:
            line = f"{event.get('when', '')}  {event.get('title', '')}"
            _shadow_text(draw, (int(18 * s), y), line[:58], font_xs, C_WARN)
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

    def _sparkline(self, draw, x, y, w, h, history: list, color) -> None:
        draw.rounded_rectangle((x, y, x + w, y + h), 10, fill=C_PANEL, outline=C_DIM)
        vals = []
        last = 20.0
        for item in history[-80:]:
            if item is None:
                vals.append(None)
            else:
                last = float(item)
                vals.append(last)
        if len(vals) < 2:
            font = _font("Nunito-Bold.ttf", 11)
            draw.text((x + 10, y + h / 2 - 8), "collecting ping graph...", font=font, fill=C_MUTED)
            return
        present = [v for v in vals if v is not None]
        if not present:
            return
        lo = min(present)
        hi = max(max(present), lo + 8)
        pts = []
        for i, v in enumerate(vals):
            if v is None:
                continue
            px = x + 6 + i * (w - 12) / max(1, len(vals) - 1)
            py = y + h - 6 - (v - lo) / (hi - lo) * (h - 12)
            pts.append((px, py))
        if len(pts) >= 2:
            fill = list(pts) + [(pts[-1][0], y + h - 6), (pts[0][0], y + h - 6)]
            draw.polygon(fill, fill=(color[0], color[1], color[2], 45))
            draw.line(pts, fill=color, width=2)

    def loop(self) -> None:
        self.root.mainloop()
