import io
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import zipfile
import pytest
from deckcompanion import cli, decky, net
from deckcompanion.common import Error, atomic, read_json, write_json


@pytest.fixture
def plugins(paths, tmp_path, monkeypatch):
    monkeypatch.setattr(decky, "homebrew", lambda: tmp_path / "homebrew")
    monkeypatch.setattr(decky.sys, "platform", "linux")
    monkeypatch.setattr(decky.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(decky, "password_set", lambda: True)
    monkeypatch.setattr(decky, "latest", lambda p: {"version": "v3.3.1"})
    monkeypatch.setattr(decky, "restart_steam", lambda: None)
    monkeypatch.setattr(decky.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    def installed(directory, name="ProtonDB Badges", version="1.2.0"):
        root = decky.homebrew() / "plugins" / directory
        write_json(root / "plugin.json", {"name": name})
        write_json(root / "package.json", {"version": version})
        return root
    return installed


def bundle(name="ProtonDB Badges", version="1.3.4", bad=None):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("upstream-dir/plugin.json", json.dumps({"name": name}))
        archive.writestr("upstream-dir/package.json", json.dumps({"version": version}))
        archive.writestr("upstream-dir/dist/index.js", "new plugin")
        if bad == "symlink":
            entry = zipfile.ZipInfo("upstream-dir/redirect")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "/etc")
        elif bad:
            archive.writestr(bad, "escape")
    payload = data.getvalue()
    # Windows ZipInfo normalizes backslashes while writing; corrupt the raw
    # filename in both ZIP headers to exercise the wire format Linux receives.
    if bad and "\\" in bad:
        payload = payload.replace(bad.replace("\\", "/").encode(), bad.encode())
    return payload


def network(paths, monkeypatch, payload=None, sha=None, name="ProtonDB Badges", version="1.3.4"):
    import hashlib
    payload = bundle(name, version) if payload is None else payload
    sha = sha or hashlib.sha256(payload).hexdigest()
    calls = []
    def fetch(url, target, budget=None, max_bytes=None):
        calls.append(url)
        assert budget is not None and max_bytes <= 128 * 1024**2
        if url == decky.STORE_URL:
            write_json(target, [{"name": name, "versions": [{"name": version, "hash": sha},
                                                              {"name": "1.0", "hash": "0" * 64}]}])
        elif url == decky.INSTALLER_URL:
            atomic(target, "#!/bin/sh\n")
        else:
            atomic(target, payload)
    monkeypatch.setattr(net, "fetch", fetch)
    return calls


def password(tmp_path):
    path = tmp_path / "password"
    path.write_text("test-only-secret\n")
    return path


def root_process(monkeypatch, calls, secret):
    class Proc:
        pid = 97456
        returncode = 0
        def __init__(self, command, **kwargs):
            assert not secret.exists()
            assert "test-only-secret" not in str(command)
            assert kwargs["stdout"] == decky.subprocess.DEVNULL
            calls.append(command)
        def communicate(self, input=None, timeout=None):
            assert input == "test-only-secret\n" and timeout == 120
    monkeypatch.setattr(decky.subprocess, "Popen", Proc)


def test_plugin_matching_by_name_and_status_offline_cache(paths, plugins, monkeypatch):
    plugins("totally-different-dir")
    plugins("unknown", "Custom plugin")
    network(paths, monkeypatch)
    # Don't invoke systemctl for a status test.
    monkeypatch.setattr(decky.sys, "platform", "test")
    result = decky.status(paths)
    assert result["plugins"] == [
        {"dir": "totally-different-dir", "name": "ProtonDB Badges", "installed": "1.2.0", "latest": "1.3.4", "update_available": True},
        {"dir": "unknown", "name": "Custom plugin", "installed": "1.2.0", "latest": None, "update_available": False}]
    monkeypatch.setattr(decky.time, "time", lambda: 99999999999)
    monkeypatch.setattr(net, "fetch", lambda *a, **k: (_ for _ in ()).throw(Error("offline", "offline")))
    assert decky.status(paths)["plugins"] == result["plugins"]


@pytest.mark.parametrize("cache", ["not json", '[]', '{"plugins":[]}', '{"plugins":{"x":null}}'])
def test_plugin_status_survives_invalid_cache(paths, plugins, monkeypatch, cache):
    plugins("old")
    atomic(paths.cache / "decky-store.json", cache)
    monkeypatch.setattr(net, "fetch", lambda *a, **k: (_ for _ in ()).throw(Error("offline", "offline")))
    assert decky.plugins_status(paths)[0]["latest"] is None


@pytest.mark.parametrize("failure", ["hash", "../escape", "/absolute", "upstream-dir/../../escape", "upstream-dir/evil\\file", "symlink"])
def test_plugin_bad_hash_or_unsafe_archive_never_reaches_sudo(paths, plugins, tmp_path, monkeypatch, failure):
    root = plugins("old")
    network(paths, monkeypatch, bundle(bad=None if failure == "hash" else failure), sha="0" * 64 if failure == "hash" else None)
    monkeypatch.setattr(decky.subprocess, "Popen", lambda *a, **k: pytest.fail("unsafe archive reached sudo"))
    result = decky.update_plugins(paths, "test-only-secret")
    assert result["plugins_updated"] == [] and result["plugins_failed"][0]["name"] == "ProtonDB Badges"
    assert read_json(root / "package.json")["version"] == "1.2.0"
    assert not (tmp_path / "escape").exists()


def test_update_plugins_cli_deletes_password_and_restarts_only_after_change(paths, plugins, tmp_path, monkeypatch, caplog, capsys):
    plugins("old")
    calls = network(paths, monkeypatch)
    secret = password(tmp_path)
    root_calls, restarted = [], []
    root_process(monkeypatch, root_calls, secret)
    monkeypatch.setattr(decky, "restart_steam", lambda: restarted.append(True))
    assert cli.main(["decky", "update-plugins", "--password-file", str(secret), "--json"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output) == {"plugins_updated": ["ProtonDB Badges"], "plugins_failed": [], "steam_restarting": True}
    assert not secret.exists() and restarted == [True]
    assert "test-only-secret" not in output + caplog.text
    assert decky.INSTALLER_URL not in calls and root_calls
    assert root_calls[0][-3:-1] == ["ProtonDB Badges", "1.3.4"]
    assert 97456 in decky.steam.STARTED_PIDS
    # Equal/newer installed versions are never downgraded or restarted.
    plugins("old", version="2.0.0")
    result = decky.update_plugins_with_password(paths, password(tmp_path))
    assert result == {"plugins_updated": [], "plugins_failed": [], "steam_restarting": False}
    assert len(root_calls) == 1 and restarted == [True]


def test_plugin_failure_does_not_fail_loader_install(paths, plugins, tmp_path, monkeypatch):
    plugins("old")
    network(paths, monkeypatch, sha="0" * 64)
    restarted = []
    monkeypatch.setattr(decky, "restart_steam", lambda: restarted.append(True))
    secret = password(tmp_path)
    result = decky.install_with_password(paths, secret)
    assert not secret.exists() and restarted == [True]
    assert result["steam_restarting"] and result["plugins_updated"] == []
    assert result["plugins_failed"] == [{"name": "ProtonDB Badges", "error": "checksum_mismatch"}]
    assert "test-only-secret" not in (paths.data / "logs/decky-install.log").read_text()


def test_loader_updates_plugins_before_restart_and_continues_after_failure(paths, plugins, tmp_path, monkeypatch):
    plugins("a-bad")
    plugins("b-good", "Good", "1.0")
    network(paths, monkeypatch)
    good = bundle("Good", "2.0")
    import hashlib
    write_json(paths.cache / "decky-store.json", {"checked_at": decky.time.time(), "plugins": {
        "ProtonDB Badges": {"version": "1.3.4", "sha256": "0" * 64, "url": decky.STORE_CDN + "0" * 64 + ".zip"},
        "Good": {"version": "2.0", "sha256": hashlib.sha256(good).hexdigest(), "url": decky.STORE_CDN + hashlib.sha256(good).hexdigest() + ".zip"}}})
    monkeypatch.setattr(net, "fetch", lambda u, p, *a, **k: atomic(p, good if u.endswith(".zip") else "#!/bin/sh"))
    secret = password(tmp_path)
    calls = []
    root_process(monkeypatch, calls, secret)
    monkeypatch.setattr(decky, "restart_steam", lambda: calls.append("restart"))
    result = decky.install_with_password(paths, secret)
    assert result["plugins_updated"] == ["Good"] and len(result["plugins_failed"]) == 1
    assert calls[-1] == "restart" and len(calls) == 2


def test_deckcompanion_plugin_uses_independent_release_and_records_change(paths, plugins, tmp_path, monkeypatch):
    import hashlib
    plugins("custom-dir", "DeckCompanion", "0.1.0")
    payload = bundle("DeckCompanion", "0.2.0")
    release = {"version": "0.2.0", "url": "https://example.org/decky-v0.2.0/plugin.zip", "sha256": hashlib.sha256(payload).hexdigest()}
    def own_release(p):
        write_json(p.cache / "decky-plugin-release.json", release)
        return release
    monkeypatch.setattr(decky, "plugin_release", own_release)
    monkeypatch.setattr(net, "fetch", lambda u, p, *a, **k: atomic(p, payload) if u == release["url"] else pytest.fail("store used for own plugin"))
    secret = password(tmp_path)
    calls = []
    root_process(monkeypatch, calls, secret)
    result = decky.update_plugins_with_password(paths, secret)
    assert result["plugins_updated"] == ["DeckCompanion"]
    entry = read_json(paths.data / "updates.json")["entries"][0]
    assert (entry["module"], entry["from"], entry["to"]) == ("decky-plugin", "0.1.0", "0.2.0")


def test_root_helper_rechecks_hash_and_preserves_previous_directory(tmp_path, monkeypatch):
    from deckcompanion import decky_archive
    target = tmp_path / "plugins/old"
    atomic(target / "keep", "old bytes")
    archive = tmp_path / "download.zip"
    atomic(archive, bundle())
    with pytest.raises(ValueError, match="SHA256"):
        decky_archive.replace(archive, target, "ProtonDB Badges", "1.3.4", "0" * 64)
    assert (target / "keep").read_text() == "old bytes"
    assert not list(target.parent.glob(".dc-plugin-*"))
    # Exercise snapshot/extraction/ownership/activation without a real sudo or Linux syscall.
    ownership = []
    monkeypatch.setattr(decky_archive.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(decky_archive.os, "chown", lambda p, u, g: ownership.append((p, u, g)), raising=False)
    def exchange(left, right):
        backup = right.with_name("exchange-temp")
        right.rename(backup)
        left.rename(right)
        backup.rename(left)
    monkeypatch.setattr(decky_archive, "exchange", exchange)
    decky_archive.replace(archive, target, "ProtonDB Badges", "1.3.4", net.digest(archive))
    assert read_json(target / "package.json")["version"] == "1.3.4"
    assert next(target.parent.glob(".dc-plugin-*/unpack/upstream-dir/keep")).read_text() == "old bytes"
    assert ownership and all(u == g == 0 for _, u, g in ownership)


def test_update_plugins_missing_password_file_is_required(paths):
    with pytest.raises(Error, match="password-file"):
        cli.execute(cli.parser().parse_args(["decky", "update-plugins"]), paths)


@pytest.mark.parametrize("args", [["status"], ["update-plugins", "--appid", "42"]])
def test_rejected_password_arguments_still_delete_private_file(paths, tmp_path, args):
    secret = password(tmp_path)
    with pytest.raises(Error, match="password-file"):
        cli.execute(cli.parser().parse_args(["decky", *args, "--password-file", str(secret)]), paths)
    assert not secret.exists()


@pytest.mark.parametrize("mode", ["failure", "timeout"])
def test_root_failure_keeps_plugin_and_never_restarts_or_exposes_inputs(paths, plugins, tmp_path, monkeypatch, mode):
    root = plugins("old")
    network(paths, monkeypatch)
    secret = password(tmp_path)
    killed = []
    class Proc:
        pid = 97457
        returncode = 1
        def __init__(self, command, **kwargs):
            assert not secret.exists()
        def communicate(self, input=None, timeout=None):
            if mode == "timeout" and input:
                raise decky.subprocess.TimeoutExpired("test-only-secret", timeout)
        def kill(self):
            killed.append(True)
    monkeypatch.setattr(decky.subprocess, "Popen", Proc)
    monkeypatch.setattr(decky, "restart_steam", lambda: pytest.fail("failed plugin triggered restart"))
    result = decky.update_plugins_with_password(paths, secret)
    assert result == {"plugins_updated": [], "plugins_failed": [{"name": "ProtonDB Badges", "error":
                      "plugin_timeout" if mode == "timeout" else "plugin_install_failed"}], "steam_restarting": False}
    assert "test-only-secret" not in str(result)
    assert read_json(root / "package.json")["version"] == "1.2.0"
    assert bool(killed) == (mode == "timeout")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux atomic directory exchange")
def test_root_helper_real_atomic_exchange(tmp_path):
    from deckcompanion import decky_archive
    target = tmp_path / "plugins/old"
    atomic(target / "keep", "old")
    archive = tmp_path / "plugin.zip"
    atomic(archive, bundle())
    decky_archive.replace(archive, target, "ProtonDB Badges", "1.3.4", net.digest(archive))
    assert read_json(target / "package.json")["version"] == "1.3.4"
    assert next(target.parent.glob(".dc-plugin-*/unpack/upstream-dir/keep")).read_text() == "old"
