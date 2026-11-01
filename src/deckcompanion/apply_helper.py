"""Linux apply worker, independent of the Steam/UI process tree. Stdlib only."""
import os
from pathlib import Path
import shutil
import sys
import time
from . import steam
from .common import Error, locked, now, write_json


def gamescope_running():
    # Linux comm is truncated to 15 characters for start-gamescope-session.
    for path in Path("/proc").glob("[0-9]*/comm"):
        try:
            name = path.read_text().strip()
            # SteamOS 3.9 runs the compositor as "gamescope-wl".
            if name in ("gamescope", "gamescope-wl") or name.startswith("start-gamescope"):
                return True
        except FileNotFoundError:
            continue
    return False


def game_mode():
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    return ("gamescope" in desktop or "steam" in desktop or
            any(os.environ.get(key, "").lower() in ("1", "true", "yes")
                for key in ("SteamDeck", "SteamGamepadUI")) or gamescope_running())


def unit_name():
    return "deckcompanion-apply-" + str(time.time_ns())


def helper_command(paths):
    # Pin this engine, not the mutable current pointer; also works from a checkout
    # after systemd discards cwd/PYTHONPATH. Forward only the documented roots/user.
    root = Path(__file__).resolve().parents[2]
    env = {"DECKCOMPANION_" + key: str(value) for key, value in (
        ("HOME", paths.data), ("CACHE", paths.cache), ("CONFIG", paths.config),
        ("STEAM_ROOT", paths.steam), ("COMPAT_TOOLS", paths.tools), ("RECIPES", paths.recipes))}
    if "DECKCOMPANION_STEAM_USER_ID" in os.environ:
        env["DECKCOMPANION_STEAM_USER_ID"] = os.environ["DECKCOMPANION_STEAM_USER_ID"]
    bootstrap = (f"import os,sys;os.environ.update({env!r});"
                 f"sys.path[:0]={[str(root / 'src'), str(root)]!r};"
                 "from deckcompanion.cli import main;sys.exit(main())")
    return [sys.executable, "-c", bootstrap, "apply-helper", *(["--game-mode"] if game_mode() else [])]


def double_fork(command):
    """Fallback only when systemd-run is absent; supervisor bounds the helper."""
    child = os.fork()
    if child:
        steam.STARTED_PIDS.append(child)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            pid, status = os.waitpid(child, os.WNOHANG)
            if pid:
                if status:
                    raise Error("detach_failed", "Cannot start detached apply supervisor")
                return
            time.sleep(0.01)
        raise Error("process_timeout", "Detached apply launcher did not exit")
    try:
        os.setsid()
        detached = os.fork()
        if detached:
            steam.STARTED_PIDS.append(detached)
            os._exit(0)
        os.chdir("/")
        with open(os.devnull, "r+b", buffering=0) as null:
            for fd in (0, 1, 2):
                os.dup2(null.fileno(), fd)
        os.closerange(3, os.sysconf("SC_OPEN_MAX"))
        steam.run(command, timeout=300)
    except Exception:
        os._exit(1)
    finally:
        os._exit(0)


def schedule(paths):
    unit = unit_name()
    command = helper_command(paths)
    runner = shutil.which("systemd-run")
    if runner:
        # A present but failing user manager is an error, never a silent fallback.
        steam.run([runner, "--user", "--unit=" + unit, "--collect",
                   "--property=RuntimeMaxSec=300", "--", *command], timeout=5)
    else:
        double_fork(command)
    return {"applied": [], "scheduled": True, "steam_restarted": False, "helper": unit}


def wait_until(predicate, seconds):
    deadline = time.monotonic() + seconds
    while True:
        if predicate():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.25, remaining))


def restore(mode):
    if mode:
        # Use live processes, not the inherited Game Mode environment: that env
        # remains true even after gamescope has fallen back to Desktop Mode.
        if wait_until(lambda: steam.running() and gamescope_running(), 10):
            return
        selector = shutil.which("steamos-session-select") or shutil.which("holo-session-select")
        if not selector:
            raise Error("session_select_missing", "Cannot restore Game Mode: session selector missing")
        steam.run([selector, "gamescope"], timeout=15)
        ready = lambda: steam.running() and gamescope_running()
    else:
        runner = shutil.which("systemd-run")
        if runner:
            steam.run([runner, "--user", "--unit=" + unit_name() + "-steam", "--collect",
                       "--", "steam", "-silent"], timeout=5)
        else:
            # Bound startup with the same double-fork supervisor; Steam is
            # intentionally long-lived once its normal launcher has handed off.
            root = Path(__file__).resolve().parents[2]
            double_fork([sys.executable, "-c", f"import sys;sys.path[:0]={[str(root / 'src'), str(root)]!r};"
                         "from deckcompanion.steam import start;start()"])
        ready = steam.running
    if not wait_until(ready, 10):
        raise Error("steam_start_failed", "Steam/session did not return within 10 seconds")


def apply_pending(paths):
    # Queue commands can briefly hold the state lock; retry only lock acquisition.
    deadline = time.monotonic() + 10
    while True:
        with_lock = locked(paths)
        try:
            with_lock.__enter__()
        except Error as exc:
            if exc.code != "busy" or time.monotonic() >= deadline:
                raise
            time.sleep(0.25)
            continue
        try:
            return steam.apply(paths)
        finally:
            with_lock.__exit__(None, None, None)


def apply_helper(paths, was_game_mode=False):
    # Losers must not shut down Steam or overwrite the active helper's receipt.
    with locked(paths, "apply.lock"):
        started = time.monotonic()
        result = {"pid": os.getpid(), "started_at": now(), "applied": [], "errors": [],
                  "timings": {}, "steam_restarted": False, "game_mode": False}
        log = paths.data / "logs/apply-last.json"
        write_json(log, result)
        shutdown = False
        closed = False
        try:
            result["game_mode"] = was_game_mode or game_mode()
            phase = time.monotonic()
            if steam.running():
                shutdown = True
                # steam -shutdown itself can take ~40 s on SteamOS.
                steam.run(["steam", "-shutdown"], timeout=120)
            closed = wait_until(lambda: not steam.running(), max(0, 120 - (time.monotonic() - phase)))
            result["timings"]["shutdown"] = time.monotonic() - phase
            if not closed:
                raise Error("steam_shutdown_timeout", "Steam did not exit within 120 seconds; nothing written")
            phase = time.monotonic()
            result["applied"] = apply_pending(paths)["applied"]
            result["timings"]["apply"] = time.monotonic() - phase
        except Exception as exc:
            result["errors"].append({"code": getattr(exc, "code", "operation_failed"), "message": str(exc)})
        finally:
            # Restore after a failed disk write as well, but never switch sessions
            # if Steam never closed. Persist disk results before session switching.
            write_json(log, result)
            should_restore = closed
            if shutdown and not closed:
                try:
                    should_restore = not steam.running()
                except Exception as exc:
                    result["errors"].append({"code": getattr(exc, "code", "operation_failed"), "message": str(exc)})
            if should_restore:
                phase = time.monotonic()
                try:
                    restore(result["game_mode"])
                    result["steam_restarted"] = True
                except Exception as exc:
                    result["errors"].append({"code": getattr(exc, "code", "operation_failed"), "message": str(exc)})
                result["timings"]["restore"] = time.monotonic() - phase
            result.update(finished_at=now())
            result["timings"]["total"] = time.monotonic() - started
            write_json(log, result)
        return result
