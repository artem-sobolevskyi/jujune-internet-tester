from __future__ import annotations

import argparse
import subprocess
import sys
import threading

from PIL import Image

from jujune.config import load_config, save_config
from jujune.engine import Engine
from jujune.overlay import Overlay
from jujune.paths import assets_dir, data_dir


def _enable_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _beep() -> None:
    try:
        if sys.platform == "win32":
            import winsound

            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        elif sys.platform == "darwin":
            subprocess.Popen(
                ["afplay", "/System/Library/Sounds/Tink.aiff"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            sys.stdout.write("\a")
            sys.stdout.flush()
    except Exception:
        pass


def _play_mp3(path) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.Popen(
                ["afplay", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        mci = ctypes.windll.winmm.mciSendStringW
        mci.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HANDLE]
        mci.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(255)
        src = str(path).replace("/", "\\")
        alias = "jujune_nyaaa"
        mci(f"close {alias}", buf, 254, None)
        err = mci(f'open "{src}" type mpegvideo alias {alias}', buf, 254, None)
        if err:
            err = mci(f'open "{src}" alias {alias}', buf, 254, None)
        if err:
            return
        mci(f"play {alias} wait", buf, 254, None)
        mci(f"close {alias}", buf, 254, None)
    except Exception:
        pass


def _play_startup_sound() -> None:
    path = assets_dir() / "nyaaa.mp3"
    if not path.is_file():
        return
    threading.Thread(target=_play_mp3, args=(path,), daemon=True, name="jujune-nyaaa").start()


def _tray_image() -> Image.Image:
    path = assets_dir() / "jujune_icon.png"
    img = Image.open(path).convert("RGBA")
    img.thumbnail((64, 64), Image.Resampling.LANCZOS)
    return img


def _print_test() -> int:
    import time

    engine = Engine()
    engine._tick()
    time.sleep(1.05)
    for _ in range(3):
        engine._tick()
        time.sleep(1.05)
    snap = engine.snapshot()
    if snap is None:
        print("No sample")
        return 1
    net, sysn, ver = snap.net, snap.sys, snap.verdict
    print(f"ping: {net.ping_ms} ms ({net.ping_mode})  router: {net.gateway_ms}  jitter: {net.jitter_ms}  loss: {net.loss_pct}%")
    disk = f"{sysn.disk_util}%" if sysn.disk_util is not None else "n/a"
    print(f"cpu: {sysn.cpu_pct}%  ram: {sysn.ram_pct}%  disk: {disk}  {sysn.disk_read_mb}R {sysn.disk_write_mb}W MB/s")
    print(f"temp: {sysn.temps}  gpu: {sysn.gpu_util}")
    print(f"wifi: {net.wifi}  iface: {net.iface}")
    print(f"verdict: {ver.level} / {ver.cause} / {ver.title}")
    print(ver.quote)
    print(ver.detail)
    print(f"logs: {data_dir()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jujune", add_help=True)
    parser.add_argument("--test", action="store_true", help="Print one sample to the console, no window")
    parser.add_argument("--expanded", action="store_true", help="Start in the full panel")
    args = parser.parse_args(argv)
    if args.test:
        return _print_test()

    _enable_dpi()
    cfg = load_config()
    engine = Engine()
    engine.start()

    overlay_ref: dict = {}
    tray_ref: dict = {}
    last_alert = {"key": None}

    def get_snap():
        return engine.snapshot()

    def persist() -> None:
        overlay = overlay_ref.get("w")
        if overlay is None:
            return
        cfg["position"] = list(overlay.position())
        cfg["compact"] = overlay.mode != "expanded"
        save_config(cfg)

    def on_close() -> None:
        persist()

    def on_quit() -> None:
        persist()
        engine.stop()
        tray = tray_ref.get("icon")
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                pass
        overlay = overlay_ref.get("w")
        if overlay is not None:
            overlay.destroy()

    overlay = Overlay(
        on_close=on_close,
        on_quit=on_quit,
        get_snapshot=get_snap,
        scale=float(cfg.get("scale") or 1.0),
    )
    overlay_ref["w"] = overlay

    def start_update() -> None:
        if overlay.update_busy:
            return
        overlay.update_busy = True
        overlay.update_label = "CHECKING..."
        overlay.update_detail = ""

        def progress(msg: str) -> None:
            overlay.update_label = msg

        def work() -> None:
            from jujune.update import UpdateError, run_update

            try:
                run_update(progress=progress, restart=lambda: overlay.root.after(0, on_quit))
            except UpdateError as exc:
                overlay.update_label = "CHECK FOR UPDATE"
                overlay.update_detail = str(exc)[:88]
            except Exception as exc:
                overlay.update_label = "CHECK FOR UPDATE"
                overlay.update_detail = f"{type(exc).__name__}: {exc}"[:88]
            finally:
                if not overlay.update_label.startswith("RESTARTING"):
                    overlay.update_busy = False

        threading.Thread(target=work, name="jujune-update", daemon=True).start()

    overlay.on_update = start_update
    if args.expanded:
        overlay.set_mode("expanded")
    elif cfg.get("compact", True):
        overlay.set_mode("compact")
    overlay.root.update_idletasks()
    pos = cfg.get("position")
    if isinstance(pos, list) and len(pos) == 2:
        overlay.place(int(pos[0]), int(pos[1]))
    else:
        sw = overlay.root.winfo_screenwidth()
        w, h = overlay._size()
        overlay.place(max(12, sw - w - 28), 42)

    def watch_lag() -> None:
        if not overlay._alive:
            return
        snap = engine.snapshot()
        if snap and snap.verdict.level == "lag" and cfg.get("sound", True):
            key = (snap.verdict.cause, int(snap.ts) // 10)
            if key != last_alert["key"]:
                last_alert["key"] = key
                _beep()
        overlay.root.after(400, watch_lag)

    overlay.root.after(800, watch_lag)

    def show_overlay(mode: str | None = None) -> None:
        def _do() -> None:
            if mode:
                overlay.set_mode(mode)
            overlay.show()

        overlay.root.after(0, _do)

    try:
        import pystray

        menu = pystray.Menu(
            pystray.MenuItem("Show", lambda: show_overlay()),
            pystray.MenuItem("Compact", lambda: show_overlay("compact")),
            pystray.MenuItem("Expanded", lambda: show_overlay("expanded")),
            pystray.MenuItem("Mini", lambda: show_overlay("mini")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Check for update", lambda: overlay.root.after(0, start_update)),
            pystray.MenuItem("Quit", lambda: overlay.root.after(0, on_quit)),
        )
        icon = pystray.Icon("Jujune", _tray_image(), "Jujune", menu)
        tray_ref["icon"] = icon
        if hasattr(icon, "run_detached"):
            icon.run_detached()
        else:
            threading.Thread(target=icon.run, daemon=True).start()
    except Exception:
        pass

    _play_startup_sound()
    overlay.loop()
    engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
