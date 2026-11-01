"""Independent content and upstream metadata; launch reads cached resolutions only."""
import hashlib
import os
import re
import tomllib
from urllib.parse import quote
from . import __version__, net, recipes
from .common import Error, component, locked, now, read_json, write_json

RELEASES_API = "https://api.github.com/repos/rennerdo30/DeckCompanion/releases"
FAILURES = (Error, OSError, ValueError, TypeError, KeyError, StopIteration, AttributeError)


def json_url(paths, url, filename, budget):
    from .update import json_url as fetch
    return fetch(paths, url, filename, budget)


def hashed_text(entry):
    if not isinstance(entry, dict) or not isinstance(entry.get("toml"), str):
        raise Error("invalid_index", "Expected embedded TOML and SHA256")
    if hashlib.sha256(entry["toml"].encode()).hexdigest() != entry.get("sha256"):
        raise Error("checksum_mismatch", "Content TOML checksum mismatch")
    return tomllib.loads(entry["toml"])


def validate(index, check_engine=True):
    from .update import signature_hook
    if not isinstance(index, dict) or type(index.get("schema_version")) is not int or index["schema_version"] != 1:
        raise Error("invalid_index", "Unsupported content schema")
    signature_hook(index)
    component(index.get("content_version"))
    minimum = index.get("min_engine")
    if not isinstance(minimum, str) or not re.fullmatch(r"\d+(?:\.\d+)*", minimum):
        raise Error("invalid_index", "min_engine must be a numeric engine version")
    if not isinstance(index.get("recipes"), dict):
        raise Error("invalid_index", "recipes must be a map")
    for ident, entry in index["recipes"].items():
        recipes.validate(hashed_text(entry), ident)
    if "launchers" in index:
        hashed_text(index["launchers"])
    if check_engine and recipes.version_key(__version__) < recipes.version_key(minimum):
        raise Error("engine_too_old", f"Content requires engine {minimum}; running {__version__}")
    return index


def releases(paths, budget, api=None, prereleases=False):
    """Read all pages within one deadline; never select from a truncated listing."""
    result = []
    api = api or os.environ.get("DECKCOMPANION_CONTENT_API", RELEASES_API)
    for page in range(1, 11):
        budget.remaining()
        batch = json_url(paths, f"{api}?per_page=100&page={page}", "content-releases.json", budget)
        if not isinstance(batch, list) or any(not isinstance(r, dict) for r in batch):
            raise Error("invalid_index", "Expected a release list")
        result.extend(r for r in batch if not r.get("draft") and (prereleases or not r.get("prerelease")))
        if len(batch) < 100:
            return result
    raise Error("invalid_index", "Release listing exceeds bounded pagination")


def refresh(paths):
    budget = net.Budget(2)
    status = {"state": "offline", "updated": False}
    try:
        candidates = [r for r in releases(paths, budget) if str(r.get("tag_name", "")).startswith("content-")]
        if not candidates:
            raise Error("unavailable", "No published content release; keeping cached or bundled content")
        release = max(candidates, key=lambda r: (r.get("published_at", ""), recipes.version_key(r["tag_name"])))
        asset = next(a for a in release["assets"] if a["name"] == "content-index.json")
        candidate = json_url(paths, asset["browser_download_url"], "content-response.json", budget)
        if asset.get("digest") and asset["digest"] != "sha256:" + net.digest(paths.cache / "content-response.json"):
            raise Error("checksum_mismatch", "Content index asset checksum mismatch")
        candidate = validate(candidate)
        if release["tag_name"] != "content-" + candidate["content_version"]:
            raise Error("invalid_index", "Content version differs from release tag")
        candidate = dict(candidate, fetched_at=now())
        with locked(paths):
            previous = read_json(paths.cache / "index.json", {})
            if previous.get("content_version") and recipes.version_key(previous["content_version"]) > recipes.version_key(candidate["content_version"]):
                raise Error("invalid_index", "Refusing an older content version")
            write_json(paths.cache / "index.json", recipes.merge_index(previous, candidate))
        status = {"state": "current", "updated": True}
    except FAILURES as exc:
        status.update(state=getattr(exc, "code", "invalid_index"), message=str(exc))
    write_json(paths.cache / "content-status.json", status)
    return dict(snapshot(paths)["content"], updated=status["updated"])


