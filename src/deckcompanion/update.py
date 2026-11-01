"""GitHub release updates with SHA256, immutable version directories and rollback."""
import json
import os
import re
from pathlib import Path
import shutil
import tempfile
import tomllib
from . import __version__, archives, net, recipes
from .common import Error, atomic, component, now, read_json, write_json

RELEASE_API = "https://api.github.com/repos/rennerdo30/DeckCompanion/releases"


def engine_release(paths):
    from . import channels
    preview = os.environ.get("DECKCOMPANION_ENGINE_PRERELEASES") == "1"
    releases = channels.releases(paths, net.Budget(2), os.environ.get("DECKCOMPANION_RELEASE_API", RELEASE_API), preview)
    candidates = [r for r in releases if re.fullmatch(r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", r.get("tag_name", ""))
                  and (preview or "-" not in r["tag_name"])]
    if not candidates:
        raise Error("unavailable", "No published engine v<semver> release")
    return max(candidates, key=lambda r: r.get("published_at", ""))


def current(paths):
    path = paths.data / "current"
    return component(path.read_text(encoding="utf-8").strip()) if path.exists() else __version__


def signature_hook(index):
    """TODO(signing): verify a detached Ed25519 signature against pinned public keys.

    SHA256 currently detects corruption, not a compromised index or GitHub account.
    A future schema must bind the canonical index bytes and enforce key rotation.
    """
    if index.get("signature"):
        raise Error("unsupported_signature", "Signed indexes require a future verifier; refusing an unverifiable signature")


def validate_index(index):
    if not isinstance(index, dict) or index.get("schema_version") != 1:
        raise Error("invalid_index", "Unsupported index schema")
    signature_hook(index)
    engine = index.get("engine", {})
    component(engine.get("version"))
    if not isinstance(engine.get("url"), str) or not engine["url"].startswith("https://"):
        raise Error("invalid_index", "Engine release URL must be HTTPS")
    sha = engine.get("sha256", "")
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise Error("invalid_index", "Engine SHA256 is required")
    entries = index.get("recipes", {})
    if not isinstance(entries, dict):
        raise Error("invalid_index", "recipes must be a map")
    import hashlib
    for ident, item in entries.items():
        text = item["toml"]
        if hashlib.sha256(text.encode()).hexdigest() != item["sha256"]:
            raise Error("checksum_mismatch", f"Recipe checksum mismatch: {ident}")
        recipes.validate(tomllib.loads(text), ident)
    return index


def switch(paths, version):
    version = component(version)
    root = paths.data / "versions" / version
    if not (root / "src/deckcompanion/__main__.py").is_file():
        raise Error("invalid_engine", "Cannot select an incomplete engine")
    old = current(paths)
    if old != version:
        if (paths.data / "versions" / old).is_dir():
            atomic(paths.data / "previous", old + "\n")
    atomic(paths.data / "current", version + "\n")


def install_engine(paths, source, version, sha256):
    version = component(version)
    old = current(paths)
    if net.digest(source) != sha256:
        raise Error("checksum_mismatch", "Engine archive checksum mismatch")
    versions = paths.data / "versions"
    target = versions / version
    if target.exists():
        receipt = read_json(target / ".release.json", {})
        if receipt.get("sha256") != sha256:
            raise Error("version_conflict", "Existing engine version differs; preserving it")
        switch(paths, version)
        from .updates import record
        record(paths, "engine", "DeckCompanion", old, version)
        return
    versions.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".install-", dir=versions))
    try:
        archives.unpack(source, staging)
        for name in ("src/deckcompanion/__main__.py", "vdf/__init__.py", "compat/DeckCompanion"):
            if not (staging / name).is_file():
                raise Error("invalid_engine", f"Release lacks {name}")
        hashes = {p.relative_to(staging).as_posix(): net.digest(p) for p in staging.rglob("*") if p.is_file() and not p.is_symlink()}
        write_json(staging / ".release.json", {"version": version, "sha256": sha256, "files": hashes})
        os.replace(staging, target)
        switch(paths, version)
        from .updates import record
        record(paths, "engine", "DeckCompanion", old, version)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def rollback(paths):
    previous = paths.data / "previous"
    if not previous.exists():
        raise Error("no_rollback", "No previous engine is installed")
    version = component(previous.read_text(encoding="utf-8").strip())
    switch(paths, version)
    return {"version": current(paths), "rolled_back": True}


def json_url(paths, url, filename, budget=None):
    path = paths.cache / filename
    net.fetch(url, path, budget, max_bytes=8 * 1024 * 1024)
    return json.loads(path.read_text(encoding="utf-8"))


def update(paths):
    from . import channels
    from .common import locked, state, save_state
    cached = read_json(paths.cache / "engine-index.json")
    if cached is None:
        legacy = read_json(paths.cache / "index.json", {})
        cached = legacy if "engine" in legacy else None
    index, fetched = cached, False
    try:
        url = os.environ.get("DECKCOMPANION_INDEX_URL")
        if not url:
            release = engine_release(paths)
            url = next(a["browser_download_url"] for a in release["assets"] if a["name"] == "index.json")
        candidate = validate_index(json_url(paths, url, "index-response.json"))
        candidate["fetched_at"] = now()
        write_json(paths.cache / "engine-index.json", candidate)
        with locked(paths):
            active = read_json(paths.cache / "index.json", {})
            if not active.get("content_version") and candidate.get("recipes"):
                write_json(paths.cache / "index.json", recipes.merge_index(active, candidate))
        index, fetched = candidate, True
    except channels.FAILURES:
        # Offline / invalid new index never replaces the last validated cache.
        if cached:
            try:
                validate_index(cached)
            except channels.FAILURES:
                index = None
    old = current(paths)
    had_pointer = (paths.data / "current").exists()
    available, updated, engine_error = old, False, None
    if index:
        engine = index["engine"]
        available = engine["version"]
        if available != old or not (paths.data / "current").exists():
            try:
                source = net.cached(paths, engine["url"], engine["sha256"])
                install_engine(paths, source, available, engine["sha256"])
                updated = current(paths) != old or not had_pointer
            except (Error, OSError) as exc:
                engine_error = str(exc)  # Other channels still refresh after an engine failure.
    content_status = channels.refresh(paths)
    mod_status = channels.resolve_mods(paths)
    from . import proton, umu
    # Decky is explicitly user-updated; background work only refreshes availability.
    from . import decky
    decky.latest(paths)
    decky.plugins_status(paths)
    try:
        runtime = umu.install(paths)
    except Exception as exc:
        runtime = {"installed": False, "error": str(exc)}
    from . import takeover, steam
    with locked(paths):
        data = state(paths)
        takeover.reconcile(paths, data, uninstall=data.get("uninstalling", False))
        steam.prune_pending(paths, data)
        save_state(paths, data)
        pending = steam.has_pending(data)
    engine_status = {"current": current(paths), "available": available, "updated": updated}
    if engine_error:
        engine_status["error"] = engine_error
    return {"steam_changes_pending": pending,
            "engine": engine_status,
            "index": {"updated": fetched, "fetched_at": index.get("fetched_at") if index else None},
            "content": content_status, "mods": mod_status, "channels": channels.snapshot(paths),
            "proton": proton.sync(paths), "umu": runtime}
