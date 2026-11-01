import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
from types import SimpleNamespace
import pytest
from deckcompanion import cli, net, proton, recipes, steam, update
from deckcompanion.common import Error, atomic, locked, read_json, save_state, state, write_json

NAME = "GE-Proton11-11"
DOWNLOAD = net.download


@pytest.fixture
def ge(paths, monkeypatch):
    payloads = {}
    def release(name=NAME, algorithm="sha256", bad=False):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as out:
            member = tarfile.TarInfo("../escape" if bad else name + "-x86_64/proton")
            member.size, member.mode = 4, 0o755
            out.addfile(member, io.BytesIO(b"fake"))
        data = stream.getvalue()
        payloads[name] = data
        return {"name": name, "url": "https://example.org/" + name,
                "size": len(data), "algorithm": algorithm, "checksum": hashlib.new(algorithm, data).hexdigest()}
    latest = release()
    def metadata(p, name=None):
        result = latest if name is None else release(name)
        if name is None:
            write_json(p.cache / "ge/latest.json", result)
        return result
    def download(url, partial, size, status, budget):
        assert budget.remaining() > 2
        assert not (paths.tools / url.rsplit("/", 1)[-1] / "proton").exists()
        atomic(partial, payloads[url.rsplit("/", 1)[-1]])
    monkeypatch.setattr(proton, "metadata", metadata)
    monkeypatch.setattr(net, "download", download)
    monkeypatch.setattr(net, "fetch", lambda *a, **kw: (_ for _ in ()).throw(Error("offline", "mock offline")))
    monkeypatch.setattr(steam, "running", lambda: False)
    monkeypatch.setattr(proton.shutil, "disk_usage", lambda _: SimpleNamespace(free=20 * 1024**3))
    return SimpleNamespace(release=release, metadata=metadata, download=download, latest=latest, payloads=payloads)


def test_install_atomic_receipt_and_idempotence(paths, ge, monkeypatch):
    installed = proton.run(paths, "install", NAME)
    assert installed["state"] == "installed"
    assert (paths.tools / NAME / "proton").read_bytes() == b"fake"
    receipt = read_json(paths.data / "proton-installed.json")[NAME]
    assert receipt == read_json(paths.tools / NAME / ".deckcompanion-ge.json")
    assert receipt["sha256"] == hashlib.sha256(ge.payloads[NAME]).hexdigest()
    assert not list(paths.tools.glob(".dc-ge-*"))
    monkeypatch.setattr(net, "download", lambda *a: pytest.fail("already installed"))
    assert proton.run(paths, "install", NAME)["installed"] is True
    assert proton.listing(paths)["active"] is False
    entries = read_json(paths.data / "updates.json", {}).get("entries", [])
    assert [(e["module"], e["from"], e["to"]) for e in entries] == [("ge-proton", None, NAME)]


