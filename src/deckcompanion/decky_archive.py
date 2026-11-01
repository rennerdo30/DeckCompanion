"""Standalone, isolated sudo helper. No package imports or credential handling."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import zipfile

MAX_ARCHIVE = 128 * 1024**2
MAX_EXPANDED = 256 * 1024**2


def extract(source, staging, name, version):
    with zipfile.ZipFile(source) as bundle:
        entries = bundle.infolist()
        if len(entries) > 10000 or sum(e.file_size for e in entries) > MAX_EXPANDED:
            raise ValueError("Plugin exceeds expansion limit")
        seen, roots = set(), set()
        for entry in entries:
            relative = entry.orig_filename.rstrip("/")
            parts = relative.split("/")
            mode = entry.external_attr >> 16
            if (not relative or any(p in ("", ".", "..") for p in parts) or
                    "\\" in relative or ":" in relative or any(ord(c) < 32 for c in relative) or
                    relative in seen or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError("Unsafe plugin archive entry")
            seen.add(relative)
            roots.add(parts[0])
        if len(roots) != 1:
            raise ValueError("Expected one plugin directory")
        for entry in entries:
            target = staging.joinpath(*entry.filename.rstrip("/").split("/"))
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as incoming, target.open("xb") as output:
                    shutil.copyfileobj(incoming, output)
                target.chmod(0o755 if (entry.external_attr >> 16) & 0o111 else 0o644)
    root = staging / roots.pop()
    if (json.loads((root / "plugin.json").read_text(encoding="utf-8"))["name"] != name or
            json.loads((root / "package.json").read_text(encoding="utf-8"))["version"] != version):
        raise ValueError("Plugin metadata differs from requested release")
    return root


def exchange(left, right):
    # Both directories stay present throughout activation (Linux renameat2).
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(left), -100, os.fsencode(right), 2):
        raise OSError(ctypes.get_errno(), "Atomic plugin exchange failed")


def replace(source, target, name, version, sha):
    source, target = Path(source), Path(target)
    if (not target.is_absolute() or target.is_symlink() or (target.exists() and not target.is_dir()) or
            target.parent.is_symlink() or target.parent.resolve() != target.parent):
        raise ValueError("Plugin target must be a directory without symlink parents")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Snapshot the unprivileged download into root's private staging directory,
    # then hash and extract that same descriptor; no download/extraction race.
    staging = Path(tempfile.mkdtemp(prefix=".dc-plugin-", dir=target.parent))
    activated = False
    try:
        with source.open("rb") as incoming, (staging / "archive.zip").open("w+b") as archive:
            total = 0
            while block := incoming.read(128 * 1024):
                total += len(block)
                if total > MAX_ARCHIVE:
                    raise ValueError("Plugin download exceeds size limit")
                archive.write(block)
            archive.seek(0)
            if hashlib.file_digest(archive, "sha256").hexdigest() != sha:
                raise ValueError("Plugin SHA256 mismatch")
            archive.seek(0)
            root = extract(archive, staging / "unpack", name, version)
        # mkdir's umask may differ under sudo. Decky needs readable directories.
        for directory in [root, *(p for p in root.rglob("*") if p.is_dir())]:
            directory.chmod(0o755)
        if os.geteuid() == 0:
            for item in [root, *root.rglob("*")]:
                os.chown(item, 0, 0)
        if target.exists():
            exchange(root, target)
        else:
            # Same-filesystem rename publishes the complete new directory at once.
            os.rename(root, target)
        activated = True
        # Old bytes remain recoverable in this private root-owned directory.
    finally:
        if not activated:
            shutil.rmtree(staging)


if __name__ == "__main__":
    try:
        replace(*sys.argv[1:])
    except Exception:
        # Never echo subprocess inputs, archive content or credentials.
        sys.exit(1)
