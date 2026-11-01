#!/usr/bin/env python3
"""Build locally only. Online mode pins release assets; --offline leaves them unresolved."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import time
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
os.environ["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
from deckcompanion import __version__, net, recipes
from deckcompanion.common import Error, atomic, component, write_json


def release(repo, tag, work):
    path = work / "api.json"
    net.fetch(f"https://api.github.com/repos/{repo}/releases/{tag}", path, max_bytes=8 * 1024 * 1024)
    return json.loads(path.read_text(encoding="utf-8"))


def resolve(text, work, metadata):
    recipe = tomllib.loads(text)
    # These source recipes use one literal occurrence of each field; append overlays
    # by replacing those exact TOML string tokens, never a broad text substitution.
    for mod in recipe.get("mods", []):
        if not mod.get("release_repo"):
            continue
        info = metadata.setdefault(mod["release_repo"], None)
        if info is None:
            info = release(mod["release_repo"], "latest", work)
            metadata[mod["release_repo"]] = info
        asset = next((a for a in info["assets"] if a["name"] == mod["asset"]), None)
        if asset is None:
            raise Error("missing_asset", f"No {mod['asset']} in {mod['release_repo']} release")
        sha = asset.get("digest", "").removeprefix("sha256:")
        if len(sha) != 64:
            path = work / "mod.zip"
            net.fetch(asset["browser_download_url"], path)
            sha = net.digest(path)
        for key, value in (("version", info["tag_name"]), ("url", asset["browser_download_url"]), ("sha256", sha)):
            old = f'{key} = {json.dumps(mod[key])}'
            # Restrict replacements to the matching [[mods]] block.
            head, *blocks = text.split("[[mods]]")
            for i, block in enumerate(blocks):
                if f'id = {json.dumps(mod["id"])}' in block:
                    blocks[i] = block.replace(old, f'{key} = {json.dumps(value)}', 1)
            text = head + "".join("[[mods]]" + b for b in blocks)
    runtime = recipe.get("runtime", {})
    pin = runtime.get("proton", "")
    if pin.startswith("GE-Proton") and pin != "GE-Proton" and "sha256" not in runtime:
        key = "ge:" + pin
        if key not in metadata:
            metadata[key] = release("GloriousEggroll/proton-ge-custom", "tags/" + pin, work)
        asset = next(a for a in metadata[key]["assets"] if a["name"] == pin + "-x86_64.tar.gz")
        sha = asset.get("digest", "").removeprefix("sha256:")
        if len(sha) != 64:
            raise Error("missing_digest", "GE asset has no API SHA256; refusing an unverified runtime")
        token = f'proton = {json.dumps(pin)}'
        text = text.replace(token, token + f'\nurl = {json.dumps(asset["browser_download_url"])}\nsha256 = {json.dumps(sha)}', 1)
    recipes.validate(tomllib.loads(text), recipe["appid"])
    return text


def build(output, version=__version__, offline=False):
    version = component(version)
    if version != __version__:
        raise Error("version_mismatch", "Bump src/deckcompanion/__init__.py before building another version")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"deckcompanion-{version}.tar.gz"
    base = f"https://github.com/rennerdo30/DeckCompanion/releases/download/v{version}/"
    entries, metadata = {}, {}
    with tempfile.TemporaryDirectory(prefix="dc-index-") as temp:
        work = Path(temp)
        for path in sorted((ROOT / "recipes").glob("*.toml")):
            text = path.read_text(encoding="utf-8")
            if not offline:
                text = resolve(text, work, metadata)
            recipes.validate(tomllib.loads(text), path.stem)
            entries[path.stem] = {"toml": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
        with tarfile.open(archive, "w:gz") as tar:
            def include(info):
                if "__pycache__" in info.name or info.name.endswith(".pyc"):
                    return None
                if info.name in ("compat/DeckCompanion", "bin/deckcompanion-ui", "install.sh", "uninstall.sh", "DeckCompanion-Installer.desktop"):
                    info.mode = 0o755
                return info
            for name in ("src", "vdf", "compat", "docs", "LICENSE", "THIRD_PARTY_NOTICES.md", "compatibilitytool.vdf", "toolmanifest.vdf", "install.sh", "uninstall.sh", "DeckCompanion-Installer.desktop"):
                source = ROOT / name
                if source.exists():
                    tar.add(source, arcname=name, filter=include)
            # Bundle the resolved recipes (pinned URLs and SHA256), the same text as the index:
            # a newer bundled recipe replaces the cache, so it must stay installable (Deck, 2026-11-01).
            for ident, entry in sorted(entries.items()):
                payload = entry["toml"].encode("utf-8")
                info = tarfile.TarInfo(f"recipes/{ident}.toml")
                info.size, info.mode, info.mtime = len(payload), 0o644, int(time.time())
                tar.addfile(info, io.BytesIO(payload))
            # The UI child can export this release payload; never build or modify ui/ here.
            ui = ROOT / "bin/deckcompanion-ui"
            if ui.is_file():
                tar.add(ui, arcname="bin/deckcompanion-ui", filter=include)
            logo = ROOT / "assets/logo"
            if logo.is_dir():
                tar.add(logo, arcname="assets/logo", filter=include)
            artwork = ROOT / "assets/steam"
            if artwork.is_dir():
                tar.add(artwork, arcname="assets/steam", filter=include)
        sha = net.digest(archive)
        index = {"schema_version": 1, "engine": {"version": version, "url": base + archive.name, "sha256": sha}, "recipes": entries}
        write_json(output / "index.json", index)
        assets = [archive, output / "index.json"]
        for name in ("install.sh", "DeckCompanion-Installer.desktop"):
            if (ROOT / name).is_file():
                atomic(output / name, (ROOT / name).read_bytes())
                assets.append(output / name)
        atomic(output / "checksums.sha256", "".join(f"{net.digest(p)}  {p.name}\n" for p in assets))
    return {"archive": str(archive), "sha256": sha, "index": str(output / "index.json"), "offline": offline}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--version", default=__version__)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.version, args.offline)))
