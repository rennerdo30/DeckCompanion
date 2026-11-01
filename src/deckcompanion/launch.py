"""Prepare locally, then replace this process with Proton. Optional fixes never block play."""
import os
from pathlib import Path
import shutil
import tempfile
import tarfile
import sys
from . import archives, content, localfix, net, recipes, steam
from .common import Error, atomic, app_id, component, locked, now, save_state, state


def resolve_proton(paths, recipe, budget, log):
    available = steam.proton(paths, log)
    runtime = recipe.get("runtime", {})
    pin = runtime.get("proton", "GE-Proton")
    exact = next((p for p in available if recipe and p["name"] == pin), None)
    if exact:
        log(f"Using pinned {exact['name']}")
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
        # Recipe fallback retains its GE preference; unconfigured games use newest.
        fallback = next((p for p in available if p["name"].startswith("GE-Proton")), available[0]) if recipe else available[0]
        log(f"Using installed {fallback['name']} (requested {pin})")
        return Path(fallback["path"])
    raise Error("no_proton", "No installed Proton available; install GE-Proton or Valve Proton in Steam")


def prepare(paths, verb, command, env=None):
    env = dict(os.environ if env is None else env)
    if not command:
        raise Error("invalid_arguments", "launch requires a Proton verb and command")
    raw_ident = env.get("SteamAppId") or env.get("SteamGameId", "")
    log_path = paths.data / "logs" / f"{raw_ident if str(raw_ident).isdigit() else 'unknown'}-last.log"
    warnings = []
    logging_ok = True
    def log(message):
        nonlocal logging_ok
        if message.startswith(("Mod ", "Setting ", "Optional content skipped:")):
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
            game_raw = steam.installed(paths).get(ident, {}).get("path")
            game = Path(game_raw) if game_raw else None
            active = saved.get("enabled", True)
            optional_ok = True
            if active:
                for operation in (
                    lambda: localfix.generate(paths, ident, recipe),
                    lambda: saved.update(installed_mods=content.reconcile(paths, ident, recipe, prefix, game, saved.get("mods", {}), budget, log)),
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
    return args or [str(proton / "proton"), verb, *command], env


def launch(paths, verb, command):
    args, env = prepare(paths, verb, command)
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
