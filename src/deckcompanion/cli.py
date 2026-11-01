"""The JSON interface shared by the desktop UI and command-line users."""
import argparse
from collections import deque
import json
import math
import sys
from . import __version__, appinfo, content, recipes, steam, takeover
from .common import Error, Paths, app_id, locked, read_json, state, save_state


def game(paths, ident, discovery=None, metadata=None, available=None):
    from .launch import selection
    ident = app_id(ident)
    from . import nonsteam
    registered = next((g for g in state(paths).get("non_steam", {}).values()
                       if g["appid"] == ident and not g.get("removed")), None)
    if registered:
        return nonsteam.game(paths, registered)
    discovery = takeover.discover(paths) if discovery is None else discovery
    entry = discovery[0].get(ident)
    recipe = recipes.load(paths, ident)
    saved = state(paths)["games"].get(ident, {})
    prefix, game_root = steam.content_roots(paths, ident)
    manifest = read_json(paths.data / "manifests" / f"{ident}.json", {"mods": {}})
    selected, reason, valve = selection(paths, ident, recipe or {}, saved, available, metadata)
    mods = []
    for mod in (recipe or {}).get("mods", []):
        enabled = saved.get("mods", {}).get(mod["id"], mod.get("enabled", True))
        status, message = content.mod_state(mod, enabled, manifest["mods"].get(mod["id"]),
            saved.get("mod_status", {}).get(mod["id"]), prefix, game_root)
        mods.append({"id": mod["id"], "name": mod["name"], "version": mod["version"],
                     "enabled": enabled, "state": status, "state_message": message,
                     "installed_version": saved.get("installed_mods", {}).get(mod["id"])})
    return {"appid": ident, "name": (entry or recipe or {}).get("name", ident), "installed": entry is not None,
            "has_recipe": recipe is not None, "enabled": saved.get("enabled", False),
            "pending": saved.get("pending"), "recipe_version": recipe["version"] if recipe else None,
            "mods": mods, "last_launch": saved.get("last_launch"), "art": steam.artwork(paths, ident),
            **({"kind": entry["kind"]} if entry and "kind" in entry else {}),
            **takeover.fields(state(paths), ident, discovery[1]), "valve_recommendation": valve,
            "proton": {"name": selected["name"], "reason": reason} if selected else None}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Error("invalid_arguments", message)


