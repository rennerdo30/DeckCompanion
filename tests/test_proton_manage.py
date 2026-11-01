"""DC-27 public regressions, also run unchanged against c949b7a."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from deckcompanion import cli, net, proton, recipes, steam
from deckcompanion.common import Error, atomic, locked, read_json, save_state, state, write_json

NAME = "GE-Proton11-10"


def release(name=NAME, published="2026-10-01T10:00:00Z", sha=True):
    archive = name + "-x86_64.tar.gz"
    assets = [{"name": archive, "size": 123, "browser_download_url": "https://example.org/" + archive,
               "digest": "sha256:" + "a" * 64 if sha else None}]
    if not sha:
        assets.append({"name": name + "-x86_64.sha512sum", "size": 200,
                       "browser_download_url": "https://example.org/check.sha512sum"})
    return {"tag_name": name, "published_at": published, "assets": assets}


def call(capsys, *args, code=0):
    assert cli.main(["proton", *args, "--json"]) == code
    return json.loads(capsys.readouterr().out)


def test_available_exact_json_pagination_order_and_installed_refresh(paths, monkeypatch, capsys):
    pages = [[release(), release("GE-Proton11-11", "2026-11-01T10:00:00Z", False)], []]
    calls = []
    def fetch(url, target, budget, **kw):
        query = parse_qs(urlsplit(url).query)
        assert url.startswith(proton.API + "?") and query["per_page"] == ["2"]
        assert 0 < budget.remaining() <= 2
        calls.append(url)
        write_json(target, pages[int(query["page"][0]) - 1])
    monkeypatch.setattr(net, "fetch", fetch)
    result = call(capsys, "available", "--per-page", "2")
    assert set(result) == {"builds", "source", "page", "per_page", "has_more", "next_page",
                           "fetched_at", "cached", "stale", "warning"}
    assert result["builds"] == [
        {"name": "GE-Proton11-11", "published_at": "2026-11-01T10:00:00Z", "size": 123,
         "digest_available": False, "checksum_algorithm": "sha512", "installed": False},
        {"name": NAME, "published_at": "2026-10-01T10:00:00Z", "size": 123,
         "digest_available": True, "checksum_algorithm": "sha256", "installed": False}]
    assert result["next_page"] == 2 and result["has_more"] and not result["cached"]
    atomic(paths.tools / (NAME + "-x86_64") / "proton", b"installed")
    again = call(capsys, "available", "--per-page", "2")
    assert again["cached"] and again["builds"][1]["installed"] and len(calls) == 1
    last = call(capsys, "available", "--page", "2", "--per-page", "2")
    assert last["builds"] == [] and not last["has_more"] and last["next_page"] is None
    assert len(calls) == 2


def test_available_six_hour_expiry_cached_fallback_and_no_cache_error(paths, monkeypatch, capsys):
    clock = [100000]
    monkeypatch.setattr(proton.time, "time", lambda: clock[0])
    monkeypatch.setattr(net, "fetch", lambda u, p, *a, **kw: write_json(p, [release()]))
    first = call(capsys, "available")
    def offline(*a, **kw):
        raise Error("network_timeout", "mock two-second timeout")
    monkeypatch.setattr(net, "fetch", offline)
    clock[0] += 6 * 3600 - 1
    fresh = call(capsys, "available")
    assert fresh["cached"] and not fresh["stale"] and fresh["warning"] is None
    clock[0] += 1
    stale = call(capsys, "available")
    assert stale["stale"] and stale["cached"] and "two-second" in stale["warning"]
    assert stale["builds"] == first["builds"] and stale["fetched_at"] == first["fetched_at"]
    assert call(capsys, "available", "--page", "2", code=1)["error"]["code"] == "ge_available_failed"


def test_available_filters_unsafe_unverifiable_and_wrong_architecture(paths, monkeypatch, capsys):
    good = release("Proton-6.21-GE-2")
    draft, pre, arm, no_checksum, unsafe, oversized = [release() for _ in range(6)]
    draft["draft"], pre["prerelease"] = True, True
    arm["assets"][0]["name"] = NAME + "-aarch64.tar.gz"
    no_checksum["assets"][0]["digest"] = None
    unsafe["assets"][0]["browser_download_url"] = "http://example.org/archive"
    oversized["assets"][0]["size"] = 3 * 1024**3
    monkeypatch.setattr(net, "fetch", lambda u, p, *a, **kw:
                        write_json(p, [draft, pre, arm, no_checksum, unsafe, oversized, good]))
    assert [b["name"] for b in call(capsys, "available")["builds"]] == ["Proton-6.21-GE-2"]


@pytest.mark.parametrize("name,sha", [(NAME, True), ("Proton-6.21-GE-2", False)])
def test_any_listed_release_installs_through_verified_resumable_worker(paths, monkeypatch, capsys, name, sha):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as out:
        member = tarfile.TarInfo(name + "-x86_64/proton")
        member.size, member.mode = 4, 0o755
        out.addfile(member, io.BytesIO(b"fake"))
    payload = stream.getvalue()
    item = release(name, sha=sha)
    item["assets"][0].update(size=len(payload), digest="sha256:" + hashlib.sha256(payload).hexdigest() if sha else None)
    calls = []
    def fetch(url, target, *a, **kw):
        calls.append(url)
        if url.endswith(".sha512sum"):
            atomic(target, hashlib.sha512(payload).hexdigest() + "  " + name + "-x86_64.tar.gz\n")
        else:
            write_json(target, [item] if "?" in url else item)
    monkeypatch.setattr(net, "fetch", fetch)
    monkeypatch.setattr(net, "download", lambda u, p, *a: atomic(p, payload))
    monkeypatch.setattr(proton.shutil, "disk_usage", lambda _: SimpleNamespace(free=20 * 1024**3))
    listed = call(capsys, "available")["builds"][0]
    installed = call(capsys, "install", listed["name"])
    assert installed["state"] == "installed" and installed["sha256"] == hashlib.sha256(payload).hexdigest()
    assert "/tags/" + name in calls[1]
    assert call(capsys, "available")["builds"][0]["installed"] is True


@pytest.fixture
def removable(paths, monkeypatch):
    atomic(paths.tools / NAME / "proton", b"proton")
    atomic(paths.tools / NAME / "files/bin/wine", b"wine")
    monkeypatch.setattr(steam, "proton_running", lambda _: False, raising=False)
    monkeypatch.setattr(steam, "game_running", lambda _: False)
    return paths.tools / NAME


def test_disk_usage_external_owned_and_valve_builds(paths, removable, capsys):
    valve = paths.steam / "steamapps/common/Proton 10.0"
    atomic(valve / "proton", b"Valve Proton")
    # An outdated ownership receipt must not hide user-added bytes.
    write_json(paths.data / "proton-installed.json", {NAME: {"installed_bytes": 1}})
    builds = {b["name"]: b for b in call(capsys)["builds"]}
    assert builds[NAME]["installed_bytes"] == 10 and builds[NAME]["removable"] is True
    assert builds["Proton 10.0"]["installed_bytes"] == 12 and builds["Proton 10.0"]["removable"] is False


def test_remove_external_build_records_state_and_returns_updated_listing(paths, removable, capsys):
    save_state(paths, {"games": {}, "settings": {"splash": False}, "foreign": ["keep"]})
    write_json(paths.data / "proton-installed.json", {NAME: {"installed_bytes": 10}})
    proton.report(paths, NAME, "installed")
    result = call(capsys, "remove", NAME)
    assert set(result) == {"builds", "active", "job", "status_file", "removed", "warnings"}
    assert result["removed"] == [NAME] and result["warnings"] == []
    assert not removable.exists() and result["builds"] == []
    saved = state(paths)
    assert saved["settings"] == {"splash": False} and saved["foreign"] == ["keep"]
    assert saved["proton_removals"][NAME] == {"state": "removed", "path": str(removable),
        "removed_at": saved["proton_removals"][NAME]["removed_at"], "forced": False, "references": []}
    assert read_json(paths.data / "proton-installed.json") == {}
    assert not proton.status_path(paths, NAME).exists()


@pytest.mark.parametrize("kind", ["recipe_pin", "game_override"])
def test_references_refuse_until_force_and_preserve_choices(paths, removable, monkeypatch, capsys, kind):
    save_state(paths, {"games": {"42": {"proton_override": NAME}}} if kind == "game_override" else {"games": {}})
    monkeypatch.setattr(recipes, "ids", lambda _: {"42"} if kind == "recipe_pin" else set())
    monkeypatch.setattr(recipes, "load", lambda *a: {"name": "Pinned game", "runtime": {"proton": NAME}}
                        if kind == "recipe_pin" else None)
    before = state(paths)
    refused = call(capsys, "remove", NAME, code=1)
    assert refused["error"]["code"] == "proton_in_use" and kind in refused["error"]["message"]
    assert removable.exists() and state(paths) == before
    forced = call(capsys, "remove", NAME, "--force")
    assert forced["warnings"][0]["kind"] == kind and not removable.exists()
    assert state(paths)["games"] == before["games"]


@pytest.mark.parametrize("condition", ["runtime", "game", "unknown", "lock", "scheduled"])
def test_remove_never_forces_running_unknown_or_busy_build(paths, removable, monkeypatch, capsys, condition):
    if condition == "runtime":
        monkeypatch.setattr(steam, "proton_running", lambda _: True)
    if condition == "game":
        save_state(paths, {"games": {"42": {"proton_override": NAME}}})
        monkeypatch.setattr(steam, "game_running", lambda _: True)
    if condition == "unknown":
        def unknown(_):
            raise Error("game_state_unknown", "unreadable process")
        monkeypatch.setattr(steam, "proton_running", unknown)
    if condition == "scheduled":
        write_json(paths.data / "proton-job.json", {"active": True, "started": proton.time.time()})
    if condition == "lock":
        with locked(paths, "proton.lock"):
            result = call(capsys, "remove", NAME, "--force", code=1)
    else:
        result = call(capsys, "remove", NAME, "--force", code=1)
    assert result["error"]["code"] == {"runtime": "proton_running", "game": "proton_running",
        "unknown": "game_state_unknown", "lock": "busy", "scheduled": "busy"}[condition]
    assert removable.exists() and "proton_removals" not in state(paths)


@pytest.mark.parametrize("name", ["Proton 10.0", "../common", "GE-Proton99-1"])
def test_remove_never_touches_steam_managed_or_missing_targets(paths, monkeypatch, capsys, name):
    valve = paths.steam / "steamapps/common/Proton 10.0/proton"
    atomic(valve, b"Valve")
    monkeypatch.setattr(steam, "proton_running", lambda _: pytest.fail("not removable"), raising=False)
    result = call(capsys, "remove", name, "--force", code=1)
    assert result["error"]["code"] == "proton_not_removable" and valve.read_bytes() == b"Valve"


def test_remove_refuses_tools_root_pointing_into_steam_depots(paths, monkeypatch, capsys):
    common = paths.steam / "steamapps/common"
    atomic(common / NAME / "proton", b"keep")
    monkeypatch.setenv("DECKCOMPANION_COMPAT_TOOLS", str(common))
    result = call(capsys, "remove", NAME, "--force", code=1)
    assert result["error"]["code"] == "proton_not_removable"
    assert (common / NAME / "proton").read_bytes() == b"keep"


def test_remove_unlinks_nested_links_preserves_their_targets_and_refuses_root_alias(paths, removable, capsys):
    outside = paths.steam / "steamapps/common/protected"
    atomic(outside / "precious", b"keep")
    (removable / "linked-directory").symlink_to(outside, target_is_directory=True)
    alias = paths.tools / "GE-Proton99-1"
    alias.symlink_to(removable, target_is_directory=True)
    refused = call(capsys, "remove", alias.name, "--force", code=1)
    assert refused["error"]["code"] == "proton_not_removable"
    assert removable.is_dir()
    result = call(capsys, "remove", NAME)
    assert result["removed"] == [NAME] and (outside / "precious").read_bytes() == b"keep"
    assert alias.is_symlink()  # Removing the requested build does not remove other aliases.


@pytest.mark.parametrize("source", ["args", "environment", "exe", "unavailable"])
def test_runtime_process_detection_checks_actual_paths_not_substrings(paths, tmp_path, source):
    proc = tmp_path / "proc"
    pid = proc / "123"
    pid.mkdir(parents=True)
    target = paths.tools / NAME
    atomic(pid / "cmdline", b"wine\0")
    atomic(pid / "environ", b"SteamAppId=42\0")
    if source == "unavailable":
        with pytest.raises(Error) as caught:
            steam.proton_running(target, proc_root=proc / "missing")
        assert caught.value.code == "game_state_unknown"
        return
    if source == "args":
        atomic(pid / "cmdline", str(target / "proton").encode() + b"\0run\0")
    if source == "environment":
        atomic(pid / "environ", b"PROTONPATH=" + str(target).encode() + b"\0")
    if source == "exe":
        atomic(target / "files/bin/wine", b"wine")
        (pid / "exe").symlink_to(target / "files/bin/wine")
    assert steam.proton_running(target, proc_root=proc)
    assert not steam.proton_running(target.with_name(NAME + "0"), proc_root=proc)


def test_automatic_cleanup_preserves_explicit_game_override(paths, monkeypatch):
    root = paths.tools / NAME
    atomic(root / "proton", b"proton")
    receipt = {"files": proton.inventory(root)}
    write_json(root / ".deckcompanion-ge.json", receipt)
    write_json(paths.data / "proton-installed.json", {NAME: receipt})
    save_state(paths, {"games": {"42": {"proton_override": NAME}}})
    monkeypatch.setattr(steam, "running", lambda: False)
    assert proton.cleanup(paths, set()) == []
    assert root.is_dir()


def test_proton_running_skips_processes_hiding_exe_and_environ(tmp_path, monkeypatch):
    # On the Deck gamescope and sandbox helpers hide exe/environ; that must not block removal.
    from pathlib import Path as _Path
    real_detector = steam.__dict__["proton_running"]
    target = tmp_path / "tools" / "GE-Proton11-7"
    (target / "files").mkdir(parents=True)
    proc = tmp_path / "proc"
    hidden, user = proc / "100", proc / "200"
    for process in (hidden, user):
        process.mkdir(parents=True)
        (process / "environ").write_bytes(b"")
    (hidden / "cmdline").write_bytes(b"gamescope-wl\0")
    (user / "cmdline").write_bytes(b"other\0")
    real = _Path.read_bytes
    def read_bytes(self):
        if self == hidden / "environ":
            raise PermissionError("undumpable")
        return real(self)
    monkeypatch.setattr(_Path, "read_bytes", read_bytes)
    assert real_detector(target, proc) is False
    (user / "cmdline").write_bytes(str(target / "files" / "bin" / "wine").encode() + b"\0")
    assert real_detector(target, proc) is True
