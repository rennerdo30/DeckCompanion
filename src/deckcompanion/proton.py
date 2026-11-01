"""Owned GE-Proton installs. No launch-path dependency and no third-party code."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile
import time
import uuid
from urllib.parse import quote
from . import archives, net, recipes, steam
from .common import Error, locked, now, read_json, save_state, state, write_json
from .splash import settings

API = "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases"
LIMIT = 2 * 1024**3
EXPANSION = 4 * 1024**3
CACHE_SECONDS = 6 * 3600


def canonical(name):
    name = re.sub(r"-x86_64$", "", name)
    if not re.fullmatch(r"(?:GE-Proton\d+(?:[.-]\d+)+(?:-[A-Za-z0-9.]+)?|Proton-\d+(?:[.-]\d+)+-GE(?:-[A-Za-z0-9.]+)?)", name):
        raise Error("invalid_proton", "Expected a versioned GE-Proton build name")
    return name


def release_asset(release):
    """Share archive selection between discovery and the verified installer."""
    tag = canonical(release["tag_name"])
    if release.get("draft") or release.get("prerelease"):
        raise Error("invalid_release", "Expected a published stable GE release")
    asset = next(a for a in release["assets"] if a["name"] in
                 (tag + "-x86_64.tar.gz", tag + ".tar.gz"))
    if (type(asset["size"]) is not int or not 0 < asset["size"] <= LIMIT or
            not asset["browser_download_url"].startswith("https://")):
        raise Error("invalid_release", "GE archive must use HTTPS and fit the 2 GiB limit")
    return tag, asset


def archive_digest(asset):
    algorithm, _, checksum = (asset.get("digest") or "").partition(":")
    return checksum if algorithm == "sha256" and re.fullmatch(r"[a-f0-9]{64}", checksum) else None


def available(paths, page=1, per_page=30):
    """One GitHub page per call, six-hour cache, two-second network budget."""
    if type(page) is not int or page < 1 or type(per_page) is not int or not 1 <= per_page <= 100:
        raise Error("invalid_arguments", "Use page >= 1 and per-page between 1 and 100")
    target = paths.cache / "ge" / f"releases-{page}-{per_page}.json"
    try:
        cached = read_json(target, {})
        if (not isinstance(cached, dict) or not isinstance(cached.get("builds"), list) or
                not isinstance(cached.get("fetched"), (int, float))):
            cached = {}
    except (OSError, ValueError):
        cached = {}
    result, from_cache, stale, error = cached, True, False, None
    if not cached or not 0 <= time.time() - cached.get("fetched", 0) < CACHE_SECONDS:
        # Each reader gets its own response path; concurrent UI calls cannot mix pages.
        response = target.with_name(f".releases-{uuid.uuid4().hex}.json")
        try:
            net.fetch(f"{API}?per_page={per_page}&page={page}", response, net.Budget(2), max_bytes=8 * 1024**2)
            releases = read_json(response)
            if not isinstance(releases, list):
                raise Error("invalid_release", "Expected a GitHub releases array")
            builds = []
            for release in releases:
                try:
                    name, asset = release_asset(release)
                    digest = archive_digest(asset)
                    checksum_asset = next((a for a in release["assets"] if a["name"] ==
                        asset["name"].removesuffix(".tar.gz") + ".sha512sum" and
                        a.get("browser_download_url", "").startswith("https://")), None)
                    # Every listed build has a verification source the installer supports.
                    if not digest and not checksum_asset:
                        continue
                    builds.append({"name": name, "published_at": release.get("published_at"),
                                   "size": asset["size"], "digest_available": bool(digest),
                                   "checksum_algorithm": "sha256" if digest else "sha512"})
                except (Error, KeyError, TypeError, StopIteration):
                    continue  # Unsupported architectures/tags are not install choices.
            builds.sort(key=lambda b: b["published_at"] or "", reverse=True)
            result = {"builds": builds, "fetched": time.time(), "fetched_at": now(),
                      "has_more": len(releases) == per_page}
            write_json(target, result)
            from_cache = False
        except (Error, OSError, ValueError, TypeError) as exc:
            if not cached:
                raise Error("ge_available_failed", f"Cannot list GE-Proton releases: {exc}") from exc
            stale, error = True, str(exc)
        finally:
            response.unlink(missing_ok=True)
    installed = {canonical_or_name(b["name"]) for b in steam.proton(paths)}
    has_more = result.get("has_more", False)
    return {"builds": [dict(b, installed=b["name"] in installed) for b in result.get("builds", [])],
            "source": "GloriousEggroll/proton-ge-custom", "page": page, "per_page": per_page,
            "has_more": has_more, "next_page": page + 1 if has_more else None,
            "fetched_at": result.get("fetched_at"), "cached": from_cache, "stale": stale, "warning": error}


def canonical_or_name(name):
    try:
        return canonical(name)
    except Error:
        return name


def installed_size(root):
    """Logical bytes, without following directory/file links or Windows junctions."""
    total = 0
    def unreadable(exc):
        raise exc
    for directory, folders, files in os.walk(root, followlinks=False, onerror=unreadable):
        folders[:] = [n for n in folders if not is_link(Path(directory) / n)]
        for name in files:
            path = Path(directory) / name
            if not is_link(path):
                total += path.stat().st_size
    return total


def is_link(path):
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def wanted(paths):
    result = {}
    for ident, saved in state(paths).get("games", {}).items():
        if not saved.get("enabled"):
            continue
        recipe = recipes.load(paths, ident)
        runtime = (recipe or {}).get("runtime", {})
        pin = runtime.get("proton", "")
        if (pin.startswith("GE-Proton") and pin != "GE-Proton") or (pin.startswith("Proton-") and "-GE" in pin):
            name = canonical(pin)
            item = result.setdefault(name, {"wanted_by": [], "sha256": runtime.get("sha256")})
            sha = runtime.get("sha256")
            if sha and item["sha256"] and sha != item["sha256"]:
                raise Error("checksum_conflict", f"Enabled recipes disagree on {name}")
            item["sha256"] = sha or item["sha256"]
            item["wanted_by"].append(recipe["name"])
    return result


def status_path(paths, name):
    return paths.data / "proton-status" / (canonical(name) + ".json")


def report(paths, name, phase, **fields):
    write_json(status_path(paths, name), dict(name=name, state=phase, updated_at=now(), **fields))


def inventory(root):
    """Include added files and link targets so cleanup preserves local changes."""
    return {p.relative_to(root).as_posix(): ("link:" + os.readlink(p) if p.is_symlink() else
            "sha256:" + net.digest(p) if p.is_file() else "directory")
            for p in root.rglob("*") if p.name != ".deckcompanion-ge.json"}


def metadata(paths, name=None):
    """Select x86_64 explicitly; a checksum asset's digest is not the archive digest."""
    name = canonical(name) if name else None
    target = paths.cache / "ge" / ((name or "latest") + ".json")
    response = target.with_suffix(".response.json")
    try:
        net.fetch(API + ("/tags/" + quote(name) if name else "/latest"), response,
                  net.DownloadBudget(30), max_bytes=2 * 1024**2)
        release = read_json(response)
        tag, asset = release_asset(release)
        if name and tag != name:
            raise Error("invalid_release", "Unexpected GE release")
        size, url = asset["size"], asset["browser_download_url"]
        algorithm, checksum = "sha256", archive_digest(asset)
        if not checksum:
            checksum_asset = next(a for a in release["assets"] if
                                  a["name"] == asset["name"].removesuffix(".tar.gz") + ".sha512sum")
            checksum_file = response.with_suffix(".sha512sum")
            net.fetch(checksum_asset["browser_download_url"], checksum_file,
                      net.DownloadBudget(30), max_bytes=8192)
            checks = [line.split() for line in checksum_file.read_text().splitlines()]
            checksum = next(parts[0] for parts in checks if len(parts) == 2 and
                            parts[1].lstrip("*") == asset["name"] and re.fullmatch(r"[a-fA-F0-9]{128}", parts[0]))
            algorithm, checksum = "sha512", checksum.lower()
        result = {"name": tag, "url": url, "size": size, "algorithm": algorithm, "checksum": checksum}
        write_json(target, result)
        return result
    except (Error, OSError, ValueError, TypeError, KeyError, StopIteration) as exc:
        cached = read_json(target)
        if cached:
            return dict(cached, stale=True)
        raise Error("ge_metadata_failed", f"Cannot resolve {name or 'latest GE-Proton'}: {exc}") from exc


