"""Original MIT launcher presets and bounded, detached installer supervision."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from . import content, net, nonsteam, steam
from .common import Error, inside, locked, now, read_json, state, write_json

LIMIT = 512 * 1024**2
DOWNLOAD_TIMEOUT = 300
INSTALL_TIMEOUT = 1200
JOB_TIMEOUT = 1560
WARNING = "Vendor checksum not published; SHA256 records downloaded bytes, not vendor authenticity."


def preset(paths, ident):
    found = next((p for p in content.launchers(paths) if p["id"] == ident), None)
    if not found:
        raise Error("unknown_launcher", "Unknown launcher id")
    return dict(found)


def prefix_path(paths, ident):
    preset(paths, ident)
    return inside(paths.data, "prefixes/launcher-" + ident)


def status_path(paths, ident):
    preset(paths, ident)
    return inside(paths.data, "launcher-status/" + ident + ".json")


def report(paths, ident, **fields):
    path = status_path(paths, ident)
    job = read_json(path, {})
    job.update(fields, updated_at=now())
    write_json(path, job)
    return job


def job_status(paths, ident):
    job = read_json(status_path(paths, ident), {})
    if job.get("active") and time.time() - job.get("started", 0) > JOB_TIMEOUT:
        return dict(job, active=False, phase="failed", error="Installer worker expired; retry installation")
    return job


def registration(paths, ident):
    saved = state(paths).get("non_steam", {}).get("launcher-" + ident)
    if saved and not saved.get("removed"):
        if saved.get("launcher") != ident:
            raise Error("game_conflict", "Preserving another game with this launcher slug")
        return saved
    return None


def info(paths, ident):
    item = preset(paths, ident)
    prefix = prefix_path(paths, ident)
    exe = inside(prefix, item["installed_exe"])
    job, saved = job_status(paths, ident), registration(paths, ident)
    installed = bool(saved and exe.is_file())
    status = ("installing" if job.get("active") else "failed" if job.get("error") else
              "installed" if installed else "not_installed")
    return dict(item, state=status, installed=installed, prefix=str(prefix), exe=str(exe),
                slug=saved["slug"] if saved else None, appid=saved["appid"] if saved else None,
                shortcut_pending=saved.get("pending") if saved else None,
                art=nonsteam.artwork(paths, saved) if saved else {k: None for k in ("portrait", "hero", "header", "logo")},
                warnings=job.get("warnings", [] if item["checksum_sha256"] else [WARNING]), job=job or None,
                status_file=str(status_path(paths, ident)))


def listing(paths):
    return {"launchers": [info(paths, p["id"]) for p in content.launchers(paths)]}


def register(paths, ident):
    item = preset(paths, ident)
    exe = inside(prefix_path(paths, ident), item["installed_exe"])
    if not exe.is_file():
        raise Error("launcher_exe_missing", "Installer exited without the expected launcher executable")
    with locked(paths):
        saved = registration(paths, ident)
        if saved:
            return nonsteam.game(paths, saved)
        return nonsteam.add(paths, exe, item["name"], item["store"], item["gameid"], launcher=ident)


def schedule(paths, ident):
    item = preset(paths, ident)
    if not sys.platform.startswith("linux") or not shutil.which("systemd-run"):
        raise Error("detach_unavailable", "Launcher installation requires Linux systemd-run --user")
    with locked(paths, "launcher-" + ident + "-schedule.lock"):
        with locked(paths, "launcher-" + ident + ".lock"):
            if job_status(paths, ident).get("active"):
                raise Error("busy", "This launcher is already installing")
            if info(paths, ident)["installed"]:
                return {"scheduled": False, "launcher": info(paths, ident)}
            token = uuid.uuid4().hex
            write_json(status_path(paths, ident), {"active": True, "phase": "scheduled", "token": token,
                "started": time.time(), "updated_at": now(), "pid": None, "installer_pid": None,
                "sha256": None, "downloaded_bytes": 0, "error": None,
                "warnings": [] if item["checksum_sha256"] else [WARNING]})
        from .apply_helper import helper_command
        command = helper_command(paths)[:3] + ["launcher-helper", ident, token, "--json"]
        # systemd owns the installer process tree, even if Steam/Game Mode closes.
        service = [shutil.which("systemd-run"), "--user", "--collect",
                   "--unit=deckcompanion-launcher-" + ident + "-" + token,
                   f"--property=RuntimeMaxSec={JOB_TIMEOUT}", "--property=KillMode=control-group"]
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR"):
            if key in os.environ:
                service.append("--setenv=" + key + "=" + os.environ[key])
        try:
            steam.run([*service, "--", *command], timeout=5)
        except Exception as exc:
            report(paths, ident, active=False, phase="failed", error=str(exc))
            raise
    return {"scheduled": True, "launcher": info(paths, ident)}


def run(paths, ident, token):
    item = preset(paths, ident)
    with locked(paths, "launcher-" + ident + ".lock"):
        job = job_status(paths, ident)
        if job.get("token") != token or not job.get("active") or job.get("phase") != "scheduled":
            raise Error("stale_job", "Installer request is no longer active")
        report(paths, ident, pid=os.getpid(), phase="preparing")
        try:
            prefix = prefix_path(paths, ident)
            prefix.mkdir(parents=True, exist_ok=True)
            exe = inside(prefix, item["installed_exe"])
            # A removed launcher can be registered again without overwriting its prefix.
            if not exe.is_file():
                saved = {"slug": "launcher-" + ident, "exe": str(exe),
                         "store": item["store"], "umu_id": item["gameid"]}
                runner, runtime_env, _ = nonsteam.runtime(paths, saved, net.DownloadBudget(DOWNLOAD_TIMEOUT))
                report(paths, ident, phase="downloading")
                installer = inside(paths.cache, "launcher-installers/" + ident + "-" + token + "." + item["installer_type"])
                try:
                    net.fetch(item["installer_url"], installer, net.DownloadBudget(DOWNLOAD_TIMEOUT), max_bytes=LIMIT)
                    size = installer.stat().st_size
                    if not 0 < size <= LIMIT:
                        raise Error("invalid_installer", "Installer is empty or exceeds 512 MiB")
                    sha = net.digest(installer)
                    report(paths, ident, phase="verifying", sha256=sha, downloaded_bytes=size)
                    if item["checksum_sha256"] and sha != item["checksum_sha256"]:
                        raise Error("checksum_mismatch", "Installer checksum mismatch")
                    with installer.open("rb") as stream:
                        magic = stream.read(8)
                    if not (magic.startswith(b"MZ") if item["installer_type"] == "exe" else magic == bytes.fromhex("d0cf11e0a1b11ae1")):
                        raise Error("invalid_installer", "Vendor returned an unexpected installer format")
                    args = (["msiexec", "/i", "Z:" + str(installer).replace("/", "\\")]
                            if item["installer_type"] == "msi" else [str(installer)])
                    log = inside(paths.data, "logs/launcher-" + ident + ".log")
                    log.parent.mkdir(parents=True, exist_ok=True)
                    with log.open("wb") as output:
                        proc = subprocess.Popen([runner, *args, *item["installer_args"]], cwd=prefix,
                            env=dict(os.environ, **runtime_env), stdout=output, stderr=subprocess.STDOUT)
                        steam.STARTED_PIDS.append(proc.pid)
                        report(paths, ident, phase="installing", installer_pid=proc.pid, log_file=str(log))
                        try:
                            result = proc.wait(timeout=INSTALL_TIMEOUT)
                        except subprocess.TimeoutExpired as exc:
                            proc.kill()
                            proc.wait(timeout=2)
                            raise Error("installer_timeout", "Launcher installer timed out") from exc
                    if result not in (0, 3010):
                        raise Error("installer_failed", f"Launcher installer exited with code {result}")
                finally:
                    installer.unlink(missing_ok=True)
            report(paths, ident, phase="registering")
            register(paths, ident)
            report(paths, ident, active=False, phase="finished", error=None)
            return {"launcher": info(paths, ident)}
        except Exception as exc:
            report(paths, ident, active=False, phase="failed", error=str(exc))
            raise


def remove(paths, ident, purge=False):
    preset(paths, ident)
    with locked(paths, "launcher-" + ident + "-schedule.lock"), locked(paths, "launcher-" + ident + ".lock"):
        if job_status(paths, ident).get("active"):
            raise Error("busy", "Wait for launcher installation to finish before removal")
        prefix = prefix_path(paths, ident)
        if purge:
            expected = (paths.data / "prefixes" / ("launcher-" + ident)).absolute()
            if prefix != expected or expected.is_symlink() or getattr(expected, "is_junction", lambda: False)():
                raise Error("unsafe_path", "Refusing to purge a redirected launcher prefix")
        with locked(paths):
            saved = registration(paths, ident)
            if saved:
                nonsteam.remove(paths, saved["slug"])
        # Python rmtree unlinks directory symlinks/junctions without following them.
        if purge and prefix.exists():
            shutil.rmtree(prefix)
        write_json(status_path(paths, ident), {})
    return {"id": ident, "removed": True, "prefix_preserved": not purge,
            "steam_changes_pending": bool(saved), "launcher": info(paths, ident)}
