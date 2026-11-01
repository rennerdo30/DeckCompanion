"""Decky discovery and explicit installs; passwords exist only in memory/stdin."""
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
from .common import Error, atomic, locked, read_json, state, write_json

RELEASE_URL = "https://api.github.com/repos/SteamDeckHomebrew/decky-loader/releases/latest"
INSTALLER_URL = "https://github.com/SteamDeckHomebrew/decky-installer/releases/latest/download/install_release.sh"
INSTALL_COMMAND = f"curl -L {INSTALLER_URL} | sh"
CACHE_SECONDS = 6 * 3600
PLUGIN_FILES = ("main.py", "plugin.json", "package.json", "dist/index.js", "LICENSE", "THIRD_PARTY_NOTICES.md", "sources/decky-api.tar.gz")
PLUGIN_RELEASES = "https://api.github.com/repos/rennerdo30/DeckCompanion/releases?per_page=100"
STORE_URL = "https://plugins.deckbrew.xyz/plugins"
STORE_CDN = "https://cdn.tzatzikiweeb.moe/file/steam-deck-homebrew/versions/"
BUNDLED_PLUGIN = Path(__file__).resolve().parents[2] / "bundled/decky"


def cached_json(path):
    try:
        value = read_json(path, {})
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def newer(installed_version, version):
    from .recipes import version_key
    return bool(isinstance(installed_version, str) and isinstance(version, str) and
                version_key(version.lstrip("v")) > version_key(installed_version.lstrip("v")))