def listing(paths):
    pins = wanted(paths)
    latest = read_json(paths.cache / "ge/latest.json", {}).get("name")
    if latest:
        pins.setdefault(latest, {"wanted_by": [], "sha256": None})["latest"] = True
    builds = {}
    for installed in steam.proton(paths):
        name = canonical_or_name(installed["name"])
        builds[name] = dict(installed, name=name, state="installed", installed=True)
        try:
            builds[name]["installed_bytes"] = installed_size(Path(installed["path"]))
        except OSError:
            builds[name]["installed_bytes"] = None
        builds[name]["removable"] = removable_path(paths, Path(installed["path"]))
    for name, info in pins.items():
        builds.setdefault(name, {"name": name, "installed": False, "state": "wanted"}).update(info)
    for file in (paths.data / "proton-status").glob("*.json"):
        receipt = read_json(file, {})
        name = receipt.get("name")
        if name and not builds.get(name, {}).get("installed"):
            builds.setdefault(name, {"name": name, "installed": False}).update(receipt)
    job = read_json(paths.data / "proton-job.json", {})
    if job.get("active") and time.time() - job.get("started", 0) > 3700:
        job = dict(job, active=False, state="failed", error="Download worker expired; retry to resume")
        for build in builds.values():
            if build["state"] in ("downloading", "scheduled", "verifying", "unpacking"):
                build.update(state="failed", error=job["error"])
    return {"builds": list(builds.values()), "active": job.get("active", False), "job": job,
            "status_file": str(paths.data / "proton-job.json")}


