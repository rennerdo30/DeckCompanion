"""Prepare locally, then replace this process with Proton. Optional fixes never block play."""
import os
from pathlib import Path
import shutil
import tempfile
import tarfile
from . import archives, content, localfix, net, recipes, steam
from .common import Error, atomic, app_id, component, locked, now, save_state, state


def resolve_proton(paths, recipe, budget, log):
    available = steam.proton(paths)
    runtime = recipe.get("runtime", {})
    pin = runtime.get("proton", "GE-Proton")
    exact = next((p for p in available if p["name"] == pin), None)
    if exact:
        return Path(exact["path"])
    if pin != "GE-Proton" and runtime.get("url") and runtime.get("sha256"):
        staging = None
        try:
            component(pin)
            source = net.cached(paths, runtime["url"], runtime["sha256"], budget)
            paths.tools.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".dc-proton-", dir=paths.tools))
            archives.unpack(source, staging, budget)
            root = staging / pin
            if not (root / "proton").is_file():
                raise Error("invalid_proton", "Runtime archive lacks the pinned Proton executable")
            target = paths.tools / pin
            if target.exists():
                raise Error("invalid_proton", "Preserving an existing runtime directory")
            os.replace(root, target)
            return target
        except (Error, OSError, ValueError, tarfile.TarError) as exc:
            log(f"Pinned Proton unavailable: {exc}")
        finally:
            if staging:
                shutil.rmtree(staging)
    if available:
        log(f"Using installed {available[0]['name']} (requested {pin})")
        return Path(available[0]["path"])
    raise Error("no_proton", "No installed Proton available; install GE-Proton or Valve Proton in Steam")


def prepare(paths, verb, command, env=None):
    env = dict(os.environ if env is None else env)
    ident = app_id(env.get("SteamAppId") or env.get("SteamGameId", ""))
    if not command:
        raise Error("invalid_arguments", "launch requires a Proton verb and command")
    log_path = paths.data / "logs" / f"{ident}-last.log"
    atomic(log_path, f"{now()} Preparing {ident}\n")
    def log(message):
        with log_path.open("a", encoding="utf-8") as output:
            output.write(f"{now()} {message}\n")
    with locked(paths):
        data = state(paths)
        saved = data["games"].setdefault(ident, {})
        saved["last_launch"] = {"time": now(), "ok": False}
        save_state(paths, data)
        try:
            recipe = recipes.load(paths, ident) or {}
        except Error as exc:
            log(f"Recipe ignored: {exc}")
            recipe = {}
        budget = net.Budget()
        proton = resolve_proton(paths, recipe, budget, log)
        prefix_raw = env.get("STEAM_COMPAT_DATA_PATH")
        prefix = Path(prefix_raw).resolve() if prefix_raw else None
        game_raw = steam.installed(paths).get(ident, {}).get("path")
        game = Path(game_raw) if game_raw else None
        # A mapping/direct selection enables the recipe unless disable was actually applied.
        active = saved.get("enabled", True)
        if active:
            try:
                localfix.generate(paths, ident, recipe)
            except (Error, OSError) as exc:
                log(str(exc))
            try:
                saved["installed_mods"] = content.reconcile(paths, ident, recipe, prefix, game, saved.get("mods", {}), budget, log)
                content.settings(recipe, prefix, game, log)
            except (Error, OSError, ValueError, TypeError, KeyError) as exc:
                log(f"Optional content skipped: {exc}")
            env.update(recipe.get("runtime", {}).get("env", {}))
        else:
            localfix.remove(paths, ident)
        args = [str(proton / "proton"), verb, *command]
        if active:
            args += recipe.get("runtime", {}).get("launch_options", [])
        env.setdefault("STEAM_COMPAT_CLIENT_INSTALL_PATH", str(paths.steam))
        # ok is preparation/handoff status, not a claim the game ran successfully.
        saved["last_launch"] = {"time": now(), "ok": True}
        save_state(paths, data)
        log(f"Handing off to {proton.name}; game result not observed")
    return args, env


def launch(paths, verb, command):
    args, env = prepare(paths, verb, command)
    try:
        os.execve(args[0], args, env)
    except OSError:
        ident = app_id(env.get("SteamAppId") or env.get("SteamGameId", ""))
        with locked(paths):
            data = state(paths)
            data["games"][ident]["last_launch"]["ok"] = False
            save_state(paths, data)
        raise
