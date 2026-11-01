#!/usr/bin/env python3
"""Build independent content assets locally. Never publishes or pins latest mods."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
os.environ["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
from deckcompanion import channels, community, net, recipes
from deckcompanion.common import Error, atomic, component, write_json


def resolve(text, paths, metadata):
    """Enrich deliberate upstream pins; latest mods are resolved only by update."""
    recipe = recipes.validate(tomllib.loads(text))
    for mod in recipe.get("mods", []):
        if not mod.get("release_repo") or mod["version"] == "latest" or mod["sha256"]:
            continue
        item = channels.release_asset(paths, mod["release_repo"], mod["asset"], net.Budget(), mod["version"], metadata)
        if not item["sha256"]:
            raise Error("missing_digest", "Pinned mod requires an upstream SHA256")
        head, *blocks = text.split("[[mods]]")
        for i, block in enumerate(blocks):
            if f'id = {json.dumps(mod["id"])}' in block:
                for key in ("url", "sha256"):
                    block = block.replace(f'{key} = {json.dumps(mod[key])}', f'{key} = {json.dumps(item[key])}', 1)
                blocks[i] = block
        text = head + "".join("[[mods]]" + b for b in blocks)
    runtime = recipe.get("runtime", {})
    pin = runtime.get("proton", "")
    if pin.startswith("GE-Proton") and pin != "GE-Proton" and "sha256" not in runtime:
        item = channels.release_asset(paths, "GloriousEggroll/proton-ge-custom", pin + "-x86_64.tar.gz", net.Budget(), pin, metadata)
        if not item["sha256"]:
            raise Error("missing_digest", "GE asset requires an upstream SHA256")
        token = f'proton = {json.dumps(pin)}'
        text = text.replace(token, token + f'\nurl = {json.dumps(item["url"])}\nsha256 = {json.dumps(item["sha256"])}', 1)
    return text


def entry(text):
    return {"toml": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}


def build(output, version, min_engine="0.4.3", offline=False):
    component(version)
    output.mkdir(parents=True, exist_ok=True)
    index = {"schema_version": 1, "content_version": version, "min_engine": min_engine, "recipes": {}}
    with tempfile.TemporaryDirectory(prefix="dc-content-") as temp:
        from types import SimpleNamespace
        paths, metadata = SimpleNamespace(cache=Path(temp)), {}
        for path in sorted((ROOT / "recipes").glob("*.toml")):
            text = path.read_text(encoding="utf-8")
            if not offline:
                text = resolve(text, paths, metadata)
            recipes.validate(tomllib.loads(text), path.stem)
            index["recipes"][path.stem] = entry(text)
    launchers = ROOT / "content/launchers.toml"
    if launchers.is_file():
        index["launchers"] = entry(launchers.read_text(encoding="utf-8"))
    upstream = ROOT / "third_party/umu-protonfixes"
    if (upstream / "gamefixes-steam").is_dir():
        index["community_fixes"] = {kind: community.scan(upstream, kind) for kind in ("steam", "umu")}
    channels.validate(index, check_engine=False)
    target = output / "content-index.json"
    write_json(target, index)
    atomic(output / "checksums.sha256", f"{net.digest(target)}  content-index.json\n")
    return {"index": str(target), "content_version": version, "tag": "content-" + version}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/content")
    parser.add_argument("--version", required=True)
    parser.add_argument("--min-engine", default="0.4.3")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.version, args.min_engine, args.offline)))