def removable_path(paths, target):
    """A configured tools root must never grant access to Steam-managed depots."""
    if (target.parent != paths.tools.resolve() or is_link(target) or
            not re.match(r"^(GE-Proton|UMU-Proton|Proton(?:[ -]|\d))", target.name, re.I)):
        return False
    roots = steam.libraries(paths)
    return not any(target.is_relative_to((lib / "steamapps/common").resolve()) for lib in roots)


def references(paths, name, data):
    refs = []
    for ident in sorted(recipes.ids(paths)):
        recipe = recipes.load(paths, ident)
        pin = (recipe or {}).get("runtime", {}).get("proton", "")
        if pin and canonical_or_name(pin) == name:
            refs.append({"kind": "recipe_pin", "appid": ident, "name": recipe["name"]})
    for ident, saved in data.get("games", {}).items():
        override = saved.get("proton_override")
        if override and canonical_or_name(override) == name:
            refs.append({"kind": "game_override", "appid": ident, "name": ident})
    return refs


def remove(paths, name, force=False):
    """Explicit removal may remove external tools, but never a Steam-managed depot."""
    with locked(paths, "proton-schedule.lock"), locked(paths, "proton.lock"), locked(paths):
        if listing(paths)["active"]:
            raise Error("busy", "A Proton download job is already running")
        name = canonical_or_name(name)
        candidates = [p for p in paths.tools.glob("*") if canonical_or_name(p.name) == name and
                      (p / "proton").is_file()]
        if len(candidates) != 1:
            raise Error("proton_not_removable", "Choose one installed build inside compatibilitytools.d")
        target = candidates[0]
        if not removable_path(paths, target) or target.resolve() != target.absolute():
            raise Error("proton_not_removable", "Refusing a linked or Steam-managed Proton build")
        data = state(paths)
        refs = references(paths, name, data)
        if refs and not force:
            raise Error("proton_in_use", "Build is referenced; use --force to remove anyway: " +
                        ", ".join(f"{r['kind']} {r['appid']} ({r['name']})" for r in refs))
        if steam.proton_running(target):
            raise Error("proton_running", "A running process uses this Proton build; close the game first")
        # Also reuse game detection for reaper/pre-launch stages before Proton appears.
        from .launch import selection, select_proton
        games = set(steam.installed(paths)) | set(data.get("games", {}))
        available_builds = steam.proton(paths)
        for ident in games:
            selected, _, _ = selection(paths, ident, recipes.load(paths, ident) or {},
                                       data.get("games", {}).get(ident), available_builds)
            if selected and Path(selected["path"]).resolve() == target.resolve() and steam.game_running(ident):
                raise Error("proton_running", f"Game {ident} may be using this build; close it first")
        for game in data.get("non_steam", {}).values():
            if game.get("removed"):
                continue
            selected, _ = select_proton(available_builds, recipes.load(paths, game["umu_id"]) or {})
            if selected and Path(selected["path"]).resolve() == target.resolve() and steam.game_running(game["appid"]):
                raise Error("proton_running", f"Game {game['appid']} may be using this build; close it first")
        removed_at = now()
        # Journal intent first, so interrupted removals remain visible.
        removals = data.setdefault("proton_removals", {})
        removals[name] = {"path": str(target), "removed_at": removed_at, "state": "removing",
                          "forced": bool(force), "references": refs}
        save_state(paths, data)
        shutil.rmtree(target)  # Unlinks nested symlinks/junctions without traversing their targets.
        registry = read_json(paths.data / "proton-installed.json", {})
        registry.pop(name, None)
        write_json(paths.data / "proton-installed.json", registry)
        status_path_for_remove = paths.data / "proton-status" / (name + ".json")
        status_path_for_remove.unlink(missing_ok=True)
        removals[name]["state"] = "removed"
        save_state(paths, data)
        return dict(listing(paths), removed=[name], warnings=refs)


