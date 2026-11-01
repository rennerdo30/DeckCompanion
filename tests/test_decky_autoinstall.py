import hashlib
import importlib.util
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace
import pytest
from deckcompanion import cli, decky, decky_api, decky_archive, install, net
from deckcompanion.common import Error, atomic, read_json, save_state, write_json
from test_decky_updates import bundle, password, plugins


def builder():
    spec = importlib.util.spec_from_file_location("autoinstall_build", Path(__file__).resolve().parents[1] / "scripts/build_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_bundles_plugin_hash_and_installer_keeps_it(tmp_path, built_plugin, monkeypatch):
    build = builder()
    result = build.build(tmp_path / "out", offline=True)
    with tarfile.open(result["archive"]) as archive:
        metadata = json.load(archive.extractfile("bundled/decky/plugin.json"))
        payload = archive.extractfile("bundled/decky/" + metadata["file"]).read()
    assert payload == built_plugin.read_bytes()
    assert metadata == {"version": "0.1.0", "file": built_plugin.name,
                        "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
    root = tmp_path / "installed-release"
    atomic(root / "bundled/decky/plugin.json", json.dumps(metadata))
    atomic(root / "bundled/decky" / metadata["file"], payload)
    assert set(install.files(root)) == {"bundled/decky/plugin.json", "bundled/decky/" + metadata["file"]}


def test_release_missing_frontend_fails_before_archive(tmp_path, monkeypatch):
    build = builder()
    monkeypatch.setattr(build, "ROOT", tmp_path / "unbuilt")
    with pytest.raises(FileNotFoundError, match="npm ci and npm run build"):
        build.build(tmp_path / "out")
    assert not list((tmp_path / "out").glob("*.tar.gz"))


@pytest.fixture
def bundled(paths, plugins, tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    payload = bundle("DeckCompanion", "0.1.0")
    name = "deckcompanion-decky-0.1.0.zip"
    atomic(root / name, payload)
    write_json(root / "plugin.json", {"version": "0.1.0", "sha256": hashlib.sha256(payload).hexdigest(),
                                      "file": name, "size": len(payload)})
    monkeypatch.setattr(decky, "BUNDLED_PLUGIN", root, raising=False)
    monkeypatch.setattr(decky, "plugin_release", lambda p: {"source": "unavailable", "stale": True})
    atomic(decky.homebrew() / "services/PluginLoader", "fixture")
    (decky.homebrew() / "plugins").mkdir(exist_ok=True)
    monkeypatch.setattr(decky.os, "access", lambda *a: False)
    return root / name


def test_status_root_owned_offline_bundle_and_newest_channel(paths, bundled, monkeypatch):
    result = decky.plugin_status(paths)
    assert {key: result[key] for key in ("installed", "version", "available_version", "source", "methods", "can_install")} == {
        "installed": False, "version": None, "available_version": "0.1.0", "source": "bundled",
        "methods": ["decky_api", "password"], "can_install": True}
    assert not result["plugins_writable"]
    channel = {"version": "0.2.0", "sha256": "a" * 64, "url": "https://example.org/plugin.zip"}
    monkeypatch.setattr(decky, "plugin_release", lambda p: channel)
    assert decky.plugin_candidate(paths)["source"] == "channel"
    channel["version"] = "0.0.9"
    assert decky.plugin_candidate(paths)["source"] == "bundled"
    channel["version"] = "0.1.0"
    assert decky.plugin_candidate(paths)["source"] == "bundled"


def test_api_authenticated_http_uses_existing_websocket_and_exact_store_args(monkeypatch):
    calls, closed = [], []
    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("127.0.0.1", 1337, 5)
        def request(self, method, route, body, headers):
            self.route = route
            self.body = json.loads(body) if body else None
            calls.append((method, route, self.body, headers))
        def getresponse(self):
            if self.route == "/auth/token":
                payload = b"fixture-token"
            else:
                value = True if self.body["code"].startswith("Boolean(") else "deckcompanion-requested"
                payload = json.dumps({"success": True, "result": {"success": True, "result": value}}).encode()
            return SimpleNamespace(status=200, read=lambda size: payload)
        def close(self):
            closed.append(True)
    monkeypatch.setattr(decky_api, "HTTPConnection", Connection)
    decky_api.install("file:///bundle/plugin.zip", "0.1.0", "a" * 64)
    assert len(calls) == len(closed) == 3
    assert calls[0][:3] == ("GET", "/auth/token", None)
    assert calls[1][3]["X-Decky-Auth"] == "fixture-token"
    assert calls[2][1] == "/methods/execute_in_tab"
    payload = calls[2][2]
    assert payload["tab"] == "SharedJSContext" and payload["run_async"] is True
    assert "window.DeckyBackend.call('utilities/install_plugin', ..." in payload["code"]
    assert json.dumps(["file:///bundle/plugin.zip", "DeckCompanion", "0.1.0", "a" * 64, 0]) in payload["code"]
    assert "confirm_plugin_install" not in payload["code"]


@pytest.mark.parametrize("mode", ["timeout", "bad_json", "too_large", "denied"])
def test_api_transport_closes_and_sanitizes_failures(monkeypatch, mode):
    closed = []
    class Connection:
        def __init__(self, *a, **k): pass
        def request(self, *a, **k):
            if mode == "timeout":
                raise TimeoutError("fixture-token")
        def getresponse(self):
            return SimpleNamespace(status=403 if mode == "denied" else 200,
                read=lambda size: b"x" * (decky_api.MAX_RESPONSE + 1) if mode == "too_large" else b"fixture-token")
        def close(self): closed.append(True)
    monkeypatch.setattr(decky_api, "HTTPConnection", Connection)
    with pytest.raises(Error) as failure:
        decky_api.request("POST", "/methods/execute_in_tab", {}, "fixture-token")
    assert "fixture-token" not in str(failure.value)
    assert closed == [True]


def test_api_unavailable_frontend_does_not_submit_or_claim_request(monkeypatch):
    calls = []
    def request(method, route, payload=None, token=None):
        calls.append(route)
        return "fixture-token" if route == "/auth/token" else {"success": True, "result": {"success": False}}
    monkeypatch.setattr(decky_api, "request", request)
    with pytest.raises(Error, match="Steam frontend"):
        decky_api.install("file:///bundle.zip", "0.1.0", "a" * 64)
    assert len(calls) == 1 + len(decky_api.TABS)


def test_api_probes_other_tab_after_legacy_500_but_never_resubmits(monkeypatch):
    calls = []
    monkeypatch.setattr(decky_api, "request", lambda *a: "fixture-token")
    def execute(tab, code, token):
        calls.append((tab, code))
        if tab == decky_api.TABS[0]:
            raise Error("decky_api_unavailable", "fixture legacy 500")
        if code.startswith("Boolean("):
            return True
        raise Error("decky_api_unavailable", "fixture lost reply")
    monkeypatch.setattr(decky_api, "execute", execute)
    with pytest.raises(Error) as failure:
        decky_api.install("file:///bundle.zip", "0.1.0", "a" * 64)
    assert failure.value.code == "decky_api_request_failed"
    assert [tab for tab, code in calls] == [decky_api.TABS[0], decky_api.TABS[1], decky_api.TABS[1]]


def test_lost_api_ack_never_falls_through_to_file_install(paths, bundled, monkeypatch):
    monkeypatch.setattr(decky.os, "access", lambda *a: True)
    def fail(*a):
        raise Error("decky_api_request_failed", "Check Steam")
    monkeypatch.setattr(decky_api, "install", fail)
    monkeypatch.setattr(decky, "_plugin_install_writable", lambda *a: pytest.fail("Duplicate installation"))
    with pytest.raises(Error) as failure:
        decky.plugin_install(paths)
    assert failure.value.code == "decky_api_request_failed"


@pytest.mark.parametrize("confirmed", [False, True])
def test_api_install_retains_verified_file_and_polls_honestly(paths, bundled, monkeypatch, confirmed):
    calls = []
    def api(artifact, version, sha, install_type):
        calls.append((artifact, version, sha, install_type))
        if confirmed:
            write_json(decky.homebrew() / "plugins/DeckCompanion/package.json", {"version": version})
            write_json(decky.homebrew() / "plugins/DeckCompanion/plugin.json", {"name": "DeckCompanion"})
    monkeypatch.setattr(decky_api, "install", api)
    clock = [0]
    monkeypatch.setattr(decky.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(decky.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    result = decky.plugin_install(paths)
    assert (result["method"], result["requested"], result["confirm_in_steam"]) == ("decky_api", True, True)
    assert result["installed"] is confirmed and result["changed"] is confirmed
    assert clock[0] == (0 if confirmed else 15)
    sha = net.digest(bundled)
    snapshot = paths.cache / "decky-artifacts" / (sha + ".zip")
    assert snapshot.is_file() and net.digest(snapshot) == sha
    assert calls == [(snapshot.resolve().as_uri(), "0.1.0", sha, 0)]
    if not confirmed:
        assert result["reason"] == "confirmation_pending"


def test_bundle_hash_failure_never_calls_decky(paths, bundled, monkeypatch):
    atomic(bundled, b"tampered")
    monkeypatch.setattr(decky_api, "install", lambda *a: pytest.fail("Unverified artifact reached Decky"))
    with pytest.raises(Error, match="SHA256"):
        decky.plugin_install(paths)


def test_newer_channel_url_and_update_install_type(paths, bundled, plugins, monkeypatch):
    plugins("DeckCompanion", "DeckCompanion", "0.1.0")
    payload = bundle("DeckCompanion", "0.2.0")
    sha = hashlib.sha256(payload).hexdigest()
    url = "https://example.org/deckcompanion-decky-0.2.0.zip"
    monkeypatch.setattr(decky, "plugin_release", lambda p: {"version": "0.2.0", "sha256": sha, "url": url})
    monkeypatch.setattr(net, "fetch", lambda u, p, *a, **k: atomic(p, payload))
    calls = []
    def api(*args):
        calls.append(args)
        write_json(decky.homebrew() / "plugins/DeckCompanion/package.json", {"version": "0.2.0"})
    monkeypatch.setattr(decky_api, "install", api)
    result = decky.plugin_install(paths)
    assert result["source"] == "channel" and result["changed"] and result["version"] == "0.2.0"
    assert calls == [(url, "0.2.0", sha, 2)]


def test_newer_installed_version_never_downgrades_to_bundle(paths, bundled, plugins, monkeypatch):
    plugins("DeckCompanion", "DeckCompanion", "0.3.0")
    monkeypatch.setattr(decky_api, "install", lambda *a: pytest.fail("Downgrade requested"))
    result = decky.plugin_install(paths)
    assert not result["changed"] and not result["requested"] and result["version"] == "0.3.0"


def test_stale_channel_cache_cannot_choose_local_file(paths, bundled, monkeypatch):
    write_json(paths.cache / "decky-plugin-release.json", {"version": "0.2.0", "sha256": "a" * 64,
               "url": "https://example.org/plugin.zip", "path": "fixture-private-path"})
    assert "path" not in decky.plugin_candidate(paths, refresh=False)


def sudo_process(monkeypatch, secret, calls):
    monkeypatch.setattr(decky_archive.os, "geteuid", lambda: 1000, raising=False)
    class Proc:
        pid, returncode = 87654, 0
        def __init__(self, command, **kwargs):
            assert not secret.exists()
            assert "test-only-secret" not in str(command)
            assert kwargs["stdout"] == kwargs["stderr"] == decky.subprocess.DEVNULL
            calls.append(command)
            self.restart = command[-3:] == ["systemctl", "restart", "plugin_loader"]
            # Execute only the root helper logic in an isolated fixture directory.
            if not self.restart:
                decky_archive.replace(*command[-5:])
        def communicate(self, input, timeout):
            assert input == "test-only-secret\n" and timeout == (30 if self.restart else 120)
    monkeypatch.setattr(decky.subprocess, "Popen", Proc)


def test_password_install_deletes_secret_activates_and_restarts_loader(paths, bundled, tmp_path, monkeypatch, capsys, caplog):
    secret, calls, restarts = password(tmp_path), [], []
    sudo_process(monkeypatch, secret, calls)
    def run(command, input, **kwargs):
        assert not secret.exists() and input == "test-only-secret\n"
        assert "test-only-secret" not in str(command)
        restarts.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(decky.subprocess, "run", run)
    monkeypatch.setattr(decky, "restart_steam", lambda: pytest.fail("Must only restart Loader"))
    assert cli.main(["decky", "plugin-install", "--password-file", str(secret), "--json"]) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["method"] == "password" and result["installed"] and result["loader_restarted"]
    assert not secret.exists() and "test-only-secret" not in output + caplog.text
    assert calls[-1][-3:] == ["systemctl", "restart", "plugin_loader"]
    assert read_json(decky.homebrew() / "plugins/DeckCompanion/package.json")["version"] == "0.1.0"
    assert 87654 in decky.steam.STARTED_PIDS


@pytest.mark.parametrize("command", ["install", "update-plugins"])
@pytest.mark.parametrize("enabled", [False, True])
def test_loader_and_plugin_updates_auto_install_missing_panel(paths, bundled, tmp_path, monkeypatch, command, enabled):
    secret, calls = password(tmp_path), []
    save_state(paths, {"games": {}, "settings": {} if enabled else {"decky_plugin_auto": False}})
    sudo_process(monkeypatch, secret, calls)
    monkeypatch.setattr(net, "fetch", lambda url, target, *a, **k: atomic(target, "#!/bin/sh") if url == decky.INSTALLER_URL else pytest.fail(url))
    result = cli.execute(cli.parser().parse_args(["decky", command, "--password-file", str(secret)]), paths)
    assert not secret.exists()
    assert result["plugins_updated"] == (["DeckCompanion"] if enabled else [])
    assert result["plugins_failed"] == []
    assert bool(calls) is enabled
    assert (decky.homebrew() / "plugins/DeckCompanion/plugin.json").exists() is enabled


def test_auto_setting_default_and_cli_off(paths):
    args = cli.parser().parse_args(["settings", "--json"])
    assert cli.execute(args, paths)["decky_plugin_auto"] is True
    args = cli.parser().parse_args(["settings", "--set", "decky_plugin_auto=off", "--json"])
    assert cli.execute(args, paths)["decky_plugin_auto"] is False
    assert decky.auto_plugin(paths) is False