def store_plugins(paths, refresh=True):
    cache = paths.cache / "decky-store.json"
    saved = cached_json(cache)
    cached = saved.get("plugins", {})
    cached = {name: item for name, item in cached.items() if isinstance(name, str) and isinstance(item, dict) and
              isinstance(item.get("version"), str) and isinstance(item.get("sha256"), str) and
              re.fullmatch(r"[a-f0-9]{64}", item["sha256"]) and item.get("url") == STORE_CDN + item["sha256"] + ".zip"} if isinstance(cached, dict) else {}
    checked = saved.get("checked_at", 0)
    if not refresh or (saved and isinstance(checked, (int, float)) and 0 <= time.time() - checked < CACHE_SECONDS):
        return cached
    try:
        with tempfile.TemporaryDirectory(prefix="dc-store-") as temp:
            target = Path(temp) / "plugins.json"
            net.fetch(STORE_URL, target, net.Budget(2), max_bytes=8 * 1024**2)
            rows = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError("Expected a plugin store list")
        result = {}
        for row in rows:
            name, versions = row["name"], row["versions"]
            if not isinstance(name, str) or not isinstance(versions, list) or name in result:
                raise ValueError("Invalid store entry")
            if not versions:
                continue
            version, sha = versions[0]["name"], versions[0]["hash"]
            if not isinstance(version, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
                raise ValueError("Invalid store version/hash")
            result[name] = {"version": version, "sha256": sha, "url": STORE_CDN + sha + ".zip"}
        write_json(cache, {"plugins": result, "checked_at": time.time()})
        return result
    except (Error, OSError, ValueError, TypeError, KeyError, AttributeError):
        return cached


def installed_plugins():
    result = []
    try:
        directories = sorted((homebrew() / "plugins").iterdir())
    except OSError:
        return result
    for directory in directories:
        if directory.name.startswith(".") or directory.is_symlink() or not directory.is_dir():
            continue
        metadata = cached_json(directory / "plugin.json")
        name = metadata.get("name")
        if not isinstance(name, str) or not name:
            continue
        version = cached_json(directory / "package.json").get("version")
        result.append({"dir": directory.name, "name": name,
                       "installed": version if isinstance(version, str) else None})
    return result


def plugins_status(paths, refresh=True):
    rows = installed_plugins()
    store = store_plugins(paths, refresh) if any(r["name"] != "DeckCompanion" for r in rows) else {}
    own = plugin_candidate(paths, refresh) if any(r["name"] == "DeckCompanion" for r in rows) else {}
    result = []
    for row in rows:
        release = own if row["name"] == "DeckCompanion" else store.get(row["name"], {})
        version = release.get("version")
        version = version if isinstance(version, str) else None
        result.append(dict(row, latest=version, update_available=newer(row["installed"], version)))
    return result


def available(paths):
    """Notification reads never contact upstream or query services/sudo."""
    result = []
    version = None
    try:
        version = (homebrew() / "services/.loader.version").read_text(encoding="utf-8").strip() or None
    except OSError:
        pass
    release = cached_json(paths.cache / "decky-release.json").get("version")
    if isinstance(release, str) and release and (not installed() or newer(version, release)):
        result.append({"module": "decky-loader", "name": "Decky Loader", "installed": version, "latest": release})
    rows = plugins_status(paths, refresh=False)
    for row in rows:
        if row["update_available"]:
            result.append({"module": "deckcompanion-plugin" if row["name"] == "DeckCompanion" else "decky-plugins",
                           "name": row["name"], "installed": row["installed"], "latest": row["latest"]})
    own = plugin_candidate(paths, refresh=False).get("version")
    if isinstance(own, str) and installed() and not any(row["name"] == "DeckCompanion" for row in rows):
        result.append({"module": "deckcompanion-plugin", "name": "DeckCompanion", "installed": None, "latest": own})
    return result


def update_plugins(paths, password):
    """Update plugins and optionally install our missing panel; failures stay isolated."""
    from . import updates
    result = {"plugins_updated": [], "plugins_failed": []}
    with locked(paths, "decky-plugins.lock"):
        rows = plugins_status(paths)
        if installed() and auto_plugin(paths) and not any(r["name"] == "DeckCompanion" for r in rows):
            release = plugin_candidate(paths)
            if release.get("sha256"):
                rows.append({"dir": "DeckCompanion", "name": "DeckCompanion", "installed": None,
                             "latest": release["version"], "update_available": True, "release": release})
            else:
                result["plugins_failed"].append({"name": "DeckCompanion", "error": "release_unavailable"})
        for row in rows:
            if not row["update_available"]:
                continue
            try:
                own = row["name"] == "DeckCompanion"
                release = (row.get("release") or plugin_candidate(paths, refresh=False) if own else
                           store_plugins(paths, refresh=False)[row["name"]])
                with tempfile.TemporaryDirectory(prefix="dc-plugin-") as temp:
                    archive = Path(temp) / "plugin.zip"
                    plugin_archive(release, archive)
                    target = homebrew() / "plugins" / row["dir"]
                    root_plugin(archive, target, row["name"], row["latest"], release["sha256"], password)
                result["plugins_updated"].append(row["name"])
                if own:
                    updates.record(paths, "decky-plugin", row["name"], row["installed"], row["latest"])
            except Exception as exc:
                # Exception text can include subprocess inputs. Expose only fixed errors.
                code = exc.code if isinstance(exc, Error) else "invalid_plugin"
                result["plugins_failed"].append({"name": row["name"], "error": code})
    return result


def auto_plugin(paths):
    from .splash import settings
    return settings(state(paths))["decky_plugin_auto"]


def plugin_archive(release, archive):
    """Download/copy and verify before either Decky or root sees the artifact."""
    from . import decky_archive
    if release.get("path"):
        source = Path(release["path"])
        if source.stat().st_size > decky_archive.MAX_ARCHIVE:
            raise Error("invalid_plugin", "Plugin archive exceeds size limit")
        atomic(archive, source.read_bytes())
    else:
        net.fetch(release["url"], archive, net.DownloadBudget(60), max_bytes=decky_archive.MAX_ARCHIVE)
    if net.digest(archive) != release["sha256"]:
        raise Error("checksum_mismatch", "Plugin SHA256 mismatch")


def root_plugin(archive, target, name, version, sha, password):
    from . import decky_archive
    with tempfile.TemporaryDirectory(prefix="dc-plugin-check-") as temp:
        decky_archive.extract(archive, Path(temp), name, version)
    code = Path(decky_archive.__file__).read_text(encoding="utf-8")
    proc = subprocess.Popen([shutil.which("sudo"), "-S", "-k", "-p", "", "timeout", "-k", "5", "110", sys.executable, "-I", "-c", code,
                             str(archive), str(target), name, version, sha],
                            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    steam.STARTED_PIDS.append(proc.pid)
    try:
        proc.communicate(password + "\n", timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=5)
        raise Error("plugin_timeout", "Plugin replacement timed out") from None
    if proc.returncode:
        raise Error("plugin_install_failed", "Root plugin replacement failed")


def restart_loader(password):
    proc = subprocess.Popen([shutil.which("sudo"), "-S", "-k", "-p", "", "timeout", "-k", "5", "25",
                             "systemctl", "restart", "plugin_loader"], stdin=subprocess.PIPE,
                            text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    steam.STARTED_PIDS.append(proc.pid)
    try:
        proc.communicate(password + "\n", timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=5)
        raise Error("decky_restart_failed", "Plugin installed but Decky Loader restart timed out") from None
    if proc.returncode:
        raise Error("decky_restart_failed", "Plugin installed but Decky Loader restart failed")


def _check_password(password_file):
    password = _read_secret(password_file)
    if not password:
        raise Error("password_required", "Enter your Deck password")
    if not sys.platform.startswith("linux") or not shutil.which("sudo"):
        raise Error("unsupported", "Decky installs only on SteamOS / Linux")
    check = subprocess.run([shutil.which("sudo"), "-S", "-k", "-p", "", "true"], input=password + "\n", text=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    if check.returncode:
        raise Error("wrong_password", "That password was not accepted")
    return password


def update_plugins_with_password(paths, password_file):
    password = _check_password(password_file)
    try:
        result = update_plugins(paths, password)
        changed = bool(result["plugins_updated"])
        if changed:
            restart_loader(password)
    finally:
        password = None
    return dict(result, steam_restarting=False, loader_restarted=changed)


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
    return {"installed": installed(), "version": version, "service": service, "latest": latest(paths),
            "password_set": password_set(), "plugins": plugins_status(paths)}


def password_set():
    """True/False when `passwd -S` reports the user's password state, None if unknown.

    Decky's installer needs sudo; SteamOS ships the deck user without a password."""
    if not sys.platform.startswith("linux") or not shutil.which("passwd"):
        return None
    try:
        output = steam.run(["passwd", "-S", os.environ.get("USER", "deck")], timeout=5).decode(errors="replace").split()
    except (Error, OSError):
        return None
    return output[1] == "P" if len(output) > 1 else None


def _read_secret(password_file):
    source = Path(password_file)
    try:
        return source.read_text(encoding="utf-8").rstrip("\n")
    except OSError as exc:
        raise Error("password_required", "Enter a password") from exc
    finally:
        source.unlink(missing_ok=True)


def drive_passwd(read, write, password, deadline):
    """Answer passwd's prompts on a pseudo-terminal. An account without a password
    gets an empty answer for "Current password"; new/retype prompts get the password."""
    buffer = ""
    answered = 0
    while time.monotonic() < deadline:
        chunk = read()
        if chunk is None:
            return answered
        buffer += chunk
        prompt = buffer.lower().rstrip()
        if prompt.endswith(":"):
            if "current" in prompt or "(current)" in prompt:
                write("\n")
            elif "new" in prompt or "retype" in prompt or "repeat" in prompt:
                write(password + "\n")
                answered += 1
            buffer = ""
    raise Error("password_timeout", "Setting the password timed out")


def set_password(password_file):
    """Create the first password for this account from inside DeckCompanion.

    SteamOS ships the deck user without a password, but Decky's installer needs sudo.
    Only for accounts without a password (changing one would need the old password).
    passwd runs on a pseudo-terminal; the password is never logged or stored."""
    password = _read_secret(password_file)
    if len(password) < 4:
        raise Error("password_too_short", "Use at least 4 characters")
    if password_set() is not False:
        raise Error("password_exists", "This Deck already has a password; enter it instead")
    import pty
    import select
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp("passwd", ["passwd"])
    def read():
        ready, _, _ = select.select([fd], [], [], 0.5)
        if not ready:
            return ""
        try:
            data = os.read(fd, 1024)
        except OSError:
            return None
        return data.decode(errors="replace") if data else None
    try:
        answered = drive_passwd(read, lambda text: os.write(fd, text.encode()), password, time.monotonic() + 20)
    finally:
        password = None
        _, status = os.waitpid(pid, 0)
        os.close(fd)
    if os.waitstatus_to_exitcode(status) != 0 or answered < 2:
        raise Error("password_not_set", "SteamOS did not accept that password; try a longer one")
    return {"password_set": True}


def install_with_password(paths, password_file):
    """Install or update Decky Loader inside DeckCompanion, without Desktop Mode.

    The official installer must run as root (it uses SUDO_USER). The UI writes the
    password to a private file in XDG_RUNTIME_DIR (tmpfs, mode 0700 directory); it is
    read once, deleted immediately and only ever passed to sudo on stdin. It is never
    logged or stored (owner request, 2026-11-01)."""
    password = _check_password(password_file)
    sudo = shutil.which("sudo")
    script = paths.data / "run" / "decky-install_release.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    net.fetch(INSTALLER_URL, script, net.Budget(30), max_bytes=1024 * 1024)
    log = paths.data / "logs" / "decky-install.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as output:
        proc = subprocess.run([sudo, "-S", "-k", "-p", "", "sh", str(script)], input=password + "\n", text=True,
                              stdout=output, stderr=subprocess.STDOUT, timeout=600)
    if proc.returncode:
        raise Error("decky_install_failed", f"The Decky installer stopped with code {proc.returncode}; see {log}")
    try:
        plugins = update_plugins(paths, password)
    except Exception:
        plugins = {"plugins_updated": [], "plugins_failed": [{"name": "Decky plugins", "error": "plugin_update_failed"}]}
    finally:
        password = None
    restart_steam()
    version_file = homebrew() / "services/.loader.version"
    version = version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else None
    return dict(installed=installed(), version=version, steam_restarting=True, log=str(log), **plugins)


def restart_steam():
    """Decky loads after a Steam restart. Game Mode respawns Steam by itself; the
    fallback starts it again on the desktop. Detached so it survives this process."""
    runner = shutil.which("systemd-run")
    script = "steam -shutdown; sleep 12; pgrep -x steam >/dev/null || (steam -silent >/dev/null 2>&1 &)"
    command = ["sh", "-c", script]
    if runner:
        command = [runner, "--user", "--collect", "--unit=deckcompanion-steam-restart-" + str(time.time_ns()),
                   "--property=RuntimeMaxSec=180", "--"] + command
    try:
        steam.run(command, timeout=10)
    except (Error, OSError):
        pass


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


def install_via_desktop(paths):
    """Game Mode flow: switch to Desktop Mode and open the official installer there once.

    Decky's installer needs root, so it must prompt for the password itself in a
    terminal. A one-time autostart entry opens Konsole with the official command as
    soon as the Plasma session starts and deletes itself first (owner UX report,
    2026-11-01: no raw commands in the Game Mode UI).
    """
    if not sys.platform.startswith("linux"):
        raise Error("unsupported", "Decky installs only on SteamOS / Linux")
    selector = shutil.which("steamos-session-select") or shutil.which("holo-session-select")
    if not selector or not shutil.which("konsole"):
        raise Error("desktop_unavailable", "Desktop Mode or Konsole is not available on this system")
    home = Path.home()
    autostart = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "autostart" / "deckcompanion-decky-install.desktop"
    script = paths.data / "run" / "decky-install.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    autostart.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "#!/bin/sh\n"
        f"rm -f '{autostart}'\n"
        "exec konsole --separate -e sh -c '"
        "echo \"DeckCompanion: installing Decky Loader with the official installer.\"; "
        "echo \"Enter your password when asked.\"; echo; "
        f"{INSTALL_COMMAND}; "
        "echo; echo \"Done. Use Return to Gaming Mode on the desktop.\"; sleep 600'\n",
        encoding="utf-8")
    script.chmod(0o755)
    autostart.write_text(
        "[Desktop Entry]\nType=Application\nName=DeckCompanion: install Decky\n"
        f"Exec=sh {script}\nX-KDE-autostart-phase=2\nNoDisplay=true\n", encoding="utf-8")
    runner = shutil.which("systemd-run")
    command = [selector, "plasma"]
    # Detached so the switch survives this process (Steam and the UI exit with Game Mode).
    if runner:
        command = [runner, "--user", "--collect", "--unit=deckcompanion-desktop-" + str(time.time_ns()),
                   "--property=RuntimeMaxSec=120", "--"] + command
    steam.run(command, timeout=10)
    return {"switching": True, "autostart": str(autostart)}


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


def bundled_plugin():
    metadata = cached_json(BUNDLED_PLUGIN / "plugin.json")
    version, sha = metadata.get("version"), metadata.get("sha256")
    if (not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version) or
            not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha)):
        return {}
    name = f"deckcompanion-decky-{version}.zip"
    archive = BUNDLED_PLUGIN / name
    if metadata.get("file") != name or not archive.is_file():
        return {}
    return dict(metadata, path=str(archive), source="bundled", stale=False)


def plugin_candidate(paths, refresh=True):
    bundled = bundled_plugin()
    channel = plugin_release(paths) if refresh else cached_json(paths.cache / "decky-plugin-release.json")
    version, sha, url = channel.get("version"), channel.get("sha256"), channel.get("url")
    valid = (isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+", version) and
             isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha) and
             isinstance(url, str) and url.startswith("https://"))
    # Channel metadata never selects a local path, including in a stale cache.
    channel = {k: channel[k] for k in ("version", "sha256", "url", "size", "checked_at", "stale") if k in channel} if valid else {}
    if valid and (not bundled or newer(bundled["version"], version)):
        return dict(channel, source="channel")
    return bundled or dict(channel, source="channel", stale=True)


