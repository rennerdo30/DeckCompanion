"""Manifest-owned content and per-prefix initial settings; no arbitrary scripts."""
import configparser
import hashlib
import io
import logging
from pathlib import Path
import re
import stat
import tomllib
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
import zipfile
from . import net, steam
from .common import Error, atomic, inside, read_json, write_json


def validate_launchers(catalog):
    def require(ok, message):
        if not ok:
            raise Error("invalid_launcher_catalog", message)
    require(type(catalog.get("schema_version")) is int and catalog["schema_version"] == 1,
            "Unsupported launcher catalog schema")
    require(isinstance(catalog.get("version"), str) and bool(catalog["version"]), "Catalog version is required")
    rows = catalog.get("launchers")
    require(isinstance(rows, list) and 0 < len(rows) <= 64, "Expected 1 to 64 launcher presets")
    seen, result = set(), []
    for row in rows:
        require(isinstance(row, dict), "Launcher preset must be a table")
        require(row.keys() <= {"id", "name", "store", "gameid", "source_url", "installer_url", "installed_exe",
                               "installer_type", "installer_args", "unattended", "checksum_sha256"},
                "Unknown launcher preset field")
        for key in ("id", "name", "store", "gameid", "source_url", "installer_url", "installed_exe", "installer_type"):
            require(isinstance(row.get(key), str) and 0 < len(row[key]) <= 4096 and
                    not any(ord(c) < 32 for c in row[key]), f"Invalid launcher {key}")
        require(bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", row["id"])) and row["id"] not in seen,
                "Invalid or duplicate launcher id")
        seen.add(row["id"])
        require(len(row["name"]) <= 128 and bool(re.fullmatch(r"[a-z0-9_-]{1,32}", row["store"])),
                "Invalid launcher name or store")
        require(bool(re.fullmatch(r"umu-[A-Za-z0-9][A-Za-z0-9_-]{0,100}", row["gameid"])), "Invalid launcher GAMEID")
        for key in ("source_url", "installer_url"):
            url = urlsplit(row[key])
            require(url.scheme == "https" and bool(url.hostname) and not url.username and not url.password,
                    "Launcher URLs must use HTTPS without credentials")
        require(row["installer_type"] in ("exe", "msi"), "Unsupported installer type")
        require(type(row.get("unattended")) is bool, "unattended must be boolean")
        args = row.get("installer_args")
        require(isinstance(args, list) and len(args) <= 64 and all(isinstance(a, str) and len(a) <= 4096 and
                not any(ord(c) < 32 for c in a) for a in args), "Installer arguments must be strings")
        inside(Path.cwd(), row["installed_exe"])
        require(row["installed_exe"].startswith("pfx/drive_c/") and row["installed_exe"].lower().endswith(".exe"),
                "Installed launcher must be an exe inside pfx/drive_c")
        sha = row.get("checksum_sha256")
        require(sha is None or isinstance(sha, str) and bool(re.fullmatch(r"[a-f0-9]{64}", sha)), "Invalid installer SHA256")
        result.append(dict(row, checksum_sha256=sha))
    return result


