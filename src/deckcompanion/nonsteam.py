"""Registered Windows games, using the existing guarded Steam apply transaction."""
import hashlib
import os
from pathlib import Path
import re
import unicodedata
import zlib
from . import content, net, recipes, splash, steam, umu
from .common import Error, inside, locked, now, read_json, save_state, state


def entry(paths, slug, data=None):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,100}", slug):
        raise Error("invalid_slug", "Invalid non-Steam game slug")
    saved = (state(paths) if data is None else data).get("non_steam", {}).get(slug)
    if not saved or saved.get("removed"):
        raise Error("unknown_game", "This non-Steam game is not registered")
    return saved


def identity(paths, saved):
    exe = '"' + str(paths.data / "bin/deckcompanion") + '"'
    # Include slug in the CRC input: duplicate display names remain distinct.
    unsigned = zlib.crc32((exe + "run-umu " + saved["slug"]).encode()) | 0x80000000
    return exe, unsigned


def add(paths, exe, name, store="none", umu_id=None):
    exe = Path(exe).expanduser().resolve()
    name = name.strip()
    if not exe.is_file() or exe.suffix.lower() != ".exe":
        raise Error("invalid_exe", "Choose an existing Windows .exe file")
    if not name or len(name) > 128 or any(ord(c) < 32 for c in name):
        raise Error("invalid_name", "Enter a game name of 1 to 128 characters")
    if store not in ("egs", "gog", "none"):
        raise Error("invalid_store", "Store must be egs, gog or none")
    data = state(paths)
    registry = data.setdefault("non_steam", {})
    if any(g["exe"] == str(exe) and not g.get("removed") for g in registry.values()):
        raise Error("already_registered", "This executable is already registered")
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:65] or "game"
    if slug in registry:
        slug += "-" + hashlib.sha256(str(exe).encode()).hexdigest()[:12]
    if slug in registry:
        raise Error("game_conflict", "Preserving previous registration and prefix; choose another name")
    saved = {"slug": slug, "name": name, "exe": str(exe), "store": store,
             "umu_id": umu.lookup(paths, name, store, umu_id), "pending": "add"}
    saved["appid"] = str(identity(paths, saved)[1])
    if saved["appid"] in steam.installed(paths) or any(g["appid"] == saved["appid"] for g in registry.values()):
        raise Error("shortcut_conflict", "Shortcut identifier collision; choose another name")
    inside(paths.data, f"prefixes/{slug}").mkdir(parents=True, exist_ok=True)
    registry[slug] = saved
    save_state(paths, data)
    return game(paths, saved)


def remove(paths, slug):
    data = state(paths)
    saved = entry(paths, slug, data)
    saved.update(removed=True, pending="remove")
    save_state(paths, data)
    return {"slug": slug, "removed": True, "steam_changes_pending": True, "prefix_preserved": True}


def roots(paths, saved):
    return inside(paths.data, f"prefixes/{saved['slug']}"), Path(saved["exe"]).parent


def game(paths, saved):
    from .launch import select_proton
    recipe = recipes.load(paths, saved["umu_id"]) or {}
    proton, reason = select_proton(steam.proton(paths), recipe or {"runtime": {}})
    prefix, folder = roots(paths, saved)
    manifest = read_json(paths.data / "manifests" / f"{saved['appid']}.json", {"mods": {}})
    mods = []
    for mod in recipe.get("mods", []):
        enabled = saved.get("mods", {}).get(mod["id"], mod.get("enabled", True))
        status, message = content.mod_state(mod, enabled, manifest["mods"].get(mod["id"]),
            saved.get("mod_status", {}).get(mod["id"]), prefix, folder)
        mods.append(dict(mod, enabled=enabled, state=status, state_message=message,
                         installed_version=saved.get("installed_mods", {}).get(mod["id"])))
    return {**saved, "kind": "non_steam", "installed": True, "has_recipe": bool(recipe),
            "enabled": True, "pending": None, "shortcut_pending": saved.get("pending"),
            "recipe_version": recipe.get("version"), "mods": mods, "last_launch": saved.get("last_launch"),
            "notes": recipe.get("notes", ""), "art": steam.artwork(paths, saved["appid"]),
            "proton": dict(proton, reason=reason) if proton else None}


