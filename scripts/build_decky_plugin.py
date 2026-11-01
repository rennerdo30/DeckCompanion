#!/usr/bin/env python3
"""Package the already-built frontend for Decky's install-from-ZIP feature."""
from pathlib import Path
import io
import json
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(root=ROOT):
    plugin = root / "decky-plugin"
    files = {name: plugin / name for name in ("main.py", "plugin.json", "package.json", "dist/index.js")}
    files["LICENSE"] = root / "LICENSE"
    files["THIRD_PARTY_NOTICES.md"] = plugin / "THIRD_PARTY_NOTICES.md"
    for path in files.values():
        if not path.is_file():
            raise FileNotFoundError(f"Missing {path}; run npm ci and npm run build in decky-plugin first")
    version = json.loads((plugin / "package.json").read_text(encoding="utf-8"))["version"]
    import re
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Plugin version must be a stable major.minor.patch")
    # Include the exact unmodified LGPL library source and license for relinking.
    api = plugin / "node_modules/@decky/api"
    source = io.BytesIO()
    with tarfile.open(fileobj=source, mode="w:gz") as tar:
        for name in ("src", "LICENSE", "package.json"):
            tar.add(api / name, arcname="decky-api/" + name)
    target = plugin / f"dist/deckcompanion-decky-{version}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, path in files.items():
            bundle.write(path, "DeckCompanion/" + name)
        bundle.writestr("DeckCompanion/sources/decky-api.tar.gz", source.getvalue())
    return target


if __name__ == "__main__":
    print(build())
