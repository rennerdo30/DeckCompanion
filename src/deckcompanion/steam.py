"""Steam disk integration. Every write is guarded; unrelated VDF data survives."""
import os
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
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


def game_running(ident, proc_root=Path("/proc")):
    """Conservative Linux check: app environment or Steam reaper's exact appid argument."""
    if not proc_root.is_dir():
        raise Error("game_state_unknown", "Cannot check whether the game is running")
    try:
        for process in proc_root.glob("[0-9]*"):
            try:
                if hasattr(os, "getuid") and process.stat().st_uid != os.getuid():
                    continue  # Steam and its games run as the invoking desktop user.
                env = (process / "environ").read_bytes().split(b"\0")
                if f"SteamAppId={ident}".encode() in env:
                    return True
                args = (process / "cmdline").read_bytes().split(b"\0")
                if args and Path(os.fsdecode(args[0])).name == "reaper":
                    for i, arg in enumerate(args[:-1]):
                        if arg == b"SteamLaunch" and args[i + 1] == f"AppId={ident}".encode():
                            return True
            except (FileNotFoundError, ProcessLookupError):
                continue  # Process exited during the snapshot.
            except PermissionError:
                # Same-user but undumpable processes (gamescope, sandbox helpers) hide their
                # environment; games launched by Steam stay readable (hardware check 2026-11-01).
                continue
    except OSError as exc:
        raise Error("game_state_unknown", "Cannot check all game processes safely") from exc
    return False


def content_roots(paths, ident):
    entry = installed(paths).get(ident)
    roots = [Path(entry["library"])] if entry else libraries(paths)
    prefix = next((lib / "steamapps/compatdata" / ident for lib in roots
                   if (lib / "steamapps/compatdata" / ident / "pfx").is_dir()), None)
    return prefix, Path(entry["path"]) if entry else None


def artwork(paths, ident):
    cache = paths.steam / "appcache/librarycache"
    try:
        grid = shortcut_path(paths).parent / "grid"
    except Error:
        grid = None
    result = {}
    # SteamOS 3.9 clients name the portrait "library_capsule" and the header "library_header".
    for name, filenames, suffix in (("portrait", ("library_600x900", "library_capsule"), "p"),
                                    ("hero", ("library_hero",), "_hero"),
                                    ("header", ("header", "library_header"), ""), ("logo", ("logo",), "_logo")):
        options = []
        for filename in filenames:
            options += [p for ext in ("jpg", "png") for p in sorted((cache / ident).glob(f"*/{filename}.{ext}"))]
            options += [cache / ident / f"{filename}.{ext}" for ext in ("jpg", "png")]
            options += [cache / f"{ident}_{filename}.{ext}" for ext in ("jpg", "png")]
        if grid:
            options.append(grid / f"{ident}{suffix}.png")
        result[name] = next((str(p.resolve()) for p in options if p.is_file()), None)
    return result


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


def proton(paths, log=lambda message: None):
    # Broken library metadata must not hide the primary library or custom tools.
    try:
        roots = libraries(paths)
    except Exception as exc:
        log(f"Library discovery skipped: {exc}")
        roots = [paths.steam]
    found = {}
    for root in [paths.tools, *(lib / "steamapps/common" for lib in roots)]:
        try:
            for script in root.glob("*/proton"):
                folder = script.parent.resolve()
                name = folder.name
                if not script.is_file() or not re.match(r"^(GE-Proton|UMU-Proton|Proton(?:[ -]|\d))", name, re.I):
                    continue
                if "runtime" in name.lower() or name.lower() == "deckcompanion":
                    continue
                found[str(folder)] = {"name": name, "path": str(folder)}
        except Exception as exc:
            log(f"Proton discovery skipped {root}: {exc}")
    def version(item):
        # "GE-Proton11-7-x86_64": the architecture suffix is not part of the version.
        name = re.sub(r"[-_ ]?(x86_64|amd64|x64)$", "", item["name"], flags=re.I)
        return tuple(int(n) for n in re.findall(r"\d+", name))
    valve_major = max((version(p)[0] for p in found.values()
                       if p["name"].startswith("Proton ") and version(p)), default=0)
    def key(item):
        name = item["name"]
        # Experimental is newer than the stable Valve release of its major, but a GE
        # build of the same major (e.g. GE-Proton11-7) still wins: it carries protonfixes.
        numbers = (valve_major, 0.5) if name == "Proton - Experimental" else version(item)
        # Zero padding makes 10, 10.0 and 10.0.0 ties; GE wins equal versions.
        return (numbers + (0,) * max(0, 8 - len(numbers)),
                name.startswith(("GE-Proton", "Proton-GE")), name)
    return sorted(found.values(), key=key, reverse=True)


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


