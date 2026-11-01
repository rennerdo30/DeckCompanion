"""The JSON interface shared by the desktop UI and command-line users."""
import argparse
from collections import deque
import json
import sys
from . import __version__, recipes, steam
from .common import Error, Paths, app_id, locked, state, save_state


def game(paths, ident):
    ident = app_id(ident)
    entry = steam.installed(paths).get(ident)
    recipe = recipes.load(paths, ident)
    saved = state(paths)["games"].get(ident, {})
    mods = []
    for mod in (recipe or {}).get("mods", []):
        mods.append({"id": mod["id"], "name": mod["name"], "version": mod["version"],
                     "enabled": saved.get("mods", {}).get(mod["id"], mod.get("enabled", True)),
                     "installed_version": saved.get("installed_mods", {}).get(mod["id"])})
    return {"appid": ident, "name": (entry or recipe or {}).get("name", ident), "installed": entry is not None,
            "has_recipe": recipe is not None, "enabled": saved.get("enabled", False),
            "pending": saved.get("pending"), "recipe_version": recipe["version"] if recipe else None,
            "mods": mods, "last_launch": saved.get("last_launch")}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Error("invalid_arguments", message)


def parser():
    root = Parser(prog="deckcompanion")
    root.add_argument("--json", action="store_true")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("games", "game", "enable", "disable", "mod", "apply", "status"):
        sub = commands.add_parser(name)
        sub.add_argument("--json", action="store_true")
        if name in ("game", "enable", "disable", "mod"):
            sub.add_argument("appid", type=app_id)
        if name == "mod":
            sub.add_argument("mod_id")
            group = sub.add_mutually_exclusive_group(required=True)
            group.add_argument("--on", action="store_true")
            group.add_argument("--off", action="store_true")
        if name == "apply":
            sub.add_argument("--restart-steam", action="store_true")
    return root


def execute(args, paths):
    name = args.command
    data = state(paths)
    if name == "games":
        identifiers = set(steam.installed(paths)) | recipes.ids(paths) | set(data["games"])
        return {"games": [game(paths, ident) for ident in sorted(identifiers, key=int)]}
    if name == "game":
        result = game(paths, args.appid)
        result["notes"] = (recipes.load(paths, args.appid) or {}).get("notes", "")
        path = paths.data / "logs" / f"{args.appid}-last.log"
        if path.exists():
            with path.open(encoding="utf-8", errors="replace") as log:
                result["log_tail"] = "".join(deque(log, maxlen=200))
        else:
            result["log_tail"] = ""
        return result
    if name in ("enable", "disable", "mod"):
        saved = data["games"].setdefault(args.appid, {})
        if name == "mod":
            recipe = recipes.load(paths, args.appid)
            if not recipe or args.mod_id not in {m["id"] for m in recipe.get("mods", [])}:
                raise Error("unknown_mod", "No such mod in this recipe")
            saved.setdefault("mods", {})[args.mod_id] = args.on
        else:
            saved["pending"] = name
        save_state(paths, data)
        return game(paths, args.appid)
    if name == "apply":
        return steam.apply(paths, args.restart_steam)
    if name == "status":
        return {"version": __version__, "steam_running": steam.running(),
                "pending": [{"appid": ident, "action": item["pending"]} for ident, item in data["games"].items() if item.get("pending")],
                "proton": steam.proton(paths)}
    raise Error("unknown_command", name)


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        paths = Paths.env()
        with locked(paths):
            result = execute(args, paths)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (Error, OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"error": {"code": getattr(exc, "code", "operation_failed"), "message": str(exc)}}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
