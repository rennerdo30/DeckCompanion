import enum
import hashlib
import io
import os
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import types
import zipfile
import pytest
from deckcompanion import archives, cli, content, launch, localfix, net, recipes, steam
from deckcompanion.common import Error, atomic, read_json, state


def payload(paths, data=b"mod data"):
    sha = hashlib.sha256(data).hexdigest()
    atomic(paths.cache / "downloads" / sha, data)
    return sha


def mod_recipe(paths, data=b"mod data", **kwargs):
    mod = {"id": "test-mod", "name": "Test", "version": "1", "url": "https://example.org/m.zip",
           "sha256": payload(paths, data), "mode": "copy", "root": "prefix", "target": "pfx/mods/m.zip"}
    mod.update(kwargs)
    return {"mods": [mod]}


def reconcile(paths, recipe, prefix, prefs=None):
    log = []
    result = content.reconcile(paths, "42", recipe, prefix, None, prefs or {}, net.Budget(), log.append)
    return result, log


def test_mod_install_update_uninstall_and_user_changes(paths, tmp_path):
    prefix = tmp_path / "compatdata/42"
    (prefix / "pfx").mkdir(parents=True)
    recipe = mod_recipe(paths)
    assert reconcile(paths, recipe, prefix) == ({"test-mod": "1"}, [])
    target = prefix / "pfx/mods/m.zip"
    assert target.read_bytes() == b"mod data"
    manifest = read_json(paths.data / "manifests/42.json")
    assert manifest["mods"]["test-mod"]["files"] == [{"path": str(target), "sha256": payload(paths)}]
    updated = mod_recipe(paths, b"new", version="2")
    assert reconcile(paths, updated, prefix)[0] == {"test-mod": "2"}
    assert target.read_bytes() == b"new"
    target.write_bytes(b"user edit")
    result, log = reconcile(paths, updated, prefix, {"test-mod": False})
    assert result == {"test-mod": "2"} and "Preserving" in log[0]
    assert target.read_bytes() == b"user edit"
    target.write_bytes(b"new")
    assert reconcile(paths, updated, prefix, {"test-mod": False}) == ({}, [])
    assert not target.exists()
    assert read_json(paths.data / "manifests/42.json")["mods"] == {}


def test_zip_traversal_checksum_and_collision_rejected(paths, tmp_path, monkeypatch):
    prefix = tmp_path / "prefix"
    (prefix / "pfx").mkdir(parents=True)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as out:
        out.writestr("../escape", b"bad")
    recipe = mod_recipe(paths, stream.getvalue(), mode="extract_zip", target="pfx/mods")
    assert "Path must stay" in reconcile(paths, recipe, prefix)[1][0]
    assert not (prefix / "pfx/escape").exists()
    recipe = mod_recipe(paths)
    atomic(paths.cache / "downloads" / recipe["mods"][0]["sha256"], b"wrong")
    assert "checksum mismatch" in reconcile(paths, recipe, prefix)[1][0]
    recipe = mod_recipe(paths, b"correct")
    target = prefix / "pfx/mods/m.zip"
    atomic(target, b"existing user mod")
    assert "Preserving" in reconcile(paths, recipe, prefix)[1][0]
    assert target.read_bytes() == b"existing user mod"


