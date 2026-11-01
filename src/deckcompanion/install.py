"""No-root install and conservative uninstall, preserving user changes and backups."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from dataclasses import replace
from . import __version__, content, localfix, net, recipes, steam, update
from .common import Error, atomic, component, now, read_json, save_state, state, write_json

PAYLOAD = ("src", "vdf", "recipes", "compat", "docs", "bin", "assets/logo", "assets/steam", "LICENSE", "THIRD_PARTY_NOTICES.md",
           "compatibilitytool.vdf", "toolmanifest.vdf", "install.sh", "uninstall.sh", "DeckCompanion-Installer.desktop")


def files(source):
    result = {}
    for name in PAYLOAD:
        path = source / name
        candidates = path.rglob("*") if path.is_dir() else [path]
        for candidate in candidates:
            if candidate.is_symlink():
                raise Error("unsafe_source", "Installer source contains a symlink")
            if candidate.is_file() and "__pycache__" not in candidate.parts and candidate.suffix != ".pyc":
                result[candidate.relative_to(source).as_posix()] = candidate
    return result


def install(paths, source):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise Error("no_root", "Run the installer as your normal user, without sudo")
    source = Path(source).resolve()
    version = component(__version__)
    index = read_json(source / "index.json")
    if index is not None:
        update.validate_index(index)
        if index["engine"]["version"] != version:
            raise Error("version_mismatch", "Release index does not match the installing engine")
    inputs = files(source)
    if not {"src/deckcompanion/__main__.py", "vdf/__init__.py", "compat/DeckCompanion"} <= inputs.keys():
        raise Error("invalid_source", "Installer needs an extracted engine release or source checkout")
    target = paths.data / "versions" / version
    hashes = {name: net.digest(path) for name, path in inputs.items()}
    if target.exists():
        if any(not (target / name).is_file() or net.digest(target / name) != sha for name, sha in hashes.items()):
            raise Error("version_conflict", "Installed version differs; use a new version or update")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".install-", dir=target.parent))
        try:
            for name, path in inputs.items():
                output = staging / name
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, output)
            write_json(staging / ".release.json", {"version": version, "files": hashes, "source_install": True})
            os.replace(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    update.switch(paths, version)
    receipt_path = paths.data / "installation.json"
    receipt = read_json(receipt_path, {"files": {}})
    def owned_write(path, data, mode=0o644):
        payload = data.encode() if isinstance(data, str) else data
        if path.exists() and path.read_bytes() != payload and receipt["files"].get(str(path)) != net.digest(path):
            raise Error("install_conflict", f"Preserving existing file: {path}")
        atomic(path, payload)
        path.chmod(mode)
        receipt["files"][str(path)] = hashlib.sha256(payload).hexdigest()
        write_json(receipt_path, receipt)
    compat = paths.tools / "DeckCompanion"
    legacy = (compat / ".git").exists()
    stub = (source / "compat/DeckCompanion").read_text(encoding="utf-8")
    if not legacy:
        for name in ("compatibilitytool.vdf", "toolmanifest.vdf"):
            owned_write(compat / name, (source / name).read_bytes())
        owned_write(compat / "DeckCompanion", stub, 0o755)
    # Existing git-clone tools keep the migration shim and their tracked files intact.
    owned_write(paths.data / "bin/deckcompanion", stub.replace('main(["launch", *sys.argv[1:]])', 'main(sys.argv[1:])'), 0o755)
    has_ui = (source / "bin/deckcompanion-ui").is_file()
    icon = next((p for p in (source / "assets/steam/icon.png", source / "assets/logo/deckcompanion-icon-256.png") if p.is_file()), None)
    if icon:
        owned_write(paths.data / "assets/icon.png", icon.read_bytes())
    data = state(paths)
    data.pop("uninstalling", None)
    if has_ui:
        ui_stub = stub[:stub.index("from deckcompanion.cli import main")] + 'os.execv(str(root / "bin/deckcompanion-ui"), [str(root / "bin/deckcompanion-ui"), *sys.argv[1:]])\n'
        owned_write(paths.data / "bin/deckcompanion-ui", ui_stub, 0o755)
        (target / "bin/deckcompanion-ui").chmod(0o755)
        data["shortcut_pending"] = True
    data["default_pending"] = "default_on"
    if index is not None:
        merged = recipes.merge_index(read_json(paths.cache / "index.json"), index)
        write_json(paths.cache / "index.json", dict(merged, fetched_at=now()))
    installed_paths = replace(paths, recipes=target / "recipes")
    recipes.refresh_cache(installed_paths)
    budget = net.Budget()
    for ident in set(steam.installed(paths)) | set(data["games"]):
        content.immediate(installed_paths, ident, recipes.load(installed_paths, ident),
                          data["games"].setdefault(ident, {}), budget)
    from . import takeover
    takeover.reconcile(paths, data)
    steam.prune_pending(paths, data)
    save_state(paths, data)
    pending = steam.has_pending(data)
    return {"installed": True, "version": version, "shortcut_pending": bool(data.get("shortcut_pending")),
            "steam_changes_pending": pending,
            "restart_required": pending, "legacy_tool_preserved": legacy}


def uninstall(paths):
    steam.require_closed()
    data = state(paths)
    data["uninstalling"] = True
    doc = steam.read_vdf(steam.mapping_path(paths))
    for ident, mapping in steam.mappings(doc).items():
        if mapping.get("name") == "DeckCompanion" and ident.isdigit() and ident != "0":
            data["games"].setdefault(ident, {})["pending"] = "disable"
    for item in data["games"].values():
        if item.get("pending") == "enable":
            item["pending"] = None
    data["shortcut_pending"] = False
    for game in data.get("non_steam", {}).values():
        game.update(removed=True, pending="remove")
    data["default_pending"] = "default_off"
    save_state(paths, data)
    steam.apply(paths)
    for config in (paths.steam / "userdata").glob("*/config"):
        file = config / "shortcuts.vdf"
        if file.exists():
            before = file.read_bytes()
            after = steam.shortcut_bytes(paths, file, remove=True)
            from . import nonsteam
            for game in data.get("non_steam", {}).values():
                after = nonsteam.shortcut_bytes(paths, after, game)
            if before != after:
                steam.guarded_write(file, after)
        ident = steam.shortcut_identity(paths)[1]
        for suffix in steam.ARTWORK.values():
            artwork = file.parent / "grid" / f"{ident}{suffix}.png"
            if artwork.exists():
                steam.require_closed()
                artwork.unlink()
    for file in (paths.config / "localfixes").glob("[0-9]*.py"):
        if file.stem.isdigit():
            localfix.remove(paths, file.stem)
    preserved = []
    for manifest_path in (paths.data / "manifests").glob("*.json"):
        manifest = read_json(manifest_path)
        for ident, mod in list(manifest["mods"].items()):
            remaining = []
            root = Path(mod["root_path"]).resolve()
            for entry in mod["files"]:
                path = Path(entry["path"])
                if not path.resolve().is_relative_to(root) or (path.exists() and net.digest(path) != entry["sha256"]):
                    preserved.append(str(path))
                    remaining.append(entry)
                else:
                    path.unlink(missing_ok=True)
            if remaining:
                mod["files"] = remaining
            else:
                manifest["mods"].pop(ident)
        write_json(manifest_path, manifest)
    receipt = read_json(paths.data / "installation.json", {"files": {}})
    owned = dict(receipt["files"])
    for version in (paths.data / "versions").glob("*"):
        info = read_json(version / ".release.json", {})
        for name, sha in info.get("files", {}).items():
            from .common import inside
            owned[str(inside(version, name))] = sha
    for raw, sha in owned.items():
        path = Path(raw)
        if not any(path.resolve().is_relative_to(r.resolve()) for r in (paths.data, paths.tools / "DeckCompanion")):
            preserved.append(raw)
            continue
        if path.exists():
            if net.digest(path) == sha:
                steam.require_closed()
                path.unlink()
            else:
                preserved.append(raw)
    # Keep state/cache/manifests and all Steam backups as migration/recovery data.
    for name in ("current", "previous"):
        (paths.data / name).unlink(missing_ok=True)
    for version in (paths.data / "versions").glob("*"):
        receipt_file = version / ".release.json"
        if receipt_file.exists() and not any(p.is_file() and p != receipt_file for p in version.rglob("*")):
            receipt_file.unlink()
        for directory in sorted((p for p in version.rglob("*") if p.is_dir() and not p.is_symlink()), key=lambda p: len(p.parts), reverse=True):
            if not any(directory.iterdir()):
                directory.rmdir()
        if version.is_dir() and not any(version.iterdir()):
            version.rmdir()
    return {"uninstalled": True, "preserved": preserved, "steam_restart_required": True}