def install(paths, name):
    name = canonical(name)
    installed = next((b for b in listing(paths)["builds"] if b["name"] == name and b["installed"]), None)
    if installed:
        return installed
    staging = None
    try:
        report(paths, name, "downloading", percent=0)
        release = metadata(paths, name)
        pin = wanted(paths).get(name, {}).get("sha256")
        if pin and release["algorithm"] == "sha256" and pin != release["checksum"]:
            raise Error("checksum_mismatch", "Recipe SHA256 differs from upstream archive digest")
        paths.tools.mkdir(parents=True, exist_ok=True)
        target = paths.tools / name
        if target.exists() or target.is_symlink():
            raise Error("proton_conflict", "Preserving an existing runtime directory")
        downloads = paths.cache / "ge/downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        partial = downloads / (release["checksum"] + ".part")
        # Account for separate cache/tools filesystems conservatively, including full expansion.
        for directory, needed in ((downloads, release["size"]), (paths.tools, EXPANSION + release["size"])):
            if shutil.disk_usage(directory).free < needed + 256 * 1024**2:
                raise Error("disk_space", "Not enough free disk space for GE-Proton (archive plus up to 4 GiB unpacked)")
        budget = net.DownloadBudget()
        report(paths, name, "downloading", percent=0, total_bytes=release["size"])
        net.download(release["url"], partial, release["size"], status_path(paths, name), budget)
        report(paths, name, "verifying", downloaded_bytes=release["size"], total_bytes=release["size"])
        with partial.open("rb") as stream:
            checksum = hashlib.file_digest(stream, release["algorithm"]).hexdigest()
        sha256 = net.digest(partial)
        if partial.stat().st_size != release["size"] or checksum != release["checksum"] or (pin and sha256 != pin):
            partial.unlink(missing_ok=True)  # Corrupt full files cannot be resumed.
            raise Error("checksum_mismatch", "GE-Proton archive checksum mismatch")
        report(paths, name, "unpacking", downloaded_bytes=release["size"], total_bytes=release["size"])
        staging = Path(tempfile.mkdtemp(prefix=".dc-ge-", dir=paths.tools))
        archives.unpack(partial, staging, budget)
        roots = list(staging.iterdir())
        if len(roots) != 1 or roots[0].is_symlink() or not roots[0].is_dir():
            raise Error("invalid_proton", "Expected one GE-Proton directory")
        root = roots[0]
        if canonical(root.name) != name or not (root / "proton").is_file() or (root / "proton").is_symlink():
            raise Error("invalid_proton", "Archive lacks the requested Proton executable")
        installed_bytes = sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())
        receipt = {"id": uuid.uuid4().hex, "sha256": sha256, "installed_at": now(), "installed_bytes": installed_bytes,
                   "files": inventory(root)}
        write_json(root / ".deckcompanion-ge.json", receipt)
        # Journal before rename: a crash may leave an absent entry, never an unowned install.
        registry = read_json(paths.data / "proton-installed.json", {})
        registry[name] = receipt
        write_json(paths.data / "proton-installed.json", registry)
        if target.exists() or target.is_symlink():
            raise Error("proton_conflict", "Preserving an existing runtime directory")
        os.rename(root, target)
        partial.unlink(missing_ok=True)
        report(paths, name, "installed", percent=100, total_bytes=release["size"])
        return {"name": name, "state": "installed", "path": str(target), "sha256": sha256}
    except Exception as exc:
        report(paths, name, "failed", error=str(exc))
        raise
    finally:
        if staging and staging.exists():
            shutil.rmtree(staging)