def shortcut_bytes(paths, payload, saved):
    import vdf
    doc = vdf.binary_loads(payload)
    entries = list(doc["shortcuts"].values())
    exe, unsigned = identity(paths, saved)
    ident = unsigned - 0x100000000
    options = "run-umu " + saved["slug"]
    owned = lambda e: e.get("appid") == ident and e.get("Exe") == exe and e.get("LaunchOptions") == options
    if any(e.get("appid") == ident and not owned(e) for e in entries):
        raise Error("shortcut_conflict", "Preserving another Steam shortcut with this identifier")
    previous = next((e for e in entries if owned(e)), None)
    if saved.get("removed"):
        entries = [e for e in entries if not owned(e)]
    else:
        desired = dict(previous or {"tags": {}}, appid=ident, AppName=saved["name"], Exe=exe,
                       StartDir='"' + str(Path(saved["exe"]).parent) + '"', LaunchOptions=options,
                       icon=str(paths.data / "assets/icon.png"), IsHidden=0, AllowDesktopConfig=1, AllowOverlay=1)
        entries = [desired if owned(e) else e for e in entries] if previous else [*entries, desired]
    doc["shortcuts"] = {str(i): e for i, e in enumerate(entries)}
    return vdf.binary_dumps(doc)


def run(paths, slug, env=None):
    from .launch import resolve_proton
    saved = entry(paths, slug)
    env = dict(os.environ if env is None else env)
    ident = saved["appid"]
    warnings = []
    def log(message):
        if message.startswith(("Mod ", "Setting ")):
            warnings.append(message)
        splash.log(paths, ident, "umu: " + message)
    prefix, folder = roots(paths, saved)
    budget = net.Budget()
    recipe = {}
    try:
        recipe = recipes.load(paths, saved["umu_id"]) or {}
    except Exception as exc:
        warnings.append(str(exc))
        log(f"Recipe skipped: {exc}; continuing without optional fixes")
    try:
        proton = resolve_proton(paths, recipe or {"runtime": {}}, budget, log)
    except Exception as exc:
        log(f"Proton unavailable: {exc}")
        raise
    # Required runtime failure is actionable; optional preparation always fails open.
    try:
        runner = umu.install(paths, budget)["path"]
    except Exception as exc:
        log(f"umu unavailable: {exc}; run deckcompanion umu install")
        raise
    args = [runner, saved["exe"]]
    try:
        with locked(paths):
            data = state(paths)
            current = entry(paths, slug, data)
            current["mod_status"] = {}
            current["installed_mods"] = content.reconcile(paths, ident, recipe, prefix, folder,
                current.get("mods", {}), budget, log, current["mod_status"])
            content.settings(recipe, prefix, folder, log)
            env.update(recipe.get("runtime", {}).get("env", {}))
            args += recipe.get("runtime", {}).get("launch_options", [])
            current["last_launch"] = {"time": now(), "ok": True}
            save_state(paths, data)
    except Exception as exc:
        warnings.append(str(exc))
        log(f"Optional preparation skipped: {exc}; continuing launch")
    env.update(WINEPREFIX=str(inside(prefix, "pfx")), GAMEID=saved["umu_id"], STORE=saved["store"], PROTONPATH=str(proton))
    try:
        summary = game(paths, entry(paths, slug))
        summary["proton"] = {"name": proton.name, "reason": "pinned" if recipe.get("runtime", {}).get("proton") == proton.name else "recipe_fallback"}
        splash.show(paths, {**summary, "warnings": warnings}, env)
    except Exception as exc:
        log(f"Splash skipped: {exc}; continuing launch")
    log(f"Handing off to umu-run with {proton.name}; game result not observed")
    try:
        os.chdir(folder)
        os.execve(runner, args, env)
    except OSError as exc:
        log(f"umu handoff failed: {exc}")
        try:
            with locked(paths):
                data = state(paths)
                entry(paths, slug, data)["last_launch"] = {"time": now(), "ok": False}
                save_state(paths, data)
        except Exception:
            pass
        raise
