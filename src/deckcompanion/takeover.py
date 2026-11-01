"""Discover eligible Windows titles and queue the existing reversible mapping transaction."""
import re
from copy import deepcopy
from pathlib import Path
import vdf
from . import appinfo, steam
from .common import now


TOOL_IDS = {"228980", "1070560", "1391110", "1628350", "4183110", "1493710", "2180100", "4628710", "891390", "1007"}


def is_game(ident, metadata, entry=None):
    app = metadata.get("apps", {}).get(ident, {})
    tool_ids = {str(v.get("appid")) for v in metadata.get("compat_tools", {}).values() if isinstance(v, dict)}
    if ident in TOOL_IDS | tool_ids:
        return False
    if str(app.get("type", "")).lower() not in ("", "game", "application", "demo"):
        return False
    name = (entry or {}).get("name", "")
    return not re.match(r"^(?:Steam Linux Runtime\b|Steamworks .*?(?:Redistributables|Redist)\b|"
                        r"Proton(?:[ -]+(?:\d|Hotfix\b|Experimental\b)))", name, re.I)


def windows(entry, metadata, mapping=None):
    app = metadata.get("apps", {}).get(entry["appid"], {})
    if not is_game(entry["appid"], metadata, entry):
        return False
    manifest = steam.read_vdf(Path(entry["library"]) /
                              f"steamapps/appmanifest_{entry['appid']}.acf").get("AppState", {})
    if int(manifest.get("StateFlags", "0")) & 1:
        return False
    def platforms(value):
        return set(re.split(r"[,\s]+", str(value).lower())) - {""}
    runtime = appinfo.recommendation(metadata, entry["appid"])
    if runtime and runtime.lower() == "native":
        return False
    systems = platforms(app.get("oslist", ""))
    if systems and "windows" not in systems and "linux" not in systems:
        return False  # A macOS-only app has no Windows build Proton could run.
    if (runtime and runtime.lower().startswith("proton")) or (mapping or {}).get("name"):
        return True
    chosen = manifest.get("UserConfig", {}).get("platform_override_dest") or manifest.get("oslist")
    if chosen:
        return "windows" in platforms(chosen) and "linux" not in platforms(chosen)
    return "windows" in systems and "linux" not in systems


def discover(paths):
    entries = steam.installed(paths)
    metadata = appinfo.load(paths)
    entries = {i: e for i, e in entries.items() if is_game(i, metadata, e)}
    target = steam.mappings(steam.read_vdf(steam.mapping_path(paths)))
    eligible = {ident for ident, entry in entries.items() if windows(entry, metadata, target.get(ident))}
    # Mapping is machine-wide; inspect every account's Windows shortcuts, not native wrappers.
    for path in sorted((paths.steam / "userdata").glob("*/config/shortcuts.vdf")):
        doc = vdf.binary_loads(path.read_bytes())
        for value in doc.get("shortcuts", {}).values():
            exe = value.get("Exe", "").strip().strip('"')
            raw = value.get("appid")
            if not exe.lower().endswith(".exe") or raw is None:
                continue
            ident = str(int(raw) & 0xffffffff)
            if ident == "0":
                continue
            entries.setdefault(ident, {"appid": ident, "name": value.get("AppName", ident),
                                       "path": exe, "kind": "shortcut"})
            eligible.add(ident)
    return entries, eligible


def capture(data, ident, target):
    """First-affected entries are immutable; None records an absent key, not an empty dict."""
    snapshot = data.setdefault("takeover_snapshot", {"taken_at": now(), "entries": {}, "default_managed": False})
    if ident not in snapshot["entries"]:
        previous = target.get(ident)
        if ident == "0" and "previous_default_mapping" in data:
            previous = data["previous_default_mapping"]
        elif ident != "0":
            saved = data["games"].get(ident, {})
            if (saved.get("enabled") or saved.get("takeover_snapshot")) and "previous_mapping" in saved:
                previous = saved["previous_mapping"]
        snapshot["entries"][ident] = deepcopy(previous)
    if ident == "0" and ((target.get("0") or {}).get("name") == "DeckCompanion" or
                         data.get("default_pending") == "default_on" or "previous_default_mapping" in data):
        snapshot["default_managed"] = True


