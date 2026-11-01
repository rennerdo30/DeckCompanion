"""Optional, bounded pre-Proton presentation. Nothing here may prevent handoff."""
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from .common import now, state

STARTED_PIDS = []
MAX_SECONDS = 10.0
HOLD_SECONDS = 120
HARD_TIMEOUT = 130
DEFAULTS = {"splash": True, "splash_seconds": 4.0,
            "show_games_without_recipe": True, "check_updates_on_start": True,
            "auto_install_ge": True, "cleanup_old_ge": True, "takeover_all": True}


def settings(data):
    saved = data.get("settings", {})
    seconds = float(saved.get("splash_seconds", DEFAULTS["splash_seconds"]))
    if not math.isfinite(seconds):
        seconds = DEFAULTS["splash_seconds"]
    return {**saved, **{key: saved.get(key, default) for key, default in DEFAULTS.items()},
            "splash_seconds": max(1.0, min(MAX_SECONDS, seconds))}


def log(paths, ident, message):
    try:
        target = paths.data / "logs" / f"{ident if str(ident).isdigit() else 'unknown'}-last.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as output:
            output.write(f"{now()} Splash: {message}\n")
    except Exception:
        try:
            print(f"DeckCompanion splash: {message}", file=sys.stderr)
        except Exception:
            pass


def show(paths, payload, env):
    ident = payload.get("appid", "unknown")
    try:
        options = settings(state(paths))
        if not options["splash"]:
            return
        if sys.platform.startswith("linux") and not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
            raise RuntimeError("no display; skipped")
        configured = env.get("DECKCOMPANION_UI", str(paths.data / "bin/deckcompanion-ui"))
        binary = shutil.which(configured) or str(Path(configured).expanduser())
        payload = dict(payload, duration_seconds=options["splash_seconds"])
        # Close before spawning: Godot must be able to read it on Windows too.
        with tempfile.TemporaryDirectory(prefix="deckcompanion-splash-") as directory:
            file = Path(directory) / "splash.json"
            file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with (Path(directory) / "ui.log").open("w+b") as output:
                proc = subprocess.Popen([binary, "--splash", str(file)], env=env,
                                        stdout=output, stderr=output)
                STARTED_PIDS.append(proc.pid)
                log(paths, ident, f"started PID {proc.pid}")
                try:
                    code = proc.wait(timeout=min(HARD_TIMEOUT, options["splash_seconds"] + HOLD_SECONDS + 2))
                except subprocess.TimeoutExpired:
                    proc.kill()  # Only the exact subprocess created above.
                    proc.wait(timeout=2)
                    raise RuntimeError(f"PID {proc.pid} timed out and was killed")
                if code:
                    raise RuntimeError(f"PID {proc.pid} exited {code}")
    except Exception as exc:
        log(paths, ident, f"{exc}; continuing launch")
