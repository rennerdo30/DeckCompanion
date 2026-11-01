"""Steam disk integration. Every write is guarded; unrelated VDF data survives."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import zlib
import vdf
from .common import Error, atomic, now, state, save_state, app_id

STARTED_PIDS = []


def run(command, timeout=10):
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    STARTED_PIDS.append(proc.pid)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()  # Only this recorded subprocess, never an enumerated Steam PID.
        proc.communicate(timeout=2)
        raise Error("process_timeout", f"Command timed out: {command[0]}")
    if proc.returncode:
        raise Error("process_failed", f"Command failed: {command[0]}")
    return out


def running():
    if os.name == "nt":
        output = run(["tasklist", "/FO", "CSV", "/NH"], 5).decode(errors="replace").lower()
        return any(f'"{name}"' in output for name in ("steam.exe", "steamwebhelper.exe"))
    proc = Path("/proc")
    if not proc.is_dir():
        raise Error("steam_state_unknown", "Cannot determine whether Steam is closed")
    for path in proc.glob("[0-9]*/comm"):
        try:
            if path.read_text().strip() in ("steam", "steamwebhelper"):
                return True
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            raise Error("steam_state_unknown", "Cannot inspect process state") from exc
    return False


def require_closed():
    if running():
        raise Error("steam_running", "Close Steam before writing its files")


def start():
    proc = subprocess.Popen(["steam", "-silent"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    STARTED_PIDS.append(proc.pid)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if running():
            return  # Steam is intentionally long-lived after its bounded startup.
        if proc.poll() not in (None, 0):
            raise Error("steam_start_failed", "Steam exited during startup")
        time.sleep(0.1)
    if proc.poll() is None:
        proc.kill()  # Our recorded launcher only; never a detected Steam PID.
        proc.wait(timeout=2)
    raise Error("process_timeout", "Steam did not start within 10 seconds")


def read_vdf(path):
    return vdf.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def libraries(paths):
    roots = [paths.steam]
    entries = read_vdf(paths.steam / "steamapps/libraryfolders.vdf").get("libraryfolders", {})
    for key, value in entries.items():
        raw = value.get("path") if isinstance(value, dict) else value if key.isdigit() else None
        if raw and Path(raw).resolve() not in roots:
            roots.append(Path(raw).resolve())
    return roots


def installed(paths):
    games = {}
    for library in libraries(paths):
        for file in (library / "steamapps").glob("appmanifest_*.acf"):
            entry = read_vdf(file).get("AppState", {})
            ident = entry.get("appid", "")
            if not ident.isdigit():
                continue
            folder = entry.get("installdir", "")
            if not folder or Path(folder).name != folder or folder in (".", "..") or ":" in folder or "\\" in folder:
                continue
            games[ident] = {"appid": ident, "name": entry.get("name", ident),
                            "path": str(library / "steamapps/common" / folder), "library": str(library)}
    return games


def proton(paths):
    candidates = list(paths.tools.glob("*/proton"))
    candidates += [p for lib in libraries(paths) for p in (lib / "steamapps/common").glob("*/proton")]
    def key(item):
        return (item["name"].startswith("GE-Proton"), tuple(int(n) for n in re.findall(r"\d+", item["name"])), item["name"])
    return sorted([{"name": p.parent.name, "path": str(p.parent)} for p in candidates
                   if p.is_file() and p.parent.name != "DeckCompanion"], key=key, reverse=True)


def mapping_path(paths):
    return paths.steam / "config/config.vdf"


def mappings(doc):
    node = doc
    for key in ("InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping"):
        node = node.setdefault(key, {})
    return node


def shortcut_path(paths):
    user = os.environ.get("DECKCOMPANION_STEAM_USER_ID")
    # "0" is Steam's anonymous placeholder, never a real account.
    users = [u for u in (paths.steam / "userdata").glob("[0-9]*") if u.name != "0"]
    recent = [u for u in users if (u / "config/localconfig.vdf").is_file()]
    if user:
        user = app_id(user)
    elif len(users) == 1:
        user = users[0].name
    elif recent:
        # Steam rewrites localconfig.vdf for the signed-in account; loginusers.vdf
        # had no MostRecent flag on SteamOS 3.9 (hardware check 2026-11-01).
        user = max(recent, key=lambda u: (u / "config/localconfig.vdf").stat().st_mtime).name
    else:
        raise Error("steam_user_required", "Set DECKCOMPANION_STEAM_USER_ID to choose a Steam account")
    return paths.steam / "userdata" / user / "config/shortcuts.vdf"


def shortcut_bytes(paths, path, remove=False):
    doc = vdf.binary_loads(path.read_bytes()) if path.exists() else {"shortcuts": {}}
    entries = list(doc.setdefault("shortcuts", {}).values())
    exe = '"' + str(paths.data / "bin/deckcompanion-ui") + '"'
    unsigned = zlib.crc32((exe + "DeckCompanion").encode()) | 0x80000000
    ident = unsigned - 0x100000000  # Steam interprets the signed VDF int32 bits as uint32.
    entries = [e for e in entries if not (e.get("appid") == ident and e.get("Exe") == exe)]
    if not remove:
        from . import update
        version = update.current(paths)
        icon = paths.data / "versions" / version / "assets/logo/deckcompanion-icon-256.png" if version else None
        entries.append({"appid": ident, "AppName": "DeckCompanion", "Exe": exe,
                        "StartDir": '"' + str(paths.data / "bin") + '"',
                        "icon": str(icon) if icon and icon.is_file() else "", "LaunchOptions": "",
                        "IsHidden": 0, "AllowDesktopConfig": 1, "AllowOverlay": 1, "tags": {}})
    doc["shortcuts"] = {str(i): item for i, item in enumerate(entries)}
    return vdf.binary_dumps(doc)


def guarded_write(path, payload):
    require_closed()
    if path.exists():
        backup = path.with_name(path.name + ".deckcompanion-" + now().replace(":", "-") + ".bak")
        shutil.copy2(path, backup)
    atomic(path, payload)


def apply(paths, restart=False):
    if running():
        if not restart:
            raise Error("steam_running", "Use apply --restart-steam or close Steam first")
        if sys.platform.startswith("linux"):
            from . import apply_helper
            return apply_helper.schedule(paths)
        run(["steam", "-shutdown"], 10)
        deadline = time.monotonic() + 10
        while running() and time.monotonic() < deadline:
            time.sleep(0.1)
        require_closed()
    data = state(paths)
    doc = read_vdf(mapping_path(paths))
    target = mappings(doc)
    changes, cleanup = [], []
    for ident, game in data["games"].items():
        pending = game.get("pending")
        if not pending:
            continue
        if pending == "enable":
            if not game.get("enabled"):
                game["previous_mapping"] = target.get(ident)
            target[ident] = {"name": "DeckCompanion", "config": "", "priority": "250"}
        else:
            if target.get(ident, {}).get("name") == "DeckCompanion":
                previous = game.get("previous_mapping")
                if previous is None:
                    target.pop(ident, None)
                else:
                    target[ident] = previous
            cleanup.append(paths.config / "localfixes" / f"{ident}.py")
        game.update(enabled=pending == "enable", pending=None)
        changes.append({"appid": ident, "action": pending})
    writes = []
    if changes:
        writes.append((mapping_path(paths), vdf.dumps(doc, pretty=True).encode()))
    if data.get("shortcut_pending"):
        path = shortcut_path(paths)
        writes.append((path, shortcut_bytes(paths, path)))
        data["shortcut_pending"] = False
        changes.append({"action": "shortcut"})
    originals = {p: p.read_bytes() if p.exists() else None for p, _ in writes}
    originals.update({p: p.read_bytes() for p in cleanup if p.exists()})
    written = []
    try:
        for path, payload in writes:
            guarded_write(path, payload)
            written.append(path)
        for path in cleanup:
            if path.exists() and path.read_text(encoding="utf-8").startswith("# managed-by: DeckCompanion\n"):
                require_closed()
                path.unlink()
                written.append(path)
        save_state(paths, data)
    except Exception:
        for path in reversed(written):
            require_closed()
            if originals[path] is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, originals[path])
        raise
    if restart:
        start()
    return {"applied": changes, "steam_restarted": bool(restart)}