def plugin_status(paths):
    directory = homebrew() / "plugins"
    target = directory / "DeckCompanion"
    try:
        version = read_json(target / "package.json", {}).get("version")
    except (OSError, ValueError, AttributeError):
        version = None
    writable = directory.is_dir() and os.access(directory, os.W_OK)
    latest_release = plugin_candidate(paths)
    return {"installed": (target / "plugin.json").is_file(), "version": version,
            "decky_installed": installed(), "plugins_writable": writable, "latest": latest_release,
            "available_version": latest_release.get("version"), "source": latest_release["source"],
            "methods": ["decky_api", "password"] if installed() else [],
            "can_install": installed() and bool(latest_release.get("sha256")),
            "instructions": "Install in Game Mode and confirm in Steam. If Decky's API is unavailable, use --password-file."}


def plugin_install(paths):
    """Ask Decky to install; the owner confirms using its normal Game Mode modal."""
    from . import decky_api, decky_archive
    result = plugin_status(paths)
    if not result["can_install"]:
        return dict(result, changed=False, requested=False,
                    reason="decky_missing" if not result["decky_installed"] else "release_unavailable")
    release = result["latest"]
    if result["installed"] and result["version"] and not newer(result["version"], release["version"]):
        return dict(result, changed=False, requested=False)
    # Keep the hash-addressed artifact after returning: confirmation may come later.
    archive = paths.cache / "decky-artifacts" / (release["sha256"] + ".zip")
    with locked(paths, "decky-plugins.lock"):
        plugin_archive(release, archive)
        with tempfile.TemporaryDirectory(prefix="dc-plugin-check-") as temp:
            try:
                decky_archive.extract(archive, Path(temp), "DeckCompanion", release["version"])
            except (ValueError, KeyError, OSError, zipfile.BadZipFile):
                raise Error("invalid_plugin", "Unexpected plugin archive contents") from None
        try:
            decky_api.install(release.get("url") or archive.resolve().as_uri(), release["version"],
                              release["sha256"], 2 if result["installed"] else 0)
        except Error as exc:
            if exc.code != "decky_api_unavailable":
                raise
            # Retain the existing conservative non-root path for older/offline Loaders.
            if result["plugins_writable"]:
                return _plugin_install_writable(paths)
            return dict(result, method="decky_api", requested=False, confirm_in_steam=False,
                        changed=False, reason="decky_api_unavailable")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            version = cached_json(homebrew() / "plugins/DeckCompanion/package.json").get("version")
            if version == release["version"] and (homebrew() / "plugins/DeckCompanion/plugin.json").is_file():
                return dict(result, method="decky_api", requested=True, confirm_in_steam=True,
                            installed=True, version=version, changed=True)
            time.sleep(0.25)
    return dict(result, method="decky_api", requested=True, confirm_in_steam=True,
                changed=False, reason="confirmation_pending")


