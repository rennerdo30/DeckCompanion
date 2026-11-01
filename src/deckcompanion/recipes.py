"""Version-one declarative recipes; downloaded indexes use the same validation."""
from pathlib import Path
import hashlib
import re
import tomllib
from urllib.parse import urlsplit
from . import __version__
from .common import Error, app_id, component, inside, read_json, write_json


def recipe_id(value):
    if isinstance(value, str) and value.startswith("umu-"):
        from .umu import game_id
        return game_id(value)
    return app_id(value)


def validate(recipe, expected=None):
    def require(ok, message):
        if not ok:
            raise Error("invalid_recipe", message)
    def strings(value):
        return isinstance(value, list) and all(isinstance(x, str) for x in value)
    def table(value):
        return isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())
    require(isinstance(recipe, dict), "Recipe must be a table")
    require(recipe.get("schema_version") == 1, "Unsupported recipe schema_version")
    require(isinstance(recipe.get("appid"), str), "appid must be a string")
    appid = recipe_id(recipe["appid"])
    require(expected is None or appid == expected, "Recipe appid differs from filename")
    component(recipe.get("version"))
    require(isinstance(recipe.get("name"), str), "Recipe name is required")
    runtime = recipe.get("runtime", {})
    require(isinstance(runtime, dict), "runtime must be a table")
    require(isinstance(runtime.get("proton", "GE-Proton"), str), "runtime.proton must be a string")
    require(table(runtime.get("env", {})), "runtime.env must contain strings")
    require(strings(runtime.get("launch_options", [])), "launch_options must be a string array")
    wine = recipe.get("wine", {})
    require(isinstance(wine, dict), "wine must be a table")
    require(strings(wine.get("winetricks", [])), "wine.winetricks must be a string array")
    require(table(wine.get("env", {})), "wine.env must contain strings")
    require(table(wine.get("dll_overrides", {})), "dll_overrides must contain strings")
    require(all(v in ("", "n", "b", "n,b", "b,n") for v in wine.get("dll_overrides", {}).values()), "Invalid DLL override order")
    require(isinstance(wine.get("registry", []), list), "registry must be an array")
    for entry in wine.get("registry", []):
        require(isinstance(entry, dict) and all(isinstance(entry.get(k), str) for k in ("key", "name", "type", "value")), "Invalid registry entry")
    for collection in ("mods", "settings"):
        require(isinstance(recipe.get(collection, []), list), f"{collection} must be an array")
        seen = set()
        for item in recipe.get(collection, []):
            require(isinstance(item, dict), f"Invalid {collection} entry")
            ident = component(item.get("id"))
            require(ident not in seen, f"Duplicate {collection} id")
            seen.add(ident)
            require(item.get("root", "prefix") in ("prefix", "game"), "root must be prefix or game")
            inside(Path.cwd(), item.get("target"))
            if collection == "mods":
                require(all(isinstance(item.get(k), str) for k in ("name", "version", "url", "sha256")), "Mod metadata must be strings")
                require(item.get("mode") in ("copy", "extract_zip"), "Invalid mod mode")
                require(type(item.get("enabled", True)) is bool, "enabled must be boolean")
                require(urlsplit(item["url"]).scheme == "https", "Mod URL must use HTTPS")
                # Empty checksum is an unresolved build-time source, never installable.
                require(item["sha256"] == "" or bool(re.fullmatch(r"[a-f0-9]{64}", item["sha256"])), "Invalid SHA256")
            else:
                require(item.get("format") in ("ini", "xml"), "Invalid settings format")
                require(isinstance(item.get("value"), str), "Setting value must be a string")
                keys = ("section", "key") if item["format"] == "ini" else ("xpath",)
                require(all(isinstance(item.get(k), str) for k in keys), "Missing settings selector")
    if "sha256" in runtime:
        require(isinstance(runtime["sha256"], str) and bool(re.fullmatch(r"[a-f0-9]{64}", runtime["sha256"])), "Invalid runtime SHA256")
    return recipe


def version_key(value):
    # Numeric components compare numerically; missing provenance predates releases.
    parts = tuple(int(n) for n in re.findall(r"\d+", str(value)))
    return parts + (0,) * max(0, 8 - len(parts))


def candidates(paths, appid):
    appid = recipe_id(appid)
    index = read_json(paths.cache / "index.json", {})
    indexed = index.get("recipes", {}).get(appid)
    result = []
    if indexed:
        try:
            recipe = validate(tomllib.loads(indexed["toml"]), appid)
            result.append((recipe, indexed.get("engine_version", index.get("engine", {}).get("version", "0")), indexed["toml"]))
        except (ValueError, TypeError, KeyError) as exc:
            raise Error("invalid_recipe", f"Invalid cached recipe {appid}") from exc
    for source, engine in ((paths.cache / "recipes" / f"{appid}.toml", "0"),
                           (paths.recipes / f"{appid}.toml", __version__)):
        if source.is_file():
            try:
                text = source.read_text(encoding="utf-8")
                recipe = validate(tomllib.loads(text), appid)
                result.append((recipe, recipe.get("engine_version", engine), text))
            except (ValueError, TypeError, KeyError) as exc:
                raise Error("invalid_recipe", f"Invalid recipe {appid}: {exc}") from exc
    return result


def rank(candidate):
    return version_key(candidate[0]["version"]), version_key(candidate[1])


def load(paths, appid):
    choices = candidates(paths, appid)
    return max(choices, key=rank)[0] if choices else None


def merge_index(previous, incoming):
    """Activating an engine index must not downgrade any already cached recipe."""
    merged = dict(incoming, recipes={})
    for index in (previous or {}, incoming):
        for ident, item in index.get("recipes", {}).items():
            item = dict(item, engine_version=item.get("engine_version", index.get("engine", {}).get("version", "0")))
            old = merged["recipes"].get(ident)
            def key(entry):
                return rank((validate(tomllib.loads(entry["toml"]), ident), entry["engine_version"]))
            if not old or key(item) > key(old):
                merged["recipes"][ident] = item
    return merged


def refresh_cache(paths):
    """Refresh older entries without changing the cached engine release metadata."""
    index = read_json(paths.cache / "index.json", {})
    changed = False
    for ident in ids(paths):
        choices = candidates(paths, ident)
        if not choices:
            continue
        best = max(choices, key=rank)
        current = index.get("recipes", {}).get(ident)
        if current and (current["toml"] != best[2] or
                        version_key(current.get("engine_version", index.get("engine", {}).get("version", "0"))) < version_key(best[1])):
            index["recipes"][ident] = {"toml": best[2], "engine_version": best[1],
                                      "sha256": hashlib.sha256(best[2].encode()).hexdigest()}
            changed = True
        cached = paths.cache / "recipes" / f"{ident}.toml"
        if cached.is_file() and tomllib.loads(cached.read_text(encoding="utf-8")) != best[0]:
            # Discard only this stale cache entry; the selected source remains available.
            if changed:
                write_json(paths.cache / "index.json", index)
            cached.unlink()
    if changed:
        write_json(paths.cache / "index.json", index)


def ids(paths):
    bundled = {p.stem for root in (paths.recipes, paths.cache / "recipes") for p in root.glob("*.toml")
               if p.stem.isdigit() or p.stem.startswith("umu-")}
    return bundled | set(read_json(paths.cache / "index.json", {}).get("recipes", {}))