def launchers(paths):
    """Read DC-36's verified cache entry, with a validated bundled fallback."""
    log = logging.getLogger(__name__)
    try:
        cached = read_json(paths.cache / "index.json", {}).get("launchers")
        if cached is not None:
            text = cached["toml"]
            if not isinstance(text, str) or len(text.encode()) > 256 * 1024:
                raise ValueError("Invalid launcher TOML size")
            if hashlib.sha256(text.encode()).hexdigest() != cached["sha256"]:
                raise ValueError("Launcher catalog checksum mismatch")
            result = validate_launchers(tomllib.loads(text))
            log.info("Launcher catalog source: cached index launchers")
            return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError, Error):
        log.warning("Cached launcher catalog rejected; using bundled content/launchers.toml")
    source = Path(__file__).resolve().parents[2] / "content/launchers.toml"
    try:
        if source.stat().st_size > 256 * 1024:
            raise ValueError("Launcher catalog exceeds 256 KiB")
        result = validate_launchers(tomllib.loads(source.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, Error) as exc:
        raise Error("invalid_launcher_catalog", "Bundled launcher catalog is missing or invalid") from exc
    log.info("Launcher catalog source: bundled content/launchers.toml")
    return result


def root_for(item, prefix, game):
    root = prefix if item.get("root", "prefix") == "prefix" else game
    if root is None or not root.is_dir():
        raise Error("content_deferred", "Content root does not exist yet")
    if item.get("root", "prefix") == "prefix" and not (root / "pfx").is_dir():
        raise Error("content_deferred", "Prefix is not initialized; applying content next launch")
    return root


def reconcile(paths, appid, recipe, prefix, game, preferences, budget, log, statuses=None, changes=None):
    manifest_path = paths.data / "manifests" / f"{appid}.json"
    manifest = read_json(manifest_path, {"mods": {}})
    wanted = {m["id"]: m for m in recipe.get("mods", []) if preferences.get(m["id"], m.get("enabled", True))}
    installed = {}
    statuses = statuses if statuses is not None else {}
    # A recipe can change roots or targets: old absolute paths are retained only as ownership records.
    for ident in set(manifest["mods"]) | set(wanted):
        previous = manifest["mods"].get(ident)
        mod = wanted.get(ident)
        try:
            same_root = bool(mod and previous and previous.get("root_path") == str(root_for(mod, prefix, game)))
            if mod and previous and same_root and previous.get("version") == mod["version"] and previous.get("sha256") == mod["sha256"] and previous.get("target") == mod["target"]:
                if record_valid(previous, prefix, game):
                    installed[ident] = previous["version"]
                    continue
            files = {}
            if mod:
                root = root_for(mod, prefix, game)
                destination = inside(root, mod["target"])
                source = net.cached(paths, mod["url"], mod["sha256"], budget)
                if mod["mode"] == "copy":
                    files[destination] = source.read_bytes()
                else:
                    with zipfile.ZipFile(source) as archive:
                        if sum(e.file_size for e in archive.infolist()) > 512 * 1024 * 1024:
                            raise Error("unsafe_archive", "Mod expands beyond size limit")
                        for entry in archive.infolist():
                            if entry.is_dir():
                                continue
                            if stat.S_ISLNK(entry.external_attr >> 16):
                                raise Error("unsafe_archive", "Mod archive contains a symlink")
                            target = inside(destination, entry.filename)
                            if target in files:
                                raise Error("unsafe_archive", "Duplicate archive target")
                            files[target] = archive.read(entry)
            old = {Path(f["path"]): f for f in previous["files"]} if previous else {}
            allowed = [r.resolve() for r in (prefix, game) if r is not None]
            for path in set(old) | set(files):
                if not any(path.resolve().is_relative_to(r) for r in allowed):
                    raise Error("unsafe_manifest", "Manifest file escaped its root")
                if not path.exists() or (path in old and net.digest(path) == old[path]["sha256"]):
                    continue
                # A byte-identical file the player installed by hand is adopted, not a conflict.
                if path not in old and path in files and net.digest(path) == hashlib.sha256(files[path]).hexdigest():
                    continue
                raise Error("content_conflict", "Preserving an existing or user-modified mod file")
            originals = {p: p.read_bytes() if p.exists() else None for p in set(old) | set(files)}
            snapshot = dict(manifest["mods"])
            try:
                for path in old.keys() - files.keys():
                    path.unlink(missing_ok=True)
                for path, payload in files.items():
                    atomic(path, payload)
                manifest["mods"].pop(ident, None)
                if mod:
                    manifest["mods"][ident] = {"version": mod["version"], "sha256": mod["sha256"], "target": mod["target"], "root_path": str(root),
                        "files": [{"path": str(p), "sha256": hashlib.sha256(b).hexdigest()} for p, b in files.items()]}
                    installed[ident] = mod["version"]
                write_json(manifest_path, manifest)
                if changes is not None:
                    changes.add(ident)
            except Exception:
                manifest["mods"] = snapshot
                for path, payload in originals.items():
                    if payload is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic(path, payload)
                raise
            if mod:
                from .updates import record
                record(paths, "mods", mod.get("name", ident), previous.get("version") if previous else None, mod["version"])
        except (Error, OSError, ValueError, zipfile.BadZipFile) as exc:
            statuses[ident] = {"code": getattr(exc, "code", "content_error"),
                               "message": failure_message(exc)}
            log(f"Mod {ident} ({mod['target'] if mod else previous.get('target', 'manifest files')}): {exc}")
            if previous:
                installed[ident] = previous["version"]
    return installed


def failure_message(exc):
    return {"content_deferred": "Will install on next launch after the prefix is ready",
            "unresolved_asset": "Mod download is not ready; run Update first",
            "content_conflict": "Existing mod files were changed; keeping your files",
            "unsafe_path": "The mod folder points outside the game prefix",
            "unsafe_manifest": "The mod folder points outside the game prefix",
            "network_timeout": "Download timed out; try again or launch later",
            "download_failed": "Download unavailable; try again or launch later"}.get(
                getattr(exc, "code", ""), "Could not apply the mod; see game notes")


def record_valid(record, prefix, game):
    allowed = [r.resolve() for r in (prefix, game) if r is not None]
    return bool(record.get("files")) and all(
        any(Path(f["path"]).resolve().is_relative_to(r) for r in allowed) and
        Path(f["path"]).is_file() and net.digest(Path(f["path"])) == f["sha256"]
        for f in record["files"])


def mod_state(mod, enabled, record, error, prefix, game):
    if not enabled and not record:
        return "off", "Off"
    if error and error.get("code") not in ("content_deferred", "game_running", "network_timeout", "download_failed"):
        return "blocked", error["message"]
    if not enabled:
        return "off", "Off; will remove on next launch" if error else "Off"
    valid = record and record_valid(record, prefix, game)
    if valid and record.get("version") == mod["version"] and record.get("sha256") == mod["sha256"] and record.get("target") == mod["target"]:
        return "installed", f"Installed {mod['version']}"
    if valid:
        return "update_available", "Update available" + (f"; {error['message']}" if error else "")
    if error:
        return ("will_install_on_launch" if error["code"] in ("content_deferred", "game_running") else "blocked"), error["message"]
    return "will_install_on_launch", "Will install on next launch"


def immediate(paths, appid, recipe, saved, budget=None):
    """Called under state.lock; query commands never reconcile or download."""
    if not recipe:
        return set()
    changes = set()
    statuses = {}
    warnings = []
    try:
        prefix, game = steam.content_roots(paths, appid)
        if prefix is None:
            raise Error("content_deferred", "Will install on next launch after the prefix is ready")
        if steam.game_running(appid):
            raise Error("game_running", "Close the game to apply mods; otherwise they apply next launch")
        installed = reconcile(paths, appid, recipe, prefix, game, saved.get("mods", {}),
                              budget or net.Budget(), warnings.append, statuses, changes)
        saved["installed_mods"] = installed
    except (Error, OSError, ValueError) as exc:
        message = str(exc) if getattr(exc, "code", "") in ("content_deferred", "game_running", "game_state_unknown") else failure_message(exc)
        statuses = {m["id"]: {"code": getattr(exc, "code", "content_error"), "message": message}
                    for m in recipe.get("mods", [])}
        warnings.append(message)
    saved["mod_status"] = statuses
    saved["content_warnings"] = warnings
    return changes


def settings(recipe, prefix, game, log):
    for item in recipe.get("settings", []):
        try:
            root = root_for(item, prefix, game)
            # Stamp is deliberately inside pfx: deleting/recreating a prefix resets it.
            if prefix is None or not (prefix / "pfx").is_dir():
                raise Error("content_deferred", "Settings wait for prefix creation")
            stamp = prefix / "pfx/.deckcompanion/settings" / item["id"]
            if stamp.exists():
                continue
            target = inside(root, item["target"])
            if item["format"] == "ini":
                config = configparser.ConfigParser(interpolation=None)
                config.optionxform = str
                if target.exists():
                    config.read_string(target.read_text(encoding="utf-8"))
                section = item["section"]
                if not config.has_section(section):
                    config.add_section(section)
                config.set(section, item["key"], item["value"])
                stream = io.StringIO()
                config.write(stream)
                payload = stream.getvalue().encode()
            else:
                if not target.exists():
                    raise Error("content_deferred", "XML file is not present yet")
                tree = ET.fromstring(target.read_bytes())
                element = tree.find(item["xpath"])
                if element is None:
                    raise Error("setting_missing", "XML selector did not match")
                element.text = item["value"]
                payload = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
            if target.exists():
                atomic(target.with_name(target.name + ".deckcompanion-original"), target.read_bytes())
            atomic(target, payload)
            atomic(stamp, "applied\n")
        except (Error, OSError, ValueError, configparser.Error, ET.ParseError) as exc:
            log(f"Setting {item['id']} ({item['target']}): {exc}")