@pytest.mark.parametrize("mode", ["checksum", "archive", "disk", "interrupted", "conflict"])
def test_failures_preserve_target_and_report(paths, ge, monkeypatch, mode):
    if mode == "checksum":
        monkeypatch.setattr(net, "download", lambda u, p, *a: atomic(p, b"wrong"))
    if mode == "archive":
        bad = ge.release(bad=True)
        monkeypatch.setattr(proton, "metadata", lambda *a: bad)
    if mode == "disk":
        monkeypatch.setattr(proton.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    if mode == "interrupted":
        def interrupt(u, p, *a):
            atomic(p, b"partial")
            raise Error("network_timeout", "retry to resume")
        monkeypatch.setattr(net, "download", interrupt)
    if mode == "conflict":
        atomic(paths.tools / NAME / "user-file", b"keep")
    with pytest.raises((Error, OSError)):
        proton.run(paths, "install", NAME)
    assert not (paths.tools / NAME / "proton").exists()
    assert not (paths.tools / "escape").exists()
    assert read_json(proton.status_path(paths, NAME))["state"] == "failed"
    assert proton.listing(paths)["job"]["state"] == "failed"
    if mode == "interrupted":
        assert next((paths.cache / "ge/downloads").glob("*.part")).read_bytes() == b"partial"
    if mode == "conflict":
        assert (paths.tools / NAME / "user-file").read_bytes() == b"keep"


@pytest.mark.parametrize("correct_pin", [True, False])
def test_sha512_fallback_checks_real_sha512_and_recipe_sha256(paths, ge, monkeypatch, correct_pin):
    release = ge.release(algorithm="sha512")
    monkeypatch.setattr(proton, "metadata", lambda *a: release)
    save_state(paths, {"games": {"42": {"enabled": True}}})
    sha256 = hashlib.sha256(ge.payloads[NAME]).hexdigest()
    monkeypatch.setattr(recipes, "load", lambda *a: {"name": "Game", "runtime": {
        "proton": NAME, "sha256": sha256 if correct_pin else "0" * 64}})
    if not correct_pin:
        with pytest.raises(Error, match="checksum mismatch"):
            proton.run(paths, "install", NAME)
        assert not (paths.tools / NAME).exists()
        return
    assert proton.run(paths, "install", NAME)["sha256"] == hashlib.sha256(ge.payloads[NAME]).hexdigest()


def test_recipe_digest_disagreement_is_not_silently_ignored(paths, ge, monkeypatch):
    save_state(paths, {"games": {"42": {"enabled": True}}})
    monkeypatch.setattr(recipes, "load", lambda *a: {"name": "Game", "runtime": {"proton": NAME, "sha256": "0" * 64}})
    with pytest.raises(Error, match="differs"):
        proton.run(paths, "install", NAME)
    assert not (paths.tools / NAME).exists()


def test_update_installs_enabled_pins_and_latest_preserves_settings(paths, ge, monkeypatch):
    save_state(paths, {"games": {"2300320": {"enabled": True}, "42": {"enabled": False}}, "settings": {"splash": False}})
    real_load = recipes.load
    def load(p, ident):
        return {"name": "FS25", "runtime": {"proton": "GE-Proton11-10"}} if ident == "2300320" else real_load(p, ident)
    monkeypatch.setattr(recipes, "load", load)
    result = proton.run(paths, "update")
    assert {NAME, "GE-Proton11-10"} == {p.name for p in paths.tools.iterdir()}
    assert state(paths)["settings"] == {"splash": False}
    assert result["proton"]["errors"] == []
    entries = read_json(paths.data / "updates.json", {}).get("entries", [])
    assert {e["to"] for e in entries if e["module"] == "ge-proton"} == {NAME, "GE-Proton11-10"}


def test_auto_off_still_installs_enabled_pins_cleanup_off_preserves_old(paths, ge, monkeypatch):
    proton.run(paths, "install", "GE-Proton10-1")
    save_state(paths, {"games": {"2300320": {"enabled": True}}, "settings": {"auto_install_ge": False, "cleanup_old_ge": False}})
    monkeypatch.setattr(recipes, "load", lambda *a: {"name": "FS25", "runtime": {"proton": "GE-Proton11-10"}})
    result = proton.run(paths, "update")
    assert (paths.tools / "GE-Proton11-10/proton").exists()
    assert (paths.tools / "GE-Proton10-1/proton").exists()
    assert not (paths.tools / NAME).exists()
    assert result["proton"]["removed"] == []


def test_cleanup_only_our_unchanged_unneeded_installs(paths, ge, monkeypatch):
    for name in [NAME, "GE-Proton11-10", "GE-Proton11-9", "GE-Proton10-1", "GE-Proton10-2"]:
        proton.run(paths, "install", name)
    atomic(paths.tools / "GE-Proton11-9/proton", b"user edited")
    (paths.tools / "GE-Proton10-2/.deckcompanion-ge.json").unlink()
    atomic(paths.tools / "GE-Proton9-1/proton", b"external")
    save_state(paths, {"games": {"2300320": {"enabled": True}}})
    monkeypatch.setattr(recipes, "load", lambda *a: {"name": "FS25", "runtime": {"proton": "GE-Proton11-10"}})
    assert proton.sync(paths)["removed"] == ["GE-Proton10-1"]
    assert {p.name for p in paths.tools.iterdir()} == {NAME, "GE-Proton11-10", "GE-Proton11-9", "GE-Proton10-2", "GE-Proton9-1"}
    monkeypatch.setattr(steam, "running", lambda: True)
    assert proton.cleanup(paths, set()) == []


def test_offline_metadata_keeps_old_builds(paths, ge, monkeypatch):
    proton.run(paths, "install", "GE-Proton10-1")
    def offline(*a):
        raise Error("offline", "offline")
    monkeypatch.setattr(proton, "metadata", offline)
    assert proton.sync(paths)["errors"]
    assert (paths.tools / "GE-Proton10-1/proton").exists()


def test_stale_metadata_does_not_cleanup_or_foreign_arch_install_twice(paths, ge, monkeypatch):
    proton.run(paths, "install", "GE-Proton10-1")
    atomic(paths.tools / (NAME + "-x86_64") / "proton", b"external")
    monkeypatch.setattr(proton, "metadata", lambda *a: dict(ge.latest, stale=True))
    result = proton.sync(paths)
    assert result["errors"] and not result["removed"]
    assert not (paths.tools / NAME).exists()
    assert (paths.tools / "GE-Proton10-1/proton").exists()


def test_parallel_lock_does_not_block_polling_or_settings(paths, ge):
    with locked(paths, "proton.lock"):
        with pytest.raises(Error, match="Another"):
            proton.run(paths, "install", NAME)
        assert proton.listing(paths)["builds"] == []
        assert cli.main(["settings", "--set", "auto_install_ge=false", "--json"]) == 0


def test_detached_handoff_pins_engine_and_roots_and_bounds_lifetime(paths, ge, monkeypatch):
    calls = []
    monkeypatch.setattr(proton.sys, "platform", "linux")
    monkeypatch.setattr(proton.shutil, "which", lambda _: "/usr/bin/systemd-run")
    monkeypatch.setattr(steam, "run", lambda command, timeout: calls.append((command, timeout)))
    result = proton.schedule(paths, "install", NAME)
    command, timeout = calls[0]
    assert result["scheduled"] and timeout == 5
    assert "--user" in command and "--property=RuntimeMaxSec=3600" in command
    assert command[-4:] == ["proton-helper", "install", NAME, "--json"]
    assert repr(str(paths.tools)) in command[-5] and "deckcompanion.cli" in command[-5]
    with pytest.raises(Error, match="already"):
        proton.schedule(paths, "install", NAME)


def test_detach_failure_and_expired_worker_are_actionable(paths, ge, monkeypatch):
    monkeypatch.setattr(proton.sys, "platform", "linux")
    monkeypatch.setattr(proton.shutil, "which", lambda _: "/usr/bin/systemd-run")
    monkeypatch.setattr(steam, "run", lambda *a, **kw: (_ for _ in ()).throw(Error("failed", "user manager failed")))
    with pytest.raises(Error):
        proton.schedule(paths, "install", NAME)
    assert proton.listing(paths)["builds"][0]["state"] == "failed"
    write_json(paths.data / "proton-job.json", {"active": True, "started": 1})
    assert not proton.listing(paths)["active"]


@pytest.mark.parametrize("algorithm", ["sha256", "sha512"])
def test_upstream_metadata_selects_x86_archive_and_matching_checksum(paths, monkeypatch, algorithm):
    sha = "a" * (64 if algorithm == "sha256" else 128)
    def fetch(url, target, *a, **kw):
        if url.endswith(".sha512sum"):
            atomic(target, sha + "  " + NAME + "-x86_64.tar.gz\n")
        else:
            write_json(target, {"tag_name": NAME, "assets": [
                {"name": NAME + "-aarch64.tar.gz", "size": 200, "browser_download_url": "https://wrong"},
                {"name": NAME + "-x86_64.tar.gz", "size": 400, "browser_download_url": "https://archive",
                 "digest": "sha256:" + sha if algorithm == "sha256" else None},
                {"name": NAME + "-x86_64.sha512sum", "digest": "sha256:" + "f"*64,
                 "browser_download_url": "https://checksum.sha512sum"}]})
    monkeypatch.setattr(net, "fetch", fetch)
    result = proton.metadata(paths)
    assert result["url"] == "https://archive" and result["checksum"] == sha and result["algorithm"] == algorithm
    monkeypatch.setattr(net, "fetch", lambda *a, **kw: (_ for _ in ()).throw(Error("offline", "offline")))
    assert proton.metadata(paths) == dict(result, stale=True)


class Response(io.BytesIO):
    url = "https://example.org/archive"
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status, self.headers = status, headers or {}


@pytest.mark.parametrize("mode", ["range", "ignored", "bad_range", "oversized", "truncated", "insecure"])
def test_resumable_worker_validates_range_cap_tls_and_progress(paths, monkeypatch, mode):
    partial = paths.cache / "test.part"
    atomic(partial, b"abc")
    status = proton.status_path(paths, NAME)
    write_json(status, {"name": NAME})
    requests = []
    def open_url(request, timeout):
        requests.append((request, timeout))
        if mode in ("range", "bad_range"):
            return Response(b"def", 206, {"Content-Range": "bytes 3-5/6" if mode == "range" else "bytes 0-2/6"})
        result = Response(b"abcdefg" if mode == "oversized" else (b"ab" if mode == "truncated" else b"abcdef"))
        if mode == "insecure": result.url = "http://insecure"
        return result
    monkeypatch.setattr(net.urllib.request, "urlopen", open_url)
    if mode in ("range", "ignored"):
        net.resume_worker(Response.url, partial, 6, status)
        assert partial.read_bytes() == b"abcdef"
        assert read_json(status)["percent"] == 100
    else:
        with pytest.raises(ValueError):
            net.resume_worker(Response.url, partial, 6, status)
    assert requests[0][0].get_header("Range") == "bytes=3-" and requests[0][1] == 30


def test_download_supervisor_kills_only_its_worker_on_deadline(paths, monkeypatch):
    monkeypatch.setattr(net, "download", DOWNLOAD)
    events = []
    class Process:
        pid = 123456
        def wait(self, timeout):
            events.append(timeout)
            if len(events) == 1: raise subprocess.TimeoutExpired("worker", timeout)
        def kill(self): events.append("kill")
    monkeypatch.setattr(net.subprocess, "Popen", lambda *a, **k: Process())
    with pytest.raises(Error, match="retry to resume"):
        net.download("https://example.org", paths.cache / "a.part", 10, paths.cache / "status")
    assert events[0] > 2 and events[1:] == ["kill", 2]
    assert net.STARTED_PIDS[-1] == 123456
