from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from jujune.paths import data_dir

DEFAULTS = {
    "extra_hosts": [],
    "compact": True,
    "sound": True,
    "position": None,
    "scale": 1.0,
    "wan_host": "1.1.1.1",
    "wan_host_alt": "8.8.8.8",
    "_hint": "extra_hosts — game server IP or hostname, e.g. [\"11.22.33.44\"]",
}


def config_path() -> Path:
    return data_dir() / "config.json"


def load_config() -> dict:
    path = config_path()
    cfg = deepcopy(DEFAULTS)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg.update(loaded)
                cfg["_hint"] = DEFAULTS["_hint"]
        except (OSError, json.JSONDecodeError):
            pass
    else:
        save_config(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    path = config_path()
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
