"""Bounded HTTP in an owned subprocess: DNS and trickling responses cannot stall launch."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import re
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
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=worker_env())
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


def worker_env():
    """The worker must import this package even when the caller only extended sys.path."""
    env = dict(os.environ)
    root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(p for p in (root, env.get("PYTHONPATH", "")) if p)
    return env


def headers(url):
    # GitHub's REST API answers 415 to Accept: application/octet-stream on JSON endpoints.
    api = url.startswith("https://api.github.com/") and "/releases/assets/" not in url
    return {"User-Agent": "DeckCompanion/0.2",
            "Accept": "application/vnd.github+json" if api else "application/octet-stream"}


def worker(url, target, max_bytes):
    request = urllib.request.Request(url, headers=headers(url))
    with urllib.request.urlopen(request, timeout=2) as response, open(target, "wb") as output:
        if not response.url.startswith("https://"):
            raise ValueError("Insecure redirect")
        total = 0
        while block := response.read(128 * 1024):
            total += len(block)
            if total > max_bytes:
                raise ValueError("Download exceeds size limit")
            output.write(block)


class DownloadBudget(Budget):
    def __init__(self, seconds=3600):
        super().__init__(seconds)

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Error("network_timeout", "Proton download budget exhausted; retry to resume")
        return remaining


def download(url, partial, size, status, budget=None):
    """Supervise DNS, connect and reads; leave partial bytes for a verified retry."""
    if not url.startswith("https://") or not 0 < size <= 2 * 1024**3:
        raise Error("invalid_download", "Expected HTTPS archive no larger than 2 GiB")
    budget = budget or DownloadBudget()
    partial.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([sys.executable, "-m", "deckcompanion.net", "--resume", url,
                             str(partial), str(size), str(status)], env=worker_env(),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    STARTED_PIDS.append(proc.pid)
    try:
        proc.wait(timeout=budget.remaining())
    except (subprocess.TimeoutExpired, Error) as exc:
        proc.kill()
        proc.wait(timeout=2)
        raise Error("network_timeout", "Proton download timed out; retry to resume") from exc
    if proc.returncode:
        raise Error("download_failed", "Proton transfer interrupted; retry to resume")


def resume_worker(url, partial, size, status):
    from .common import read_json, write_json
    if not url.startswith("https://") or not 0 < size <= 2 * 1024**3:
        raise ValueError("Invalid download")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > size:
        partial.unlink()
        offset = 0
    if offset == size:
        return
    request_headers = headers(url)
    if offset:
        request_headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        if not response.url.startswith("https://"):
            raise ValueError("Insecure redirect")
        if response.status == 206:
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
            if not match or tuple(map(int, match.groups())) != (offset, size - 1, size):
                raise ValueError("Invalid resume response")
        elif response.status == 200:
            offset = 0  # Server ignored Range: safely restart instead of appending.
        else:
            raise ValueError("Unexpected response status")
        receipt = read_json(status, {})
        last = 0
        with partial.open("ab" if offset else "wb") as output:
            while block := response.read(128 * 1024):
                offset += len(block)
                if offset > size:
                    raise ValueError("Download exceeds declared size")
                output.write(block)
                if time.monotonic() - last >= 0.5 or offset == size:
                    write_json(status, dict(receipt, state="downloading", downloaded_bytes=offset,
                                           total_bytes=size, percent=int(offset * 100 / size)))
                    last = time.monotonic()
        if offset != size:
            raise ValueError("Incomplete download")


if __name__ == "__main__":
    if sys.argv[1] == "--resume":
        resume_worker(sys.argv[2], Path(sys.argv[3]), int(sys.argv[4]), Path(sys.argv[5]))
    else:
        worker(sys.argv[1], sys.argv[2], int(sys.argv[3]))
