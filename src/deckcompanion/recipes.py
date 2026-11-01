"""Version-one declarative recipes; downloaded indexes use the same validation."""
from pathlib import Path
import re
import tomllib
from urllib.parse import urlsplit
from .common import Error, app_id, component, inside


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
    appid = app_id(recipe["appid"])
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


def load(paths, appid):
    appid = app_id(appid)
    cached = paths.cache / "recipes" / f"{appid}.toml"
    bundled = paths.recipes / f"{appid}.toml"
    source = cached if cached.exists() else bundled
    if not source.exists():
        return None
    try:
        return validate(tomllib.loads(source.read_text(encoding="utf-8")), appid)
    except (ValueError, TypeError, KeyError) as exc:
        raise Error("invalid_recipe", f"Invalid recipe {appid}: {exc}") from exc


def ids(paths):
    return {p.stem for root in (paths.recipes, paths.cache / "recipes") for p in root.glob("*.toml") if p.stem.isdigit()}
