from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from jujune import __version__
from jujune.config import load_config
from jujune.paths import data_dir

SOURCE_REPO = "artem-24paybase/jujune-internet-tester"
RELEASES_REPO = "artem-24paybase/jujune-releases"
USER_AGENT = f"Jujune/{__version__}"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


class UpdateError(Exception):
    pass


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _token() -> Optional[str]:
    for key in ("JUJUNE_GITHUB_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(key)
        if value:
            return value.strip()
    cfg = load_config()
    extra = cfg.get("github_token")
    if extra:
        return str(extra).strip()
    gh = shutil.which("gh")
    if not gh:
        return None
    kwargs: dict = {"capture_output": True, "text": True, "timeout": 8}
    if sys.platform == "win32":
        kwargs["creationflags"] = CREATE_NO_WINDOW
    try:
        proc = subprocess.run([gh, "auth", "token"], **kwargs)
    except Exception:
        return None
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return None


def _repos() -> list[str]:
    cfg = load_config()
    preferred = str(cfg.get("releases_repo") or "").strip()
    order = []
    if preferred:
        order.append(preferred)
    order.extend([RELEASES_REPO, SOURCE_REPO])
    seen: set[str] = set()
    out: list[str] = []
    for repo in order:
        if repo and repo not in seen:
            seen.add(repo)
            out.append(repo)
    return out


def _parse_version(raw: str) -> tuple[int, ...]:
    text = raw.strip().lstrip("vV")
    parts: list[int] = []
    for item in text.replace("-", ".").split("."):
        if item.isdigit():
            parts.append(int(item))
        else:
            break
    return tuple(parts or [0])


def _newer(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)


def _request(url: str, token: Optional[str] = None, accept: str = "application/vnd.github+json") -> bytes:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:180]
        raise UpdateError(f"GitHub {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"Network error: {exc.reason}") from exc


def _download(url: str, dest: Path, token: Optional[str], progress: Callable[[str], None], total: int = 0) -> None:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/octet-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=60) as resp, tmp.open("wb") as fh:
            size = int(resp.headers.get("Content-Length") or total or 0)
            done = 0
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if size:
                    progress(f"DOWNLOADING {min(99, done * 100 // size)}%")
                else:
                    progress(f"DOWNLOADING {done // (1024 * 1024)} MB")
        tmp.replace(dest)
    except urllib.error.HTTPError as exc:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise UpdateError(f"Download failed ({exc.code})") from exc
    except urllib.error.URLError as exc:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise UpdateError(f"Download failed: {exc.reason}") from exc
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def _pick_asset(release: dict) -> dict:
    assets = release.get("assets") or []
    for asset in assets:
        name = str(asset.get("name") or "").lower()
        if name == "jujune.exe":
            return asset
    for asset in assets:
        name = str(asset.get("name") or "").lower()
        if name.endswith(".exe"):
            return asset
    raise UpdateError("Release has no EXE")


def fetch_latest() -> tuple[dict, Optional[str]]:
    token = _token()
    last_error: Optional[UpdateError] = None
    for repo in _repos():
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        try:
            payload = json.loads(_request(url, token=token).decode("utf-8"))
        except UpdateError as exc:
            last_error = exc
            continue
        if not isinstance(payload, dict) or payload.get("message") == "Not Found":
            last_error = UpdateError(f"No releases in {repo}")
            continue
        if not payload.get("assets"):
            last_error = UpdateError(f"No files in {repo} release")
            continue
        return payload, token
    raise last_error or UpdateError("No GitHub release found")


def _apply_windows(new_exe: Path, restart: Callable[[], None]) -> None:
    current = Path(sys.executable).resolve()
    bat = data_dir() / "apply_update.bat"
    old_name = current.name + ".old"
    bat.write_text(
        "\r\n".join(
            [
                "@echo off",
                "setlocal",
                f'set "TARGET={current}"',
                f'set "SOURCE={new_exe}"',
                f"set PID={os.getpid()}",
                ":wait",
                'tasklist /FI "PID eq %PID%" | find "%PID%" >nul',
                "if not errorlevel 1 (",
                "  timeout /t 1 /nobreak >nul",
                "  goto wait",
                ")",
                f'if exist "{current.parent / old_name}" del /f /q "{current.parent / old_name}"',
                f'ren "%TARGET%" "{old_name}"',
                'copy /y "%SOURCE%" "%TARGET%"',
                'start "" "%TARGET%"',
                'del /f /q "%SOURCE%"',
                'del /f /q "%~f0"',
            ]
        )
        + "\r\n",
        encoding="utf-8",
    )
    flags = CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat)],
        cwd=str(current.parent),
        close_fds=True,
        creationflags=flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    restart()


def run_update(progress: Callable[[str], None], restart: Callable[[], None]) -> None:
    progress("CHECKING...")
    release, token = fetch_latest()
    tag = str(release.get("tag_name") or release.get("name") or "")
    if not tag:
        raise UpdateError("Release has no version")
    if not _newer(tag, __version__):
        progress(f"UP TO DATE  v{__version__}")
        return
    asset = _pick_asset(release)
    url = asset.get("url") or asset.get("browser_download_url")
    if not url:
        raise UpdateError("Release has no download URL")
    size = int(asset.get("size") or 0)
    dest = data_dir() / "Jujune-update.exe"
    progress("DOWNLOADING...")
    api_url = str(asset.get("url") or "")
    public_url = str(asset.get("browser_download_url") or url)
    try:
        _download(public_url, dest, None, progress, size)
    except UpdateError:
        if token and api_url.startswith("https://api.github.com/"):
            _download(api_url, dest, token, progress, size)
        else:
            raise
    if dest.stat().st_size < 1_000_000:
        dest.unlink(missing_ok=True)
        raise UpdateError("Downloaded file is too small")
    if not getattr(sys, "frozen", False):
        progress(f"SAVED {dest.name}  v{tag.lstrip('vV')}")
        return
    if sys.platform != "win32":
        progress(f"SAVED {dest}  (not Windows)")
        return
    progress("RESTARTING...")
    _apply_windows(dest, restart)