def plugin_install_with_password(paths, password_file):
    password = _check_password(password_file)
    try:
        result = plugin_status(paths)
        if not result["can_install"]:
            return dict(result, changed=False, reason="decky_missing" if not result["decky_installed"] else "release_unavailable")
        release = result["latest"]
        if result["installed"] and result["version"] and not newer(result["version"], release["version"]):
            return dict(result, method="password", changed=False, loader_restarted=False)
        with locked(paths, "decky-plugins.lock"), tempfile.TemporaryDirectory(prefix="dc-plugin-") as temp:
            archive = Path(temp) / "plugin.zip"
            plugin_archive(release, archive)
            root_plugin(archive, homebrew() / "plugins/DeckCompanion", "DeckCompanion", release["version"], release["sha256"], password)
        restart_loader(password)
    finally:
        password = None
    from . import updates
    updates.record(paths, "decky-plugin", "DeckCompanion", result["version"], release["version"])
    return dict(result, method="password", installed=True, version=release["version"], changed=True, loader_restarted=True)


def _plugin_install_writable(paths):
    """Install independently; verify bytes, preserve edits, retain a rollback directory."""
    result = plugin_status(paths)
    if not result["can_install"] or not result["plugins_writable"]:
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
    plugin_archive(release, archive)
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
    from . import updates
    updates.record(paths, "decky-plugin", "DeckCompanion", result["version"], release["version"])
    return dict(result, installed=True, version=release["version"], changed=True, path=str(target),
                backup=str(backup) if backup else None, instructions="Reload plugins in Decky Settings.")
