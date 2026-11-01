"""Prepare locally, then replace this process with Proton. Optional fixes never block play."""
import os
from pathlib import Path
import sys
from . import content, localfix, net, recipes, splash, steam
from .common import Error, atomic, app_id, locked, now, read_json, save_state, state


def select_proton(available, recipe):
    """Installed build for both launch fallback and read-only game previews."""
    pin = recipe.get("runtime", {}).get("proton", "GE-Proton")
    exact = next((p for p in available if recipe and p["name"] == pin), None)
    if exact:
        return exact, "pinned"
    if not available:
        return None, None
    fallback = next((p for p in available if p["name"].startswith("GE-Proton")), available[0]) if recipe else available[0]
    return fallback, "recipe_fallback" if recipe else "newest"


def resolve_proton(paths, recipe, budget, log):
    available = steam.proton(paths, log)
    selected, reason = select_proton(available, recipe)
    runtime = recipe.get("runtime", {})
    pin = runtime.get("proton", "GE-Proton")
    if reason == "pinned":
        log(f"Using pinned {selected['name']}")
        return Path(selected["path"])
    if available:
        # Recipe fallback retains its GE preference; unconfigured games use newest.
        fallback = selected
        log(f"Using installed {fallback['name']} (requested {pin})")
        return Path(fallback["path"])
    raise Error("no_proton", "No installed Proton available; install GE-Proton or Valve Proton in Steam")


