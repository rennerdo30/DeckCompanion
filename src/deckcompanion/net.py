"""Bounded HTTP in an owned subprocess: DNS and trickling responses cannot stall launch."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from .common import Error

STARTED_PIDS = []


class Budget:
    def __init__(self, seconds=2):
        self.deadline = time.monotonic() + seconds

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Error("network_timeout", "Launch network budget exhausted; using installed content")
        return min(2, remaining)


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def fetch(url, target, budget=None, max_bytes=1024 * 1024 * 1024):
    if not url.startswith("https://"):
        raise Error("unsafe_url", "Downloads require HTTPS")
    budget = budget or Budget()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".download-", dir=target.parent)
    os.close(fd)
    proc = None
    try:
        proc = subprocess.Popen([sys.executable, "-m", "deckcompanion.net", url, name, str(max_bytes)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        STARTED_PIDS.append(proc.pid)
        try:
            proc.wait(timeout=budget.remaining())
        except (subprocess.TimeoutExpired, Error) as exc:
            proc.kill()
            proc.wait(timeout=2)
            raise Error("network_timeout", "Download timed out; keeping cached content") from exc
        if proc.returncode:
            raise Error("download_failed", "Download failed; keeping cached content")
        os.replace(name, target)
        return target
    finally:
        Path(name).unlink(missing_ok=True)


def cached(paths, url, sha256, budget=None):
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise Error("unresolved_asset", "Asset has no resolved SHA256; run update first")
    target = paths.cache / "downloads" / sha256
    if target.exists():
        if digest(target) == sha256:
            return target
        raise Error("checksum_mismatch", "Cached asset checksum mismatch")
    partial = target.with_suffix(".part")
    try:
        fetch(url, partial, budget)
        if digest(partial) != sha256:
            raise Error("checksum_mismatch", "Downloaded asset checksum mismatch")
        os.replace(partial, target)
        return target
    finally:
        partial.unlink(missing_ok=True)


def worker(url, target, max_bytes):
    request = urllib.request.Request(url, headers={"User-Agent": "DeckCompanion/0.2", "Accept": "application/octet-stream"})
    with urllib.request.urlopen(request, timeout=2) as response, open(target, "wb") as output:
        if not response.url.startswith("https://"):
            raise ValueError("Insecure redirect")
        total = 0
        while block := response.read(128 * 1024):
            total += len(block)
            if total > max_bytes:
                raise ValueError("Download exceeds size limit")
            output.write(block)


if __name__ == "__main__":
    worker(sys.argv[1], sys.argv[2], int(sys.argv[3]))