def shortcut_identity(paths):
    exe = '"' + str(paths.data / "bin/deckcompanion-ui") + '"'
    unsigned = zlib.crc32((exe + "DeckCompanion").encode()) | 0x80000000
    return exe, unsigned


def shortcut_value(key, value):
    """Compare owned fields without claiming Steam's serialization or user settings."""
    if key == "appid":
        try:
            return int(value) & 0xffffffff
        except (TypeError, ValueError):
            return None
    if key in ("Exe", "StartDir", "icon") and isinstance(value, str):
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        return os.path.normpath(value) if value else ""
    return value


def shortcut_bytes(paths, path, remove=False):
    doc = vdf.binary_loads(path.read_bytes()) if path.exists() else {"shortcuts": {}}
    entries = list(doc.setdefault("shortcuts", {}).values())
    exe, unsigned = shortcut_identity(paths)
    ident = unsigned - 0x100000000  # Steam interprets the signed VDF int32 bits as uint32.
    owned = lambda e: (shortcut_value("appid", e.get("appid")) == unsigned or
                       (e.get("AppName") == "DeckCompanion" and
                        shortcut_value("Exe", e.get("Exe")) == shortcut_value("Exe", exe)))
    previous = next((e for e in entries if owned(e)), None)
    if not remove:
        icon = paths.data / "assets/icon.png"
        desired = {"appid": ident, "AppName": "DeckCompanion", "Exe": exe,
                        "StartDir": '"' + str(paths.data / "bin") + '"',
                        "icon": str(icon) if icon.is_file() else "", "LaunchOptions": ""}
        if previous and all(shortcut_value(k, previous.get(k)) == shortcut_value(k, value)
                            for k, value in desired.items()):
            return path.read_bytes()  # Steam-added fields, order and player tags stay intact.
        desired = dict(previous if previous is not None else {
            "tags": {}, "IsHidden": 0, "AllowDesktopConfig": 1, "AllowOverlay": 1}, **desired)
        entries = [desired if owned(e) else e for e in entries] if previous else [*entries, desired]
    else:
        entries = [e for e in entries if not owned(e)]
    doc["shortcuts"] = {str(i): item for i, item in enumerate(entries)}
    return vdf.binary_dumps(doc)


ARTWORK = {"portrait": "p", "wide": "", "hero": "_hero", "logo": "_logo", "icon": "_icon"}


def differs(path, payload):
    return not path.is_file() or path.read_bytes() != payload


def has_pending(data):
    return bool(data.get("default_pending") or data.get("shortcut_pending") or
                any(g.get("pending") for g in data["games"].values()))


def mapping_matches(entry):
    if not isinstance(entry, dict):
        return False
    try:
        return (entry.get("name") == "DeckCompanion" and entry.get("config") == "" and
                int(entry.get("priority")) == 250)
    except (TypeError, ValueError):
        return False


def prune_pending(paths, data):
    """Drop writes whose managed values already match Steam's current disk state."""
    target = mappings(read_vdf(mapping_path(paths)))
    if data.get("default_pending") == "default_on" and mapping_matches(target.get("0")):
        data["default_pending"] = None
    for ident, game in data["games"].items():
        if game.get("pending") == "enable" and mapping_matches(target.get(ident)):
            if not game.get("enabled"):
                # Unknown historical mapping must not be invented during an engine update.
                game.setdefault("previous_mapping", None)
            game.update(enabled=True, pending=None)
        elif game.get("pending") == "disable" and target.get(ident, {}).get("name") != "DeckCompanion":
            from . import localfix
            localfix.remove(paths, ident)
            game.update(enabled=False, pending=None)
    if data.get("shortcut_pending"):
        try:
            path = shortcut_path(paths)
            from . import net
            data["shortcut_pending"] = (differs(path, shortcut_bytes(paths, path)) or
                any(not p.is_file() or net.digest(p) != hashlib.sha256(payload).hexdigest()
                    for p, payload in shortcut_artwork(paths, path)))
        except Error as exc:
            if exc.code != "steam_user_required":
                raise  # Leave the queue for apply to resolve an account choice.