def test_valid_zip_and_failure_restores_previous_manifest(paths, tmp_path, monkeypatch):
    prefix = tmp_path / "prefix"
    (prefix / "pfx").mkdir(parents=True)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as out:
        out.writestr("folder/file.txt", b"hello")
    recipe = mod_recipe(paths, stream.getvalue(), mode="extract_zip", target="pfx/mods")
    assert reconcile(paths, recipe, prefix)[0] == {"test-mod": "1"}
    target = prefix / "pfx/mods/folder/file.txt"
    assert target.read_bytes() == b"hello"
    original = (paths.data / "manifests/42.json").read_bytes()
    monkeypatch.setattr(content, "write_json", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    assert "disk full" in reconcile(paths, {"mods": []}, prefix)[1][0]
    assert target.read_bytes() == b"hello"
    assert (paths.data / "manifests/42.json").read_bytes() == original


def test_settings_once_and_prefix_defer(paths, tmp_path):
    prefix = tmp_path / "prefix"
    log = []
    recipe = {"settings": [{"id": "display", "target": "pfx/config.ini", "format": "ini", "section": "Video", "key": "Mode", "value": "deck"},
                           {"id": "xml", "target": "pfx/config.xml", "format": "xml", "xpath": "./display", "value": "deck"}]}
    content.settings(recipe, prefix, None, log.append)
    assert len(log) == 2 and not prefix.exists()
    (prefix / "pfx").mkdir(parents=True)
    atomic(prefix / "pfx/config.xml", '<settings><display>old</display><keep>yes</keep></settings>')
    content.settings(recipe, prefix, None, log.append)
    ini = prefix / "pfx/config.ini"
    assert "Mode = deck" in ini.read_text()
    assert "<display>deck</display>" in (prefix / "pfx/config.xml").read_text()
    ini.write_text("[Video]\nMode = user\n")
    content.settings(recipe, prefix, None, log.append)
    assert "user" in ini.read_text()
    assert (prefix / "pfx/config.xml.deckcompanion-original").exists()


def test_localfix_upstream_order_marker_and_foreign_file(paths, tmp_path, monkeypatch):
    package = types.ModuleType("protonfixes")
    package.__file__ = str(tmp_path / "protonfixes/__init__.py")
    upstream_path = tmp_path / "protonfixes/gamefixes-steam/42.py"
    atomic(upstream_path, "# upstream exists")
    calls = []
    util = types.SimpleNamespace(once=lambda **kw: lambda fn: fn,
        protontricks=lambda *a: calls.append(("tricks", *a)), regedit_add=lambda *a: calls.append(("reg", *a)),
        winedll_override=lambda *a: calls.append(("dll", *a)), set_environment=lambda *a: calls.append(("env", *a)),
        OverrideOrder=enum.Enum("OverrideOrder", {"NATIVE_BUILTIN": "n,b"}))
    monkeypatch.setitem(sys.modules, "protonfixes", package)
    monkeypatch.setitem(sys.modules, "protonfixes.util", util)
    upstream = types.SimpleNamespace(main=lambda: calls.append("upstream"), early=lambda: calls.append("early"))
    monkeypatch.setitem(sys.modules, "protonfixes.gamefixes-steam.42", upstream)
    recipe = {"wine": {"winetricks": ["win7"], "dll_overrides": {"d3dcompiler_47": "n,b"}, "env": {"A": "B"},
                       "registry": [{"key": "HKCU\\x", "name": "n", "type": "REG_SZ", "value": "v"}]}}
    file = localfix.generate(paths, "42", recipe)
    code = file.read_text()
    assert code.startswith(localfix.MARKER) and "gamefixes-steam.42" in code and "@util.once(retry=True)" in code
    namespace = {}
    exec(compile(code, str(file), "exec"), namespace)
    namespace["early"]()
    namespace["main"]()
    assert calls[:3] == ["early", "upstream", ("tricks", "win7")]
    assert calls[-1] == ("env", "A", "B")
    localfix.remove(paths, "42")
    assert not file.exists()
    atomic(file, "# user file\n")
    with pytest.raises(Error, match="Preserving"):
        localfix.generate(paths, "42", recipe)
    localfix.remove(paths, "42")
    assert file.read_text() == "# user file\n"


def test_http_deadline_kills_only_owned_worker(paths, monkeypatch):
    import importlib
    # Get the real callable without allowing any real subprocess/network access.
    real_fetch = importlib.reload(net).fetch
    class Process:
        pid = 98765
        returncode = None
        def __init__(self):
            self.waits, self.killed = [], False
        def wait(self, timeout):
            self.waits.append(timeout)
            if not self.killed:
                raise subprocess.TimeoutExpired("http", timeout)
            self.returncode = -1
        def kill(self):
            self.killed = True
    proc = Process()
    monkeypatch.setattr(net.subprocess, "Popen", lambda *a, **k: proc)
    with pytest.raises(Error, match="timed out"):
        real_fetch("https://example.org/file", paths.cache / "target")
    assert proc.killed and net.STARTED_PIDS[-1] == 98765
    assert 0 < proc.waits[0] <= 2 and proc.waits[1] == 2
    assert not (paths.cache / "target").exists()
    assert not list(paths.cache.glob(".download-*"))


def test_worker_headers_and_import_path(monkeypatch):
    from deckcompanion import net
    assert net.headers("https://api.github.com/repos/a/b/releases/latest")["Accept"] == "application/vnd.github+json"
    assert net.headers("https://api.github.com/repos/a/b/releases/assets/1")["Accept"] == "application/octet-stream"
    assert net.headers("https://github.com/a/b/releases/download/v1/x.zip")["Accept"] == "application/octet-stream"
    monkeypatch.delenv("PYTHONPATH", raising=False)
    root = str(Path(net.__file__).resolve().parents[1])
    assert net.worker_env()["PYTHONPATH"].split(os.pathsep)[0] == root


def test_launch_timeout_uses_installed_and_cached_mod(paths, tmp_path, monkeypatch):
    proton = paths.tools / "GE-Proton10-9"
    atomic(proton / "proton", "fake")
    recipe = mod_recipe(paths)
    recipe["runtime"] = {"proton": "GE-Proton11-11", "url": "https://example.org/proton.tar.gz", "sha256": "a"*64,
                         "env": {"CUSTOM": "value"}, "launch_options": ["--deck"]}
    monkeypatch.setattr(recipes, "load", lambda *a: recipe)
    calls = []
    def timeout(*a, **kw):
        calls.append(a)
        raise Error("network_timeout", "fake HTTP timeout")
    monkeypatch.setattr(net, "fetch", timeout)
    prefix = tmp_path / "prefix"
    (prefix / "pfx").mkdir(parents=True)
    args, env = launch.prepare(paths, "waitforexitandrun", ["game.exe", "argument with spaces", "$literal"],
        {"SteamAppId": "42", "STEAM_COMPAT_DATA_PATH": str(prefix)})
    assert args == [str(proton / "proton"), "waitforexitandrun", "game.exe", "argument with spaces", "$literal", "--deck"]
    assert env["CUSTOM"] == "value"
    assert (prefix / "pfx/mods/m.zip").read_bytes() == b"mod data"
    assert len(calls) == 1
    assert state(paths)["games"]["42"]["last_launch"]["ok"] is True
    assert "fake HTTP timeout" in (paths.data / "logs/42-last.log").read_text()


def test_first_launch_defers_content_and_exec_failure_marks_status(paths, tmp_path, monkeypatch):
    atomic(paths.tools / "GE-Proton11-11/proton", "fake")
    monkeypatch.setattr(recipes, "load", lambda *a: mod_recipe(paths))
    prefix = tmp_path / "new-prefix"
    env = {"SteamGameId": "42", "STEAM_COMPAT_DATA_PATH": str(prefix)}
    args, _ = launch.prepare(paths, "run", ["game.exe"], env)
    assert not prefix.exists()
    assert "Content root does not exist" in (paths.data / "logs/42-last.log").read_text()
    monkeypatch.setattr(launch, "prepare", lambda *a: (args, env))
    monkeypatch.setattr(launch.os, "execve", lambda *a: (_ for _ in ()).throw(OSError("not executable")))
    with pytest.raises(OSError):
        launch.launch(paths, "run", ["game.exe"])
    assert state(paths)["games"]["42"]["last_launch"]["ok"] is False


def test_archive_traversal_and_cached_proton_install(paths, tmp_path):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as out:
        info = tarfile.TarInfo("GE-Proton11-11/proton")
        info.size, info.mode = 4, 0o755
        out.addfile(info, io.BytesIO(b"fake"))
    sha = payload(paths, stream.getvalue())
    runtime = {"proton": "GE-Proton11-11", "url": "https://example.org/p.tar.gz", "sha256": sha}
    assert launch.resolve_proton(paths, {"runtime": runtime}, net.Budget(), lambda x: None) == paths.tools / "GE-Proton11-11"
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as out:
        info = tarfile.TarInfo("../escaped")
        out.addfile(info, io.BytesIO())
    source = tmp_path / "bad.tar"
    source.write_bytes(stream.getvalue())
    with pytest.raises(Error):
        archives.unpack(source, tmp_path / "extract")
    assert not (tmp_path / "escaped").exists()


def test_download_sha_mismatch_rejected(paths, monkeypatch):
    monkeypatch.setattr(net, "fetch", lambda url, target, budget: atomic(target, b"wrong"))
    with pytest.raises(Error, match="checksum mismatch"):
        net.cached(paths, "https://example.org/mod", "f"*64)
    assert not list((paths.cache / "downloads").glob("*"))