def release_asset(paths, repo, asset, budget, tag="latest", metadata=None):
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise Error("invalid_recipe", "Invalid release_repo")
    endpoint = "latest" if tag == "latest" else "tags/" + quote(component(tag), safe="")
    key = repo + "/" + endpoint
    metadata = metadata if metadata is not None else {}
    if key not in metadata:
        filename = "release-" + hashlib.sha256(key.encode()).hexdigest() + ".json"
        metadata[key] = json_url(paths, f"https://api.github.com/repos/{repo}/releases/{endpoint}", filename, budget)
    release = metadata[key]
    if release.get("draft") or release.get("prerelease"):
        raise Error("invalid_release", "Expected a stable upstream release")
    version = component(release["tag_name"])
    if tag != "latest" and version != tag:
        raise Error("invalid_release", "Upstream tag differs from requested pin")
    name = asset(version) if callable(asset) else asset
    item = next(a for a in release["assets"] if a["name"] == name)
    url = item["browser_download_url"]
    if not isinstance(url, str) or not url.startswith("https://") or "/releases/latest/" in url:
        raise Error("invalid_release", "Expected an immutable HTTPS asset URL")
    digest = item.get("digest")
    if digest and (not isinstance(digest, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)):
        raise Error("invalid_release", "Invalid upstream SHA256 digest")
    return {"version": version, "url": url, "sha256": digest[7:] if digest else ""}


def mod_key(mod):
    return mod["release_repo"] + "/" + mod["asset"]


def resolve_mods(paths):
    cache = read_json(paths.cache / "mod-resolutions.json", {"resolved": {}, "updated_at": None})
    resolved, warnings, metadata, seen = dict(cache["resolved"]), [], {}, set()
    changed = False
    for ident in sorted(recipes.ids(paths)):
        try:
            recipe = recipes.load(paths, ident, resolve=False)
            for mod in recipe.get("mods", []):
                if mod["version"] != "latest":
                    continue
                key = mod_key(mod)
                if key in seen:  # Same mod in several recipes: resolve once.
                    continue
                seen.add(key)
                try:
                    budget = net.DownloadBudget(60)
                    item = release_asset(paths, mod["release_repo"], mod["asset"], budget, metadata=metadata)
                    warning = None
                    if not item["sha256"]:
                        target = paths.cache / "mod-hash-download"
                        try:
                            net.fetch(item["url"], target, budget, max_bytes=512 * 1024**2)
                            item["sha256"] = net.digest(target)
                            downloads = paths.cache / "downloads"
                            downloads.mkdir(parents=True, exist_ok=True)
                            os.replace(target, downloads / item["sha256"])
                        finally:
                            target.unlink(missing_ok=True)
                        warning = "Upstream asset has no digest; SHA256 calculated from HTTPS download"
                    net.cached(paths, item["url"], item["sha256"], budget)
                    resolved[key] = dict(item, release_repo=mod["release_repo"], asset=mod["asset"], warning=warning)
                    changed = True
                except FAILURES as exc:
                    warnings.append(f"{key}: {exc}")
        except FAILURES as exc:
            warnings.append(f"{ident}: {exc}")
    result = {"resolved": resolved, "updated_at": now() if changed else cache["updated_at"], "warnings": warnings}
    write_json(paths.cache / "mod-resolutions.json", result)
    return result


def cached_mod(paths, mod):
    if mod["version"] != "latest":
        return mod
    item = read_json(paths.cache / "mod-resolutions.json", {}).get("resolved", {}).get(mod_key(mod))
    if item and item.get("release_repo") == mod["release_repo"] and item.get("asset") == mod["asset"]:
        if re.fullmatch(r"[a-f0-9]{64}", item.get("sha256", "")) and str(item.get("url", "")).startswith("https://"):
            component(item.get("version"))
            return dict(mod, **{k: item[k] for k in ("version", "url", "sha256")})
    return dict(mod, sha256="")  # latest is never installable without a cached resolution.


def snapshot(paths):
    from .update import current
    index = read_json(paths.cache / "index.json", {})
    engine = read_json(paths.cache / "engine-index.json", {})
    status = read_json(paths.cache / "content-status.json", {})
    mods = read_json(paths.cache / "mod-resolutions.json", {})
    from . import umu as runtime
    umu = read_json(paths.cache / "umu-current.json") or runtime.legacy(paths) or {}
    content = {"version": index.get("content_version"), "fetched_at": index.get("fetched_at") if index.get("content_version") else None,
               "state": status.get("state", "cached" if index.get("content_version") else "bundled")}
    if status.get("message"):
        content["message"] = status["message"]
    return {"engine": {"current": current(paths), "available": engine.get("engine", {}).get("version", current(paths))},
            "content": content, "mods": {"resolved": mods.get("resolved", {}), "updated_at": mods.get("updated_at")},
            "umu": {"version": umu.get("version")}}
