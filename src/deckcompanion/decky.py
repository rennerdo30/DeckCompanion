"""Decky discovery and explicit Desktop installer handoff; never handles credentials."""
import json
import os
from pathlib import Path
import shutil
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from . import net, steam
from .common import Error, atomic, read_json, state, write_json

RELEASE_URL = "https://api.github.com/repos/SteamDeckHomebrew/decky-loader/releases/latest"
INSTALL_COMMAND = "curl -L https://github.com/SteamDeckHomebrew/decky-installer/releases/latest/download/install_release.sh | sh"
CACHE_SECONDS = 6 * 3600
PLUGIN_FILES = ("main.py", "plugin.json", "package.json", "dist/index.js", "LICENSE", "THIRD_PARTY_NOTICES.md", "sources/decky-api.tar.gz")
PLUGIN_RELEASES = "https://api.github.com/repos/rennerdo30/DeckCompanion/releases?per_page=100"


def homebrew():
    return Path.home() / "homebrew"


def installed():
    return (homebrew() / "services/PluginLoader").is_file()


def latest(paths):
    cache = paths.cache / "decky-release.json"
    try:
        saved = read_json(cache, {})
        if not isinstance(saved, dict) or not isinstance(saved.get("version"), str):
            saved = {}
    except (OSError, ValueError):
        saved = {}
    checked = saved.get("checked_at", 0)
    if not isinstance(checked, (int, float)):
        checked = 0
    if saved and 0 <= time.time() - checked < CACHE_SECONDS:
        return dict(saved, source="cache", stale=False)
    try:
        with tempfile.TemporaryDirectory(prefix="dc-decky-") as temp:
            target = Path(temp) / "release.json"
            net.fetch(RELEASE_URL, target, max_bytes=1024 * 1024)
            info = json.loads(target.read_text(encoding="utf-8"))
        version = info.get("tag_name")
        if not isinstance(version, str) or not version.startswith("v") or info.get("prerelease") or info.get("draft"):
            raise ValueError("GitHub did not return a stable release")
        saved = {"version": version, "checked_at": time.time()}
        write_json(cache, saved)
        return dict(saved, source="github", stale=False)
    except (Error, OSError, ValueError, TypeError, AttributeError):
        return dict(saved, source="cache" if saved else "unavailable", stale=True,
                    **({} if saved else {"version": None}))


def status(paths):
    version = None
    try:
        version = (homebrew() / "services/.loader.version").read_text(encoding="utf-8").strip() or None
    except OSError:
        pass
    service = "unknown"
    if sys.platform.startswith("linux"):
        try:
            proc = subprocess.Popen(["systemctl", "is-active", "plugin_loader"],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            steam.STARTED_PIDS.append(proc.pid)
            try:
                output, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate(timeout=2)
                raise Error("process_timeout", "Decky service query timed out")
            value = output.decode(errors="replace").strip()
            if value in ("active", "inactive", "failed", "activating", "deactivating", "unknown"):
                service = value
        except (Error, OSError):
            pass
    return {"installed": installed(), "version": version, "service": service, "latest": latest(paths)}


def install():
    from .apply_helper import game_mode
    result = {"needs_desktop": True, "command": INSTALL_COMMAND, "launched": False,
              "instructions": ["Switch to Desktop Mode and open Konsole.",
                  "Run the official installer command. Its own sudo prompt handles authentication.",
                  "Return to Gaming Mode after it finishes. DeckCompanion cannot confirm completion."]}
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    # SteamDeck=1 may be inherited by Desktop applications; KDE identifies Desktop.
    is_game_mode = any(s in desktop for s in ("gamescope", "steam")) or ("kde" not in desktop and "plasma" not in desktop and game_mode())
    if not sys.platform.startswith("linux") or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) or is_game_mode:
        return result
    konsole, runner = shutil.which("konsole"), shutil.which("systemd-run")
    if not konsole or not runner:
        result["instructions"].append("Automatic handoff needs Konsole and a systemd user session; run the command manually.")
        return result
    unit = "deckcompanion-decky-" + str(time.time_ns())
    try:
        # A separate user unit survives the UI. The terminal has a finite lifetime.
        steam.run([runner, "--user", "--collect", "--unit=" + unit,
                   "--property=RuntimeMaxSec=1800", "--", konsole, "--separate", "-e",
                   "sh", "-c", INSTALL_COMMAND], timeout=5)
        result.update(launched=True, unit=unit)
    except (Error, OSError):
        result["instructions"].append("Could not open the terminal; run the command manually in Konsole.")
    return result