def parser():
    root = Parser(prog="deckcompanion")
    root.add_argument("--json", action="store_true")
    commands = root.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add-game")
    add.add_argument("--exe", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--store", choices=("egs", "gog", "none"), default="none")
    add.add_argument("--umu-id")
    add.add_argument("--json", action="store_true")
    for name in ("remove-game", "run-umu"):
        sub = commands.add_parser(name)
        sub.add_argument("slug")
        sub.add_argument("--json", action="store_true")
    runtime = commands.add_parser("umu")
    runtime.add_argument("action", choices=("install",))
    runtime.add_argument("--json", action="store_true")
    for name in ("games", "game", "game-option", "enable", "disable", "mod", "default", "apply", "status", "update", "rollback", "settings"):
        sub = commands.add_parser(name)
        sub.add_argument("--json", action="store_true")
        if name in ("game", "game-option", "enable", "disable", "mod"):
            sub.add_argument("appid", type=app_id)
        if name == "game-option":
            sub.add_argument("--takeover", choices=("on", "off"))
            sub.add_argument("--proton", help="Installed build/tool name, or auto to clear the override")
        if name == "mod":
            sub.add_argument("mod_id")
        if name in ("mod", "default"):
            group = sub.add_mutually_exclusive_group(required=True)
            group.add_argument("--on", action="store_true")
            group.add_argument("--off", action="store_true")
        if name == "apply":
            sub.add_argument("--restart-steam", action="store_true")
        if name == "settings":
            sub.add_argument("--splash", choices=("on", "off"))
            sub.add_argument("--splash-seconds", type=float)
            sub.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    launch = commands.add_parser("launch")
    launch.add_argument("--json", action="store_true")
    launch.add_argument("verb")
    launch.add_argument("cmd", nargs=argparse.REMAINDER)
    helper = commands.add_parser("apply-helper", help="internal detached Steam apply worker")
    helper.add_argument("--json", action="store_true")
    helper.add_argument("--game-mode", action="store_true")
    commands.choices["update"].add_argument("--detach", action="store_true")
    proton = commands.add_parser("proton")
    proton.add_argument("action", nargs="?", choices=("install",))
    proton.add_argument("name", nargs="?")
    proton.add_argument("--json", action="store_true")
    proton.add_argument("--detach", action="store_true")
    worker = commands.add_parser("proton-helper", help=argparse.SUPPRESS)
    worker.add_argument("task", choices=("install", "update"))
    worker.add_argument("name", nargs="?")
    worker.add_argument("--json", action="store_true")
    for name in ("install", "uninstall"):
        sub = commands.add_parser(name)
        sub.add_argument("--json", action="store_true")
        if name == "install":
            from pathlib import Path
            sub.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    return root


def execute(args, paths):
    name = args.command
    if name in ("proton", "proton-helper"):
        from . import proton
        if name == "proton-helper":
            return proton.run(paths, args.task, args.name)
        if not args.action:
            if args.name or args.detach:
                raise Error("invalid_arguments", "Use proton install <name> --detach")
            return proton.listing(paths)
        if not args.name:
            raise Error("invalid_arguments", "proton install requires a build name")
        return (proton.schedule if args.detach else proton.run)(paths, "install", args.name)
    if name == "update":
        from . import proton
        return (proton.schedule if args.detach else proton.run)(paths, "update")
    data = state(paths)
    if name in ("add-game", "remove-game", "umu"):
        from . import nonsteam, umu
        if name == "umu":
            return umu.install(paths)
        if name == "remove-game":
            return nonsteam.remove(paths, args.slug)
        return nonsteam.add(paths, args.exe, args.name, args.store, args.umu_id)
    if name == "settings":
        from .splash import DEFAULTS, MAX_SECONDS, settings
        options = settings(data)
        for setting in args.set:
            key, separator, value = setting.partition("=")
            if not separator or key not in DEFAULTS:
                raise Error("invalid_arguments", "Unknown setting; expected a documented key=value")
            if key == "splash_seconds":
                try:
                    number = float(value)
                except ValueError as exc:
                    raise Error("invalid_arguments", "Splash duration must be a finite number") from exc
                if not math.isfinite(number):
                    raise Error("invalid_arguments", "Splash duration must be a finite number")
                options[key] = max(1.0, min(MAX_SECONDS, number))
            else:
                if value.lower() not in ("true", "false", "on", "off"):
                    raise Error("invalid_arguments", "Boolean settings accept true/false or on/off")
                options[key] = value.lower() in ("true", "on")
        if args.splash is not None:
            options["splash"] = args.splash == "on"
        if args.splash_seconds is not None:
            if not math.isfinite(args.splash_seconds):
                raise Error("invalid_arguments", "Splash duration must be a finite number")
            options["splash_seconds"] = max(1.0, min(MAX_SECONDS, args.splash_seconds))
        if args.splash is not None or args.splash_seconds is not None or args.set:
            data.setdefault("settings", {}).update(options)
            if any(item.partition("=")[0] == "takeover_all" for item in args.set):
                takeover.reconcile(paths, data, uninstall=data.get("uninstalling", False))
            save_state(paths, data)
        return options
    if name in ("install", "uninstall"):
        from . import install
        return install.install(paths, args.source) if name == "install" else install.uninstall(paths)
    if name in ("update", "rollback"):
        from . import update
        return getattr(update, name)(paths)
    if name == "games":
        discovery = takeover.discover(paths)
        identifiers = set(discovery[0]) | {i for i in recipes.ids(paths) if i.isdigit()} | set(data["games"])
        metadata, available = appinfo.load(paths), steam.proton(paths)
        from . import nonsteam
        registered_ids = {g["appid"] for g in data.get("non_steam", {}).values()}
        return {"games": [game(paths, ident, discovery, metadata, available) for ident in sorted(identifiers - registered_ids, key=int)] +
                [nonsteam.game(paths, saved) for saved in data.get("non_steam", {}).values() if not saved.get("removed")]}
    if name == "game":
        result = game(paths, args.appid)
        result.setdefault("notes", (recipes.load(paths, args.appid) or {}).get("notes", ""))
        warnings = data["games"].get(args.appid, {}).get("content_warnings", [])
        if warnings:
            result["notes"] += "\n" + "\n".join(warnings)
        path = paths.data / "logs" / f"{args.appid}-last.log"
        if path.exists():
            with path.open(encoding="utf-8", errors="replace") as log:
                result["log_tail"] = [line.rstrip("\r\n") for line in deque(log, maxlen=200)]
        else:
            result["log_tail"] = []
        return result
    if name == "game-option":
        saved = data["games"].setdefault(args.appid, {})
        if args.takeover is not None:
            saved["takeover_excluded"] = args.takeover == "off"
            if args.takeover == "off" and saved.get("enabled"):
                saved["pending"] = "disable"
        if args.proton is not None:
            saved["proton_override"] = None if args.proton == "auto" else args.proton
        takeover.reconcile(paths, data, uninstall=data.get("uninstalling", False))
        save_state(paths, data)
        return game(paths, args.appid)
    if name in ("enable", "disable", "mod"):
        from . import nonsteam
        registered = next((g for g in data.get("non_steam", {}).values()
                           if g["appid"] == args.appid and not g.get("removed")), None)
        if registered:
            if name != "mod":
                raise Error("invalid_action", "Use remove-game to remove a non-Steam game")
            recipe = recipes.load(paths, registered["umu_id"]) or {}
            if args.mod_id not in {m["id"] for m in recipe.get("mods", [])}:
                raise Error("unknown_mod", "No such mod in this recipe")
            registered.setdefault("mods", {})[args.mod_id] = args.on
            save_state(paths, data)
            return nonsteam.game(paths, registered)
        saved = data["games"].setdefault(args.appid, {})
        if name == "mod":
            recipe = recipes.load(paths, args.appid)
            if not recipe or args.mod_id not in {m["id"] for m in recipe.get("mods", [])}:
                raise Error("unknown_mod", "No such mod in this recipe")
            saved.setdefault("mods", {})[args.mod_id] = args.on
        else:
            saved["pending"] = name
            saved["takeover_excluded"] = name == "disable"
        changed = set()
        if name in ("enable", "mod"):
            changed = content.immediate(paths, args.appid, recipes.load(paths, args.appid), saved)
        save_state(paths, data)
        result = game(paths, args.appid)
        if name == "mod":
            mod = next(m for m in result["mods"] if m["id"] == args.mod_id)
            result["mod_result"] = {"changed_now": args.mod_id in changed, "message": mod["state_message"]}
        return result
    if name == "apply":
        return steam.apply(paths, args.restart_steam)
    if name == "default":
        data["default_pending"] = "default_on" if args.on else "default_off"
        save_state(paths, data)
    if name in ("status", "default"):
        _, eligible = takeover.discover(paths)
        pending = [{"appid": ident, "action": item["pending"]} for ident, item in data["games"].items() if item.get("pending")]
        pending += [{"slug": g["slug"], "action": g["pending"]} for g in data.get("non_steam", {}).values() if g.get("pending")]
        if data.get("default_pending"):
            pending.append({"action": data["default_pending"]})
        return {"version": __version__, "install_path": str(paths.data), "steam_running": steam.running(),
                "default_tool": steam.mappings(steam.read_vdf(steam.mapping_path(paths))).get("0", {}).get("name") == "DeckCompanion",
                "pending": pending,
                "takeover_all": data.get("settings", {}).get("takeover_all", True),
                "takeover_counts": takeover.counts(data, eligible),
                "proton": steam.proton(paths),
                "last_apply": read_json(paths.data / "logs/apply-last.json")}
    raise Error("unknown_command", name)


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        paths = Paths.env()
        if args.command == "launch":
            from .launch import launch
            launch(paths, args.verb, args.cmd)
            return 0
        if args.command == "run-umu":
            from .nonsteam import run
            run(paths, args.slug)
            return 0
        if args.command == "apply":
            with locked(paths):
                data = state(paths)
                takeover.reconcile(paths, data, uninstall=data.get("uninstalling", False))
                steam.prune_pending(paths, data)
                save_state(paths, data)
                pending = steam.has_pending(data)
            if not pending:
                print(json.dumps({"applied": [], "steam_restarted": False}))
                return 0
        if args.command == "apply-helper":
            from .apply_helper import apply_helper
            result = apply_helper(paths, args.game_mode)
        elif args.command == "apply" and args.restart_steam and sys.platform.startswith("linux") and steam.running():
            # Never hold state.lock while starting a helper which will need it.
            from .apply_helper import schedule
            result = schedule(paths)
        elif args.command in ("proton", "proton-helper", "update"):
            result = execute(args, paths)
        else:
            with locked(paths):
                result = execute(args, paths)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get("errors") else 0
    except Exception as exc:
        print(json.dumps({"error": {"code": getattr(exc, "code", "operation_failed"), "message": str(exc)}}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
