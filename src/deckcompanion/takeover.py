"""Discover eligible Windows titles and queue the existing reversible mapping transaction."""
import re
from pathlib import Path
import vdf
from . import appinfo, steam


def windows(entry, metadata):
    app = metadata["apps"].get(entry["appid"], {})
    if str(app.get("type", "")).lower() not in ("", "game", "application"):
        return False
    manifest = steam.read_vdf(Path(entry["library"]) /
                              f"steamapps/appmanifest_{entry['appid']}.acf").get("AppState", {})
    if int(manifest.get("StateFlags", "0")) & 1:
        return False
    def platforms(value):
        return set(re.split(r"[,\s]+", str(value).lower())) - {""}
    chosen = manifest.get("UserConfig", {}).get("platform_override_dest") or manifest.get("oslist")
    if chosen:
        return "windows" in platforms(chosen) and "linux" not in platforms(chosen)
    depots = app.get("depots", {})
    installed = set(manifest.get("InstalledDepots", {}))
    systems = set().union(*(platforms(depots.get(d, "")) for d in installed))
    if "linux" in systems:
        return False
    if "windows" in systems:
        return True
    systems = platforms(app.get("oslist", ""))
    if systems:
        return "windows" in systems and "linux" not in systems
    # A known depot list with no Windows depots is never evidence of a Windows title.
    systems = set().union(*(platforms(v) for v in depots.values()))
    return "windows" in systems and "linux" not in systems


def discover(paths):
    entries = steam.installed(paths)
    metadata = appinfo.load(paths)
    eligible = {ident for ident, entry in entries.items() if windows(entry, metadata)}
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


def reconcile(paths, data, uninstall=False):
    entries, eligible = discover(paths) if not uninstall else ({}, set())
    enabled = data.get("settings", {}).get("takeover_all", True) and not uninstall
    target = steam.mappings(steam.read_vdf(steam.mapping_path(paths)))
    for ident in eligible | set(data["games"]):
        saved = data["games"].get(ident, {})
        desired = enabled and ident in eligible and not saved.get("takeover_excluded", False)
        if desired:
            saved = data["games"].setdefault(ident, {})
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
            "pending": sum(bool(g.get("takeover_managed") and g.get("pending")) for g in data["games"].values())}
