"""GitHub release updates with SHA256, immutable version directories and rollback."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import tomllib
from . import __version__, archives, net, recipes
from .common import Error, atomic, component, now, read_json, write_json

RELEASE_API = "https://api.github.com/repos/rennerdo30/DeckCompanion/releases/latest"


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
    if net.digest(source) != sha256:
        raise Error("checksum_mismatch", "Engine archive checksum mismatch")
    versions = paths.data / "versions"
    target = versions / version
    if target.exists():
        receipt = read_json(target / ".release.json", {})
        if receipt.get("sha256") != sha256:
            raise Error("version_conflict", "Existing engine version differs; preserving it")
        switch(paths, version)
        return
    versions.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".install-", dir=versions))
    try:
        archives.unpack(source, staging)
        for name in ("src/deckcompanion/__main__.py", "vdf/__init__.py", "compat/DeckCompanion"):
            if not (staging / name).is_file():
                raise Error("invalid_engine", f"Release lacks {name}")
        write_json(staging / ".release.json", {"version": version, "sha256": sha256})
        os.replace(staging, target)
        switch(paths, version)
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
    cached = read_json(paths.cache / "index.json")
    index, fetched = cached, False
    try:
        url = os.environ.get("DECKCOMPANION_INDEX_URL")
        if not url:
            release = json_url(paths, os.environ.get("DECKCOMPANION_RELEASE_API", RELEASE_API), "release-response.json")
            url = next(a["browser_download_url"] for a in release["assets"] if a["name"] == "index.json")
        candidate = validate_index(json_url(paths, url, "index-response.json"))
        candidate["fetched_at"] = now()
        write_json(paths.cache / "index.json", candidate)  # Atomic activation of all recipes.
        index, fetched = candidate, True
    except (Error, OSError, ValueError, TypeError, KeyError, StopIteration):
        # Offline / invalid new index never replaces the last validated cache.
        if cached:
            validate_index(cached)
    old = current(paths)
    had_pointer = (paths.data / "current").exists()
    available, updated = old, False
    if index:
        engine = index["engine"]
        available = engine["version"]
        if available != old or not (paths.data / "current").exists():
            try:
                source = net.cached(paths, engine["url"], engine["sha256"])
                install_engine(paths, source, available, engine["sha256"])
                updated = current(paths) != old or not had_pointer
            except Error as exc:
                if exc.code not in ("network_timeout", "download_failed"):
                    raise
    return {"engine": {"current": current(paths), "available": available, "updated": updated},
            "index": {"updated": fetched, "fetched_at": index.get("fetched_at") if index else None}}