def shortcut(paths):
    """Only return an appid when the installed shortcut exists in the active account."""
    import vdf
    exe, ident = steam.shortcut_identity(paths)
    try:
        path = steam.shortcut_path(paths)
        entries = vdf.binary_loads(path.read_bytes()).get("shortcuts", {}).values()
        if any(steam.shortcut_value("appid", e.get("appid")) == ident and
               steam.shortcut_value("Exe", e.get("Exe")) == steam.shortcut_value("Exe", exe) for e in entries):
            return str(ident)
    except (Error, OSError, ValueError, TypeError, SyntaxError):
        pass
    return None


def panel(paths, ident=None):
    from .cli import game
    from .splash import settings
    from datetime import datetime
    data = state(paths)
    if ident == str(steam.shortcut_identity(paths)[1]):
        ident = None
    chosen = "current" if ident else "none"
    if not ident:
        launches = dict(data["games"])
        launches.update({g["appid"]: g for g in data.get("non_steam", {}).values() if not g.get("removed")})
        played = steam.last_played(paths)
        candidates = set(steam.installed(paths)) | set(launches) | set(played)
        candidates.discard(str(steam.shortcut_identity(paths)[1]))
        def recency(i):
            try:
                stamp = datetime.fromisoformat(launches.get(i, {}).get("last_launch", {}).get("time", "")).timestamp()
            except (ValueError, TypeError, AttributeError):
                stamp = 0
            return max(stamp, played.get(i, 0))
        running = [i for i in candidates if steam.game_running(i)]
        recent = [i for i in candidates if recency(i) > 0]
        ident = max(running or recent, key=lambda i: (recency(i), i), default=None)
        chosen = "running" if running else "last" if ident else "none"
    detail = game(paths, ident) if ident else None
    if detail:
        detail["proton_override"] = data["games"].get(ident, {}).get("proton_override")
    return {"game": detail, "selection": chosen, "settings": settings(data),
            "proton": steam.proton(paths), "shortcut_appid": shortcut(paths)}


def plugin_release(paths):
    """Plugin tags are independent of the engine's latest release/index."""
    cache = paths.cache / "decky-plugin-release.json"
    try:
        saved = read_json(cache, {})
        if not isinstance(saved, dict):
            saved = {}
        checked = saved.get("checked_at", 0)
        if saved and isinstance(checked, (int, float)) and 0 <= time.time() - checked < CACHE_SECONDS:
            return dict(saved, source="cache", stale=False)
    except (OSError, ValueError):
        saved = {}
    try:
        budget = net.Budget()
        with tempfile.TemporaryDirectory(prefix="dc-plugin-release-") as temp:
            choices = []
            for page in range(1, 11):
                target = Path(temp) / "releases.json"
                net.fetch(PLUGIN_RELEASES + f"&page={page}", target, budget, max_bytes=8 * 1024 * 1024)
                releases = json.loads(target.read_text(encoding="utf-8"))
                if not isinstance(releases, list):
                    raise ValueError("Invalid GitHub releases response")
                for release in releases:
                    match = re.fullmatch(r"decky-v(\d+\.\d+\.\d+)", release.get("tag_name", ""))
                    if match and not release.get("draft") and not release.get("prerelease"):
                        version = match[1]
                        name = f"deckcompanion-decky-{version}.zip"
                        asset = next((a for a in release.get("assets", []) if a.get("name") == name), {})
                        sha = asset.get("digest", "").removeprefix("sha256:")
                        url = f"https://github.com/rennerdo30/DeckCompanion/releases/download/decky-v{version}/{name}"
                        if re.fullmatch(r"[a-f0-9]{64}", sha) and asset.get("browser_download_url") == url and 0 < asset.get("size", 0) <= 8 * 1024 * 1024:
                            choices.append({"version": version, "url": url, "sha256": sha, "size": asset["size"]})
                if len(releases) < 100:
                    break
            else:
                raise ValueError("Release history exceeds bounded lookup")
        if not choices:
            raise ValueError("No stable decky-v release with a SHA256 asset digest")
        saved = dict(max(choices, key=lambda c: tuple(map(int, c["version"].split(".")))), checked_at=time.time())
        write_json(cache, saved)
        return dict(saved, source="github", stale=False)
    except (Error, OSError, ValueError, TypeError, AttributeError):
        return dict(saved, source="cache" if saved else "unavailable", stale=True)