def migrate_snapshot(data, target):
    # Upgrade 0.4.2's session snapshots without replacing the user's preserved originals.
    for ident, saved in data["games"].items():
        if (saved.get("enabled") or saved.get("takeover_snapshot")) and "previous_mapping" in saved:
            capture(data, ident, target)
    if "previous_default_mapping" in data or data.get("default_pending") == "default_on":
        capture(data, "0", target)
    if data.get("takeover_snapshot"):
        capture(data, "0", target)


def snapshot_status(data):
    snapshot = data.get("takeover_snapshot")
    return {"taken_at": snapshot["taken_at"], "games": len(set(snapshot["entries"]) - {"0"})} if snapshot else None


def restore_pending(data):
    ids = set(data.get("restore_pending", []))
    return ([{"appid": i, "action": "restore"} for i in sorted(ids - {"0"}, key=int)] +
            ([{"action": "restore_default"}] if "0" in ids else []))


def restore_all(paths, data):
    target = steam.mappings(steam.read_vdf(steam.mapping_path(paths)))
    migrate_snapshot(data, target)
    data.setdefault("settings", {})["takeover_all"] = False
    for saved in data["games"].values():
        saved["takeover_excluded"] = False
        saved["pending"] = None
    data["default_pending"] = None
    snapshot = data.get("takeover_snapshot", {})
    data["restore_pending"] = [i for i, value in snapshot.get("entries", {}).items()
                               if (i != "0" or snapshot.get("default_managed")) and target.get(i) != value]
    for ident in snapshot.get("entries", {}):
        if ident != "0":
            saved = data["games"].setdefault(ident, {})
            if ident not in data["restore_pending"]:
                saved.update(enabled=False, takeover_managed=False, takeover_snapshot=False)
    pending = restore_pending(data)
    return {"restoring": len(pending), "pending": pending}


def reconcile(paths, data, uninstall=False):
    entries, eligible = discover(paths) if not uninstall else ({}, set())
    enabled = data.get("settings", {}).get("takeover_all", True) and not uninstall
    target = steam.mappings(steam.read_vdf(steam.mapping_path(paths)))
    migrate_snapshot(data, target)
    metadata = appinfo.load(paths)
    installed = steam.installed(paths) if not uninstall else {}
    for ident in eligible | set(data["games"]):
        saved = data["games"].get(ident, {})
        if ident in data.get("restore_pending", []):
            continue
        if not is_game(ident, metadata, installed.get(ident)):
            if saved.get("enabled"):
                saved["pending"] = "disable"
            elif saved.get("pending") == "enable":
                saved["pending"] = None
            continue
        desired = enabled and ident in eligible and not saved.get("takeover_excluded", False)
        if desired:
            saved = data["games"].setdefault(ident, {})
            capture(data, "0", target)
            capture(data, ident, target)
            if not saved.get("takeover_snapshot"):
                if not saved.get("enabled"):
                    previous = target.get(ident)
                    saved["previous_mapping"] = previous if (previous or {}).get("name") != "DeckCompanion" else None
                saved["takeover_snapshot"] = True
            saved["takeover_managed"] = True
            if not saved.get("enabled") or not steam.mapping_matches(target.get(ident)) or saved.get("pending") == "disable":
                saved["pending"] = "enable"
        elif saved.get("takeover_managed"):
            if saved.get("enabled"):
                saved["pending"] = "disable"
            else:
                saved.update(pending=None, takeover_managed=False, takeover_snapshot=False)
    return entries, eligible


def fields(data, ident, eligible):
    saved = data["games"].get(ident, {})
    wanted = data.get("settings", {}).get("takeover_all", True)
    label = "excluded" if saved.get("takeover_excluded") and ident in eligible else (
        "on" if wanted and ident in eligible else "n/a")
    previous = saved.get("previous_mapping")
    return {"takeover": label, "previous_mapping": previous.get("name") if isinstance(previous, dict) else None}


def counts(data, eligible):
    return {"eligible": len(eligible),
            "on": sum(fields(data, i, eligible)["takeover"] == "on" for i in eligible),
            "excluded": sum(fields(data, i, eligible)["takeover"] == "excluded" for i in eligible),
            "applied": sum(bool(g.get("takeover_managed") and g.get("enabled")) for g in data["games"].values()),
            "pending": sum(bool(g.get("takeover_managed") and (g.get("pending") or i in data.get("restore_pending", [])))
                           for i, g in data["games"].items())}
