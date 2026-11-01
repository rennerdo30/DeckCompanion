"""Manifest-owned content and per-prefix initial settings; no arbitrary scripts."""
import configparser
import hashlib
import io
from pathlib import Path
import stat
import xml.etree.ElementTree as ET
import zipfile
from . import net
from .common import Error, atomic, inside, read_json, write_json


def root_for(item, prefix, game):
    root = prefix if item.get("root", "prefix") == "prefix" else game
    if root is None or not root.is_dir():
        raise Error("content_deferred", "Content root does not exist yet")
    if item.get("root", "prefix") == "prefix" and not (root / "pfx").is_dir():
        raise Error("content_deferred", "Prefix is not initialized; applying content next launch")
    return root


def reconcile(paths, appid, recipe, prefix, game, preferences, budget, log):
    manifest_path = paths.data / "manifests" / f"{appid}.json"
    manifest = read_json(manifest_path, {"mods": {}})
    wanted = {m["id"]: m for m in recipe.get("mods", []) if preferences.get(m["id"], m.get("enabled", True))}
    installed = {}
    # A recipe can change roots or targets: old absolute paths are retained only as ownership records.
    for ident in set(manifest["mods"]) | set(wanted):
        previous = manifest["mods"].get(ident)
        mod = wanted.get(ident)
        try:
            same_root = bool(mod and previous and previous.get("root_path") == str(root_for(mod, prefix, game)))
            if mod and previous and same_root and previous.get("sha256") == mod["sha256"] and previous.get("target") == mod["target"]:
                if all(Path(f["path"]).is_file() for f in previous["files"]):
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
                if path.exists() and (path not in old or net.digest(path) != old[path]["sha256"]):
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
            except Exception:
                manifest["mods"] = snapshot
                for path, payload in originals.items():
                    if payload is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic(path, payload)
                raise
        except (Error, OSError, ValueError, zipfile.BadZipFile) as exc:
            log(f"Mod {ident} ({mod['target'] if mod else previous.get('target', 'manifest files')}): {exc}")
            if previous:
                installed[ident] = previous["version"]
    return installed


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