def plugin_status(paths):
    directory = homebrew() / "plugins"
    target = directory / "DeckCompanion"
    try:
        version = read_json(target / "package.json", {}).get("version")
    except (OSError, ValueError, AttributeError):
        version = None
    writable = directory.is_dir() and os.access(directory, os.W_OK)
    latest_release = plugin_release(paths)
    return {"installed": (target / "plugin.json").is_file(), "version": version,
            "decky_installed": installed(), "plugins_writable": writable, "latest": latest_release,
            "can_install": installed() and writable and bool(latest_release.get("sha256")),
            "instructions": "Use Decky Settings > Developer > Install Plugin from ZIP with the versioned plugin ZIP if this directory is not writable. No sudo or ownership changes are made by DeckCompanion."}


def plugin_install(paths):
    """Install independently; verify bytes, preserve edits, retain a rollback directory."""
    result = plugin_status(paths)
    if not result["can_install"]:
        return dict(result, changed=False, reason="decky_missing" if not result["decky_installed"] else
                    "plugins_not_writable" if not result["plugins_writable"] else "release_unavailable")
    directory = homebrew() / "plugins"
    target = directory / "DeckCompanion"
    receipt_path = paths.data / "decky-plugin-installed.json"
    receipt = read_json(receipt_path, {})
    if target.is_symlink():
        return dict(result, changed=False, reason="existing_plugin_preserved")
    if target.exists():
        actual = {p.relative_to(target).as_posix(): net.digest(p) for p in target.rglob("*") if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts}
        if any(p.is_symlink() for p in target.rglob("*")) or actual != receipt.get("files"):
            return dict(result, changed=False, reason="existing_plugin_preserved")
        if receipt.get("sha256") == result["latest"]["sha256"]:
            return dict(result, changed=False)
    archive = paths.cache / "decky-plugin.zip"
    release = result["latest"]
    net.fetch(release["url"], archive, net.DownloadBudget(20), max_bytes=8 * 1024 * 1024)
    if net.digest(archive) != release["sha256"]:
        raise Error("checksum_mismatch", "Plugin archive SHA256 differs from the GitHub release digest")
    with tempfile.TemporaryDirectory(prefix=".deckcompanion-", dir=directory) as temp:
        staging = Path(temp) / "DeckCompanion"
        with zipfile.ZipFile(archive) as bundle:
            expected = {"DeckCompanion/" + name for name in PLUGIN_FILES}
            if set(bundle.namelist()) != expected or len(bundle.infolist()) != len(expected) or sum(i.file_size for i in bundle.infolist()) > 8 * 1024 * 1024:
                raise Error("invalid_plugin", "Unexpected plugin zip contents")
            for name in PLUGIN_FILES:
                atomic(staging / name, bundle.read("DeckCompanion/" + name))
        if read_json(staging / "package.json").get("version") != release["version"] or read_json(staging / "plugin.json").get("name") != "DeckCompanion":
            raise Error("invalid_plugin", "Plugin metadata does not match the release")
        files = {name: net.digest(staging / name) for name in PLUGIN_FILES}
        backup = directory / (".DeckCompanion-previous-" + str(time.time_ns())) if target.exists() else None
        if backup:
            os.rename(target, backup)
        try:
            os.rename(staging, target)
            write_json(receipt_path, {"version": release["version"], "sha256": release["sha256"], "files": files})
        except Exception:
            if target.exists():
                os.rename(target, Path(temp) / "failed")
            if backup:
                os.rename(backup, target)
            raise
    return dict(result, installed=True, version=release["version"], changed=True, path=str(target),
                backup=str(backup) if backup else None, instructions="Reload plugins in Decky Settings.")
