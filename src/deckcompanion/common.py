"""Paths, durable writes and a single-writer lock. No runtime dependencies."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile


class Error(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def now():
    return datetime.now(timezone.utc).isoformat()


def app_id(value):
    if not re.fullmatch(r"[1-9][0-9]{0,19}", str(value)):
        raise Error("invalid_appid", "Expected a numeric Steam appid")
    return str(value)


def component(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise Error("invalid_name", "Invalid version or identifier")
    return value


def inside(root, relative):
    # Reject symlink redirects outside a root; manifests only own contained files.
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise Error("unsafe_path", "Expected a portable relative path")
    if any(p in ("", ".", "..") for p in relative.split("/")):
        raise Error("unsafe_path", "Path must stay inside its declared root")
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise Error("unsafe_path", f"Rejected path {relative!r}: resolves to {target}, which escapes declared root {root.resolve()}; symlink redirects outside the root are not allowed")
    return target


@dataclass(frozen=True)
class Paths:
    data: Path
    cache: Path
    config: Path
    steam: Path
    tools: Path
    recipes: Path

    @classmethod
    def env(cls):
        def path(key, default):
            value = os.environ.get(key, str(default))
            if not value.strip():
                raise Error("invalid_path", f"{key} must not be empty")
            return Path(value).expanduser().resolve()
        home = Path.home()
        share = path("XDG_DATA_HOME", home / ".local/share")
        data = path("DECKCOMPANION_HOME", share / "deckcompanion")
        steam = path("DECKCOMPANION_STEAM_ROOT", os.environ.get("STEAM_COMPAT_CLIENT_INSTALL_PATH", str(share / "Steam")))
        return cls(data, path("DECKCOMPANION_CACHE", data / "cache"),
                   path("DECKCOMPANION_CONFIG", path("XDG_CONFIG_HOME", home / ".config") / "protonfixes"),
                   steam, path("DECKCOMPANION_COMPAT_TOOLS", steam / "compatibilitytools.d"),
                   path("DECKCOMPANION_RECIPES", Path(__file__).resolve().parents[2] / "recipes"))


def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".dc-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content.encode("utf-8") if isinstance(content, str) else content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, obj):
    atomic(path, json.dumps(obj, indent=2, sort_keys=True) + "\n")


@contextmanager
def locked(paths, name="state.lock"):
    paths.data.mkdir(parents=True, exist_ok=True)
    with (paths.data / name).open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                if not lock.read(1):
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Error("busy", "Another DeckCompanion command is writing state") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def state(paths):
    return read_json(paths.data / "state.json", {"games": {}, "shortcut_pending": False})


def save_state(paths, value):
    write_json(paths.data / "state.json", value)
