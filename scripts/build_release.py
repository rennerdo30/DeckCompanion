#!/usr/bin/env python3
"""Build an engine release locally; content and upstream versions update separately."""
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


def build(output, version=__version__, offline=False):
    version = component(version)
    if version != __version__:
        raise Error("version_mismatch", "Bump src/deckcompanion/__init__.py before building another version")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"deckcompanion-{version}.tar.gz"
    base = f"https://github.com/rennerdo30/DeckCompanion/releases/download/v{version}/"
    entries = {}
    with tempfile.TemporaryDirectory(prefix="dc-index-") as temp:
        work = Path(temp)
        for path in sorted((ROOT / "recipes").glob("*.toml")):
            text = path.read_text(encoding="utf-8")
            recipes.validate(tomllib.loads(text), path.stem)
            entries[path.stem] = {"toml": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
        with tarfile.open(archive, "w:gz") as tar:
            def include(info):
                # Development-only upstream sources and their nested dependencies never ship,
                # even if a future payload root includes a third_party directory.
                if "third_party" in Path(info.name).parts or "__pycache__" in info.name or info.name.endswith(".pyc"):
                    return None
                if info.name in ("compat/DeckCompanion", "bin/deckcompanion-ui", "install.sh", "uninstall.sh", "DeckCompanion-Installer.desktop"):
                    info.mode = 0o755
                return info
            for name in ("src", "vdf", "compat", "content", "docs", "LICENSE", "THIRD_PARTY_NOTICES.md", "compatibilitytool.vdf", "toolmanifest.vdf", "install.sh", "uninstall.sh", "DeckCompanion-Installer.desktop"):
                source = ROOT / name
                if source.exists():
                    tar.add(source, arcname=name, filter=include)
            # Bundle source recipes as the offline fallback; never resolve upstream here.
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
        index = {"schema_version": 1, "engine": {"version": version, "url": base + archive.name, "sha256": sha}, "recipes": {}}
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
