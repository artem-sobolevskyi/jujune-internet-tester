from __future__ import annotations

import os
import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def assets_dir() -> Path:
    return app_root() / "assets"


def data_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home())) / "Jujune"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "Jujune"
    else:
        root = Path.home() / ".jujune"
    root.mkdir(parents=True, exist_ok=True)
    return root
