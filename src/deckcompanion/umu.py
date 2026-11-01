"""Runtime-only GPL zipapp installation. Never import the downloaded program."""
import csv
import io
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
from . import net
from .common import Error, atomic, read_json, write_json

VERSION = "1.4.4"
URL = f"https://github.com/Open-Wine-Components/umu-launcher/releases/download/{VERSION}/umu-launcher-{VERSION}-zipapp.tar"
SHA256 = "eb590691841f7fad3fc3ad8fd5db4ccb87849fe7948e62b28ece7a4ee48cc851"
LICENSE_URL = f"https://raw.githubusercontent.com/Open-Wine-Components/umu-launcher/{VERSION}/LICENSE"
LICENSE_SHA256 = "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"
DATABASE_URL = "https://raw.githubusercontent.com/Open-Wine-Components/umu-database/main/umu-database.csv"


def game_id(value):
    value = str(value)
    if not value.startswith("umu-"):
        value = "umu-" + value
    if not re.fullmatch(r"umu-[A-Za-z0-9][A-Za-z0-9_-]{0,100}", value):
        raise Error("invalid_umu_id", "Expected an umu game identifier")
    return value


def install(paths, budget=None):
    target = paths.data / "umu" / VERSION
    runner = target / "umu-run"
    if target.exists():
        receipt = read_json(target / "receipt.json", {})
        if (runner.is_file() and (target / "LICENSE").is_file() and
                receipt.get("archive_sha256") == SHA256 and
                net.digest(runner) == receipt.get("runner_sha256") and
                net.digest(target / "LICENSE") == LICENSE_SHA256):
            return {"version": VERSION, "path": str(runner), "installed": True}
        raise Error("umu_conflict", "Preserving changed umu installation; see umu directory")
    budget = budget or net.Budget(20)
    archive = net.cached(paths, URL, SHA256, budget)
    license_file = net.cached(paths, LICENSE_URL, LICENSE_SHA256, budget)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".umu-", dir=target.parent))
    try:
        # Extract only the official executable; no imports, symlinks or source copies.
        with tarfile.open(archive) as source:
            member = source.getmember("umu/umu-run")
            if not member.isfile() or member.size > 8 * 1024 * 1024:
                raise Error("invalid_umu", "Release lacks the expected umu-run zipapp")
            with source.extractfile(member) as stream:
                atomic(staging / "umu-run", stream.read())
        (staging / "umu-run").chmod(0o755)
        atomic(staging / "LICENSE", license_file.read_bytes())
        write_json(staging / "receipt.json", {"version": VERSION, "url": URL,
                   "archive_sha256": SHA256, "runner_sha256": net.digest(staging / "umu-run")})
        os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"version": VERSION, "path": str(runner), "installed": True}


def lookup(paths, name, store, explicit=None):
    if explicit:
        return game_id(explicit)
    database = paths.cache / "umu-database.csv"
    if not database.is_file():
        try:
            net.fetch(DATABASE_URL, database, net.Budget(), max_bytes=2 * 1024 * 1024)
        except Exception:
            return "umu-default"  # Registration always works offline.
    try:
        rows = csv.DictReader(io.StringIO(database.read_text(encoding="utf-8-sig")))
        matches = {game_id(row["UMU_ID"]) for row in rows
                   if row["TITLE"].strip().casefold() == name.casefold() and row["STORE"] == store}
        return matches.pop() if len(matches) == 1 else "umu-default"
    except (OSError, ValueError, KeyError, Error):
        return "umu-default"