def cleanup(paths, keep):
    removed = []
    registry = read_json(paths.data / "proton-installed.json", {})
    # Never remove runtimes from a running game or a concurrent launch.
    if steam.running():
        return removed
    with locked(paths):
        keep = set(keep) | set(wanted(paths))  # Recheck recipes after the downloads.
        keep.update(canonical_or_name(g["proton_override"]) for g in state(paths).get("games", {}).values()
                    if g.get("proton_override"))
        for name, receipt in list(registry.items()):
            target = paths.tools / canonical(name)
            if name in keep or target.is_symlink() or not target.is_dir():
                continue
            marker = target / ".deckcompanion-ge.json"
            script = target / "proton"
            if marker.is_symlink() or script.is_symlink() or read_json(marker) != receipt or not script.is_file():
                continue
            if inventory(target) != receipt.get("files"):
                continue
            shutil.rmtree(target)
            del registry[name]
            status_path(paths, name).unlink(missing_ok=True)
            removed.append(name)
        write_json(paths.data / "proton-installed.json", registry)
    return removed


def sync(paths):
    pins = wanted(paths)
    options = settings(state(paths))
    errors = []
    # Resolve newest even with auto-install off: cleanup must still preserve it.
    try:
        newest = metadata(paths)
        latest = newest["name"]
        if newest.get("stale"):
            errors.append("Using cached GE metadata; cleanup deferred until latest release can be checked")
    except Error as exc:
        latest = read_json(paths.cache / "ge/latest.json", {}).get("name")
        errors.append(str(exc))
    needed = set(pins)
    if latest and options["auto_install_ge"]:
        needed.add(latest)
    for name in sorted(needed):
        try:
            install(paths, name)
        except (Error, OSError, ValueError, tarfile.TarError) as exc:
            errors.append(f"{name}: {exc}")
    removed = []
    if options["cleanup_old_ge"] and latest and not errors:
        removed = cleanup(paths, set(pins) | {latest})
    return dict(listing(paths), removed=removed, errors=errors)


def run(paths, task, name=None):
    # Distinct from state.lock: downloads never hold settings/launch/status hostage.
    with locked(paths, "proton.lock"):
        write_json(paths.data / "proton-job.json", {"active": True, "state": "running", "started": time.time(), "pid": os.getpid()})
        try:
            if task == "update":
                from .update import update
                result = update(paths)
            else:
                result = install(paths, name)
            errors = result.get("proton", {}).get("errors", [])
            write_json(paths.data / "proton-job.json", {"active": False, "state": "failed" if errors else "finished",
                                                      "error": "; ".join(errors), "result": result})
            return result
        except Exception as exc:
            write_json(paths.data / "proton-job.json", {"active": False, "state": "failed", "error": str(exc)})
            raise


def schedule(paths, task, name=None):
    from .apply_helper import helper_command
    if not sys.platform.startswith("linux") or not shutil.which("systemd-run"):
        raise Error("detach_unavailable", "Background Proton installs require systemd-run --user; run the CLI directly instead")
    with locked(paths, "proton-schedule.lock"):
        with locked(paths, "proton.lock"):
            pass  # Check for a foreground worker; release before systemd starts one.
        if listing(paths)["active"]:
            raise Error("busy", "A Proton download job is already running")
        command = helper_command(paths)
        command = command[:3] + ["proton-helper", task] + ([canonical(name)] if name else []) + ["--json"]
        write_json(paths.data / "proton-job.json", {"active": True, "state": "scheduled", "started": time.time()})
        if name:
            report(paths, name, "scheduled", percent=0)
        try:
            steam.run([shutil.which("systemd-run"), "--user", "--unit=deckcompanion-proton", "--collect",
                       "--property=RuntimeMaxSec=3600", "--", *command], timeout=5)
        except Exception as exc:
            write_json(paths.data / "proton-job.json", {"active": False, "state": "failed", "error": str(exc)})
            if name:
                report(paths, name, "failed", error=str(exc))
            raise
    return {"scheduled": True, "active": True, "status_file": str(paths.data / "proton-job.json")}
