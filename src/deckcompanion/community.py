"""Offline protonfixes discovery; never import or execute a community fix."""
import hashlib
from pathlib import Path
import re
from .common import Error, read_json, write_json


def valid_id(ident, kind):
    pattern = r"[0-9]+" if kind == "steam" else r"umu-[A-Za-z0-9_-]+"
    return isinstance(ident, str) and re.fullmatch(pattern, ident) is not None and ident != "umu-default"


def validate(index):
    if not isinstance(index, dict) or set(index) - {"steam", "umu"}:
        raise Error("invalid_index", "community_fixes must contain Steam/umu fix ID lists")
    for kind, ids in index.items():
        if (not isinstance(ids, list) or len(ids) > 100000 or
                any(not valid_id(ident, kind) for ident in ids) or len(set(ids)) != len(ids)):
            raise Error("invalid_index", "Invalid community fix IDs")
    return index


def scan(root, kind):
    """Collect names only: default.py and non-game helpers are never game fixes."""
    directory = root / ("gamefixes-" + kind)
    return sorted(p.stem for p in directory.glob("*.py") if p.is_file() and valid_id(p.stem, kind))


def bundled(paths, build):
    root = Path(build["path"]).resolve()
    if not re.match(r"^(?:GE-Proton|UMU-Proton|Proton-.*-GE)", build["name"], re.I):
        return {"steam": [], "umu": []}
    fixes = root / "protonfixes"
    # Directory timestamps detect added/removed fixes; build identity detects replacements.
    try:
        locations = [root, root / "proton", fixes, fixes / "gamefixes-steam", fixes / "gamefixes-umu"]
        stamp = [p.stat().st_mtime_ns if p.exists() else None for p in locations]
        target = paths.cache / "community-fixes" / (hashlib.sha256(str(root).encode()).hexdigest() + ".json")
        try:
            cached = read_json(target, {})
            if (cached.get("path") == str(root) and cached.get("mtime") == stamp and
                    set(cached["ids"]) == {"steam", "umu"}):
                return validate(cached["ids"])
        except (OSError, ValueError, TypeError, KeyError, AttributeError, Error):
            pass
        ids = {kind: scan(fixes, kind) for kind in ("steam", "umu")}
        try:
            write_json(target, {"path": str(root), "mtime": stamp, "ids": ids})
        except OSError:
            pass  # Read-only cache must not change selection.
        return ids
    except OSError:
        return {"steam": [], "umu": []}


def available(paths, ident, builds):
    kind = "umu" if str(ident).startswith("umu-") else "steam"
    matches = [build for build in builds if ident in bundled(paths, build).get(kind, [])]
    known = False
    try:
        index = read_json(paths.cache / "index.json", {}).get("community_fixes", {})
        known = ident in validate(index).get(kind, [])
    except (OSError, ValueError, TypeError, AttributeError, Error):
        pass
    return matches, known


def fields(selected, matches, known):
    applied = selected is not None and any(selected["path"] == b["path"] for b in matches)
    return {"community_fix": "applied" if applied else "available_not_installed" if known and not matches else None,
            "community_fix_source": selected["name"] if applied else None}