def prepare(paths, verb, command, env=None, splash_info=None):
    env = dict(os.environ if env is None else env)
    if not command:
        raise Error("invalid_arguments", "launch requires a Proton verb and command")
    raw_ident = env.get("SteamAppId") or env.get("SteamGameId", "")
    log_path = paths.data / "logs" / f"{raw_ident if str(raw_ident).isdigit() else 'unknown'}-last.log"
    warnings = []
    logging_ok = True
    def log(message):
        nonlocal logging_ok
        if message.startswith(("Mod ", "Setting ", "Optional content skipped:", "Engine preparation skipped:", "Pinned Proton unavailable:")):
            warnings.append(message)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as output:
                output.write(f"{now()} {message}\n")
        except Exception:
            logging_ok = False
            try:
                print(f"DeckCompanion: {message}", file=sys.stderr)
            except Exception:
                pass  # Even an unavailable log/stderr cannot block a game.
    try:
        atomic(log_path, "")
    except Exception:
        logging_ok = False
    log(f"Preparing {raw_ident}")
    available = steam.proton(paths, log)
    proton = Path(available[0]["path"]) if available else None
    original_env = dict(env)
    args = None
    recipe, saved, entry = {}, {}, {}
    prefix, game = None, None
    chain_ready = False
    ident = None
    try:
        ident = app_id(raw_ident)
        with locked(paths):
            data = state(paths)
            saved = data["games"].setdefault(ident, {})
            recipe = recipes.load(paths, ident) or {}
            budget = net.Budget()
            # Resolve the recipe pin before optional work; keep it on later failure.
            proton = resolve_proton(paths, recipe, budget, log)
            prefix_raw = env.get("STEAM_COMPAT_DATA_PATH")
            prefix = Path(prefix_raw).resolve() if prefix_raw else None
            entry = steam.installed(paths).get(ident, {})
            game_raw = entry.get("path")
            game = Path(game_raw) if game_raw else None
            active = saved.get("enabled", True)
            optional_ok = True
            if active:
                saved["mod_status"] = {}
                def generate_fix():
                    nonlocal chain_ready
                    localfix.generate(paths, ident, recipe)
                    chain_ready = True
                for operation in (
                    generate_fix,
                    lambda: saved.update(installed_mods=content.reconcile(paths, ident, recipe, prefix, game, saved.get("mods", {}), budget, log, saved["mod_status"])),
                    lambda: content.settings(recipe, prefix, game, log),
                ):
                    try:
                        operation()
                    except Exception as exc:
                        optional_ok = False
                        log(f"Optional content skipped: {exc}")
            else:
                localfix.remove(paths, ident)
            args = [str(proton / "proton"), verb, *command]
            if active and optional_ok and logging_ok:
                env.update(recipe.get("runtime", {}).get("env", {}))
                args += recipe.get("runtime", {}).get("launch_options", [])
            saved["last_launch"] = {"time": now(), "ok": True}
            saved["content_warnings"] = warnings
            save_state(paths, data)
    except Exception as exc:
        log(f"Engine preparation skipped: {exc}; launching with original arguments")
        args, env = None, original_env
        if proton is None:
            available = steam.proton(paths, log)
            proton = Path(available[0]["path"]) if available else None
    if proton is None:
        message = "No installed Proton available; install GE-Proton, UMU-Proton or Valve Proton in Steam"
        log(message)
        raise Error("no_proton", message)
    env.setdefault("STEAM_COMPAT_CLIENT_INSTALL_PATH", str(paths.steam))
    log(f"Handing off to {proton.name}; game result not observed")
    if splash_info is not None and ident is not None:
        try:
            manifest = read_json(paths.data / "manifests" / f"{raw_ident}.json", {"mods": {}})
            mods = []
            for mod in recipe.get("mods", []):
                enabled = saved.get("mods", {}).get(mod["id"], mod.get("enabled", True))
                status, message = content.mod_state(mod, enabled, manifest["mods"].get(mod["id"]),
                    saved.get("mod_status", {}).get(mod["id"]), prefix, game)
                mods.append({"id": mod["id"], "name": mod["name"], "version": mod["version"],
                             "enabled": enabled, "state": status, "state_message": message,
                             "installed_version": saved.get("installed_mods", {}).get(mod["id"])})
            wine = recipe.get("wine", {}) if chain_ready else {}
            steps = ["Winetricks: " + step for step in wine.get("winetricks", [])]
            steps += ["Registry: " + step["key"] + " / " + step["name"] for step in wine.get("registry", [])]
            steps += ["DLL: " + key + " = " + value for key, value in wine.get("dll_overrides", {}).items()]
            steps += ["Environment: " + key for key in wine.get("env", {})]
            pin = recipe.get("runtime", {}).get("proton", "GE-Proton")
            splash_info.update(appid=raw_ident, name=(entry or recipe).get("name", raw_ident),
                art=steam.artwork(paths, raw_ident), recipe_version=recipe.get("version"),
                proton={"name": proton.name, "reason": "pinned" if recipe and proton.name == pin else "recipe_fallback" if recipe else "newest"},
                fixes={"upstream_chained": chain_ready, "wine_steps_count": len(steps), "wine_steps": steps},
                mods=mods, warnings=list(warnings))
        except Exception as exc:
            splash.log(paths, raw_ident, f"summary unavailable: {exc}")
    return args or [str(proton / "proton"), verb, *command], env


def native(command):
    """True when Steam hands us a Linux program (ELF or script), e.g. a non-Steam shortcut.

    As Steam's default compatibility tool we also receive native shortcuts such as
    DeckCompanion's own UI or emulators; Proton cannot run those (hardware check 2026-11-01).
    """
    try:
        with open(command[0], "rb") as source:
            head = source.read(4)
    except (OSError, IndexError, TypeError):
        return False
    return head == b"\x7fELF" or head.startswith(b"#!")


def launch(paths, verb, command):
    if native(command):
        if verb not in ("run", "waitforexitandrun"):
            return  # Prefix queries such as getcompatpath mean nothing for a native program.
        os.execv(command[0], command)
        return
    info = {}
    args, env = prepare(paths, verb, command, splash_info=info)
    if verb in ("run", "waitforexitandrun") and info:
        try:
            splash.show(paths, info, env)
        except Exception as exc:
            splash.log(paths, info.get("appid"), f"{exc}; continuing launch")
    try:
        os.execve(args[0], args, env)
    except OSError:
        try:
            ident = app_id(env.get("SteamAppId") or env.get("SteamGameId", ""))
            with locked(paths):
                data = state(paths)
                data["games"][ident]["last_launch"]["ok"] = False
                save_state(paths, data)
        except Exception:
            pass
        raise