def shortcut_artwork(paths, path):
    from . import update
    version = update.current(paths)
    if not version:
        return []
    ident = shortcut_identity(paths)[1]
    source = paths.data / "versions" / version / "assets/steam"
    return [(path.parent / "grid" / f"{ident}{suffix}.png", (source / f"{name}.png").read_bytes())
            for name, suffix in ARTWORK.items() if (source / f"{name}.png").is_file()]


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
    plan = prepare_apply(paths)
    commit_apply(paths, plan)
    if restart:
        start()
    return {"applied": plan["changes"], "steam_restarted": bool(restart)}


def prepare_apply(paths):
    """Read/serialize before shutdown so the respawn window only contains disk writes."""
    data = state(paths)
    doc = read_vdf(mapping_path(paths))
    target = mappings(doc)
    changes, cleanup = [], []
    pending = data.get("default_pending")
    if pending:
        if pending == "default_on":
            if target.get("0", {}).get("name") != "DeckCompanion":
                data["previous_default_mapping"] = target.get("0")
            target["0"] = {"name": "DeckCompanion", "config": "", "priority": "250"}
        elif target.get("0", {}).get("name") == "DeckCompanion":
            previous = data.get("previous_default_mapping")
            if previous is None:
                target.pop("0", None)
            else:
                target["0"] = previous
        data["default_pending"] = None
        changes.append({"action": pending})
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
        writes.extend(shortcut_artwork(paths, path))
        data["shortcut_pending"] = False
        changes.append({"action": "shortcut"})
    writes = [(p, payload) for p, payload in writes if differs(p, payload)]
    originals = {p: p.read_bytes() if p.exists() else None for p, _ in writes}
    originals.update({p: p.read_bytes() for p in cleanup if p.exists()})
    return {"writes": writes, "originals": originals, "cleanup": cleanup, "data": data,
            "changes": changes, "mappings": {str(c.get("appid", "0")): target.get(str(c.get("appid", "0")))
                for c in changes if c["action"] != "shortcut"},
            "shortcut": any(c["action"] == "shortcut" for c in changes)}


def backup_apply(plan):
    # Stage backup bytes now; the closed-window path only renames them into place.
    plan["backups"] = []
    for path, payload in plan["originals"].items():
        if payload is not None:
            fd, name = tempfile.mkstemp(prefix=".dc-backup-", dir=path.parent)
            os.close(fd)
            staged = Path(name)
            backup = path.with_name(path.name + ".deckcompanion-" + now().replace(":", "-") + ".bak")
            plan["backups"].append((staged, backup))
            atomic(staged, payload)


def discard_prepared(plan):
    for staged, _ in plan.get("backups", []):
        staged.unlink(missing_ok=True)


def commit_apply(paths, plan, closed_window=False):
    """closed_window is only for the helper immediately after observing every Steam PID gone.

    Its caller holds state.lock and prepared backups before shutdown. Never query processes
    between writes on that path: SteamOS can respawn Steam in less than 200 milliseconds.
    State is committed by that caller only after post-return semantic verification.
    """
    writes, originals, cleanup = (plan[k] for k in ("writes", "originals", "cleanup"))
    written = []
    try:
        if closed_window:
            for staged, backup in plan["backups"]:
                os.replace(staged, backup)
        for path, payload in writes:
            if closed_window:
                atomic(path, payload)
            else:
                guarded_write(path, payload)
            written.append(path)
        for path in cleanup:
            if path.exists() and path.read_text(encoding="utf-8").startswith("# managed-by: DeckCompanion\n"):
                if not closed_window: require_closed()
                path.unlink()
                written.append(path)
        if not closed_window:
            save_state(paths, plan["data"])
    except Exception:
        for path in reversed(written):
            if not closed_window: require_closed()
            if originals[path] is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, originals[path])
        raise


def verify_apply(paths, plan):
    """Steam may reserialize its files after starting; compare our managed values only."""
    target = mappings(read_vdf(mapping_path(paths)))
    for ident, expected in plan["mappings"].items():
        actual = target.get(ident)
        if expected is None:
            if actual is not None: return False
        else:
            try:
                if not isinstance(actual, dict) or any(
                        (int(actual.get(k, -1)) != int(value) if k == "priority" else actual.get(k) != value)
                        for k, value in expected.items()):
                    return False
            except (TypeError, ValueError):
                return False
    if plan["shortcut"]:
        path = shortcut_path(paths)
        if differs(path, shortcut_bytes(paths, path)): return False
        from . import net
        if any(not p.is_file() or net.digest(p) != hashlib.sha256(payload).hexdigest()
               for p, payload in shortcut_artwork(paths, path)):
            return False
    return not any(p.exists() and p.read_text(encoding="utf-8").startswith("# managed-by: DeckCompanion\n")
                   for p in plan["cleanup"])
