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
from .common import Error, atomic, component, locked, read_json, write_json

REPO = "Open-Wine-Components/umu-launcher"
DATABASE_URL = "https://raw.githubusercontent.com/Open-Wine-Components/umu-database/main/umu-database.csv"


def game_id(value):
    value = str(value)
    if not value.startswith("umu-"):
        value = "umu-" + value
    if not re.fullmatch(r"umu-[A-Za-z0-9][A-Za-z0-9_-]{0,100}", value):
        raise Error("invalid_umu_id", "Expected an umu game identifier")
    return value


def pin(paths, version):
    if version != "latest":
        component(version)
    write_json(paths.cache / "umu-pin.json", {"version": version})


def installed(paths, item):
    if not item:
        raise Error("umu_unavailable", "Run Update to install umu-launcher before launching")
    version = component(item["version"])
    target = paths.data / "umu" / version
    runner = target / "umu-run"
    receipt = read_json(target / "receipt.json", {})
    if (runner.is_file() and (target / "LICENSE").is_file() and
            receipt.get("archive_sha256") == item.get("sha256") and
            net.digest(runner) == receipt.get("runner_sha256") and
            net.digest(target / "LICENSE") == (receipt.get("license_sha256") or item.get("license_sha256"))):
        return {"version": version, "path": str(runner), "installed": True}
    raise Error("umu_conflict", "Preserving changed umu installation; see umu directory")


def legacy(paths, version=None):
    """Read pre-channel receipts without changing existing installations."""
    from .recipes import version_key
    candidates = []
    for path in (paths.data / "umu").glob("*/receipt.json"):
        try:
            receipt = read_json(path, {})
            tag = component(receipt["version"])
            if tag != path.parent.name or (version is not None and tag != version):
                continue
            sha = receipt.get("archive_sha256", "")
            if not re.fullmatch(r"[a-f0-9]{64}", sha):
                continue
            item = {"version": tag, "url": receipt["url"], "sha256": sha,
                    "license_sha256": net.digest(path.parent / "LICENSE")}
            installed(paths, item)
            candidates.append(item)
        except (Error, OSError, ValueError, TypeError, KeyError):
            continue
    return max(candidates, key=lambda r: version_key(r["version"])) if candidates else None


def install(paths, budget=None):
    # Non-Steam launch passes its budget: only the active verified runtime is read.
    active = read_json(paths.cache / "umu-current.json") or legacy(paths)
    if budget is not None:
        return installed(paths, active)
    from . import channels
    with locked(paths, "umu.lock"):
        try:
            budget = net.DownloadBudget(60)
            wanted = read_json(paths.cache / "umu-pin.json", {}).get("version", "latest")
            item = channels.release_asset(paths, REPO, lambda v: f"umu-launcher-{v}-zipapp.tar", budget, wanted)
            if not item["sha256"]:
                raise Error("missing_digest", "umu archive requires an upstream SHA256 digest")
            target = paths.data / "umu" / item["version"]
            if target.exists():
                previous = legacy(paths, item["version"])
                if previous and previous["sha256"] == item["sha256"]:
                    item["license_sha256"] = previous["license_sha256"]
                result = installed(paths, item)
            else:
                result = activate(paths, item, budget)
            write_json(paths.cache / "umu-current.json", item)
            from .updates import record
            record(paths, "umu", "umu-launcher", active.get("version") if active else None, item["version"])
            return result
        except channels.FAILURES as exc:
            if active:
                return dict(installed(paths, active), error=str(exc), cached=True)
            raise


def activate(paths, item, budget):
    archive = net.cached(paths, item["url"], item["sha256"], budget)
    target = paths.data / "umu" / item["version"]
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
        # Retain the licence from the selected upstream tag, with a local integrity receipt.
        license_url = f"https://raw.githubusercontent.com/{REPO}/{item['version']}/LICENSE"
        net.fetch(license_url, staging / "LICENSE", budget, max_bytes=1024 * 1024)
        write_json(staging / "receipt.json", {"version": item["version"], "url": item["url"],
                   "archive_sha256": item["sha256"], "runner_sha256": net.digest(staging / "umu-run"),
                   "license_url": license_url, "license_sha256": net.digest(staging / "LICENSE")})
        os.replace(staging, target)
    except tarfile.TarError as exc:
        raise Error("invalid_umu", "Invalid umu zipapp archive") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return installed(paths, item)


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
