"""Offline regressions for making DeckCompanion the default compatibility tool."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import types
import urllib.request

import pytest
import vdf
from deckcompanion import cli, content, install, launch, localfix, steam
from deckcompanion.common import atomic, save_state, state

ROOT = Path(__file__).resolve().parents[1]


def call(capsys, *args):
    code = cli.main([*args, "--json"])
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "release"
    for name in ("src/deckcompanion/__main__.py", "vdf/__init__.py", "bin/deckcompanion-ui"):
        atomic(root / name, b"fake payload")
    for name in ("compat/DeckCompanion", "compatibilitytool.vdf", "toolmanifest.vdf"):
        atomic(root / name, (ROOT / name).read_bytes())
    if hasattr(install.os, "geteuid"):
        monkeypatch.setattr(install.os, "geteuid", lambda: 1000)
    return root


@pytest.mark.parametrize("previous", [None, {"name": "proton_10", "config": "custom", "priority": "90"}])
def test_default_queue_apply_restore(paths, monkeypatch, capsys, previous):
    monkeypatch.setattr(steam, "running", lambda: False)
    doc = {"other": "keep"}
    steam.mappings(doc)
    if previous:
        steam.mappings(doc)["0"] = previous
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    before = steam.mapping_path(paths).read_bytes()
    code, result = call(capsys, "default", "--on")
    assert code == 0, result
    assert result["default_tool"] is False
    assert {"action": "default_on"} in result["pending"]
    assert steam.mapping_path(paths).read_bytes() == before
    assert steam.apply(paths)["applied"] == [{"action": "default_on"}]
    assert steam.mappings(steam.read_vdf(steam.mapping_path(paths)))["0"] == {
        "name": "DeckCompanion", "config": "", "priority": "250"}
    assert call(capsys, "status")[1]["default_tool"] is True
    # Re-enabling must preserve the original restoration entry.
    call(capsys, "default", "--on")
    steam.apply(paths)
    assert call(capsys, "default", "--off")[1]["pending"] == [{"action": "default_off"}]
    steam.apply(paths)
    assert steam.read_vdf(steam.mapping_path(paths)) == doc


def test_install_queues_default_and_uninstall_restores(paths, source, monkeypatch):
    monkeypatch.setattr(steam, "running", lambda: True)
    doc = {}
    prior = {"name": "proton_experimental", "config": "old"}
    steam.mappings(doc)["0"] = prior
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    assert install.install(paths, source)["steam_changes_pending"] is True
    assert state(paths).get("default_pending") == "default_on"
    assert steam.read_vdf(steam.mapping_path(paths)) == doc
    monkeypatch.setattr(steam, "running", lambda: False)
    steam.apply(paths)
    install.uninstall(paths)
    assert steam.read_vdf(steam.mapping_path(paths)) == doc
    assert "0" not in state(paths)["games"]


def test_default_preserves_foreign_change_and_rolls_back(paths, monkeypatch, capsys):
    monkeypatch.setattr(steam, "running", lambda: False)
    assert call(capsys, "default", "--on")[0] == 0
    real_save = steam.save_state
    monkeypatch.setattr(steam, "save_state", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        steam.apply(paths)
    assert not steam.mapping_path(paths).exists()
    assert state(paths)["default_pending"] == "default_on"
    monkeypatch.setattr(steam, "save_state", real_save)
    steam.apply(paths)
    doc = {}
    steam.mappings(doc)["0"] = {"name": "foreign"}
    atomic(steam.mapping_path(paths), vdf.dumps(doc))
    call(capsys, "default", "--off")
    steam.apply(paths)
    assert steam.read_vdf(steam.mapping_path(paths)) == doc


def test_installed_proton_version_order(paths):
    for name in ("GE-Proton9-99", "UMU-Proton-10.0-4", "GE-Proton10-0", "GE-Proton11-11", "GE-Proton11-7-x86_64",
                 "DeckCompanion", "SteamLinuxRuntime_sniper", "LegacyRuntime", "OtherTool99"):
        atomic(paths.tools / name / "proton", "fake")
    for name in ("Proton 10.0", "Proton 11.0", "Proton - Experimental", "Proton 12.0"):
        if name != "Proton 12.0":
            atomic(paths.steam / "steamapps/common" / name / "proton", "fake")
        else:
            (paths.steam / "steamapps/common" / name).mkdir()
    assert [p["name"] for p in steam.proton(paths)] == [
        "GE-Proton11-11", "GE-Proton11-7-x86_64", "Proton - Experimental", "Proton 11.0", "UMU-Proton-10.0-4",
        "GE-Proton10-0", "Proton 10.0", "GE-Proton9-99"]


def test_proton_alias_resolves_once(paths):
    atomic(paths.tools / "GE-Proton11-11/proton", "fake")
    try:
        (paths.tools / "Proton-GE Latest").symlink_to(paths.tools / "GE-Proton11-11", target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    result = steam.proton(paths)
    assert result == [{"name": "GE-Proton11-11", "path": str((paths.tools / "GE-Proton11-11").resolve())}]


@pytest.mark.parametrize("failure", ["state", "recipe", "content", "localfix", "save", "lock", "library", "log"])
def test_engine_errors_still_exec_original_arguments(paths, monkeypatch, failure):
    atomic(paths.tools / "GE-Proton9-9/proton", "fake")
    atomic(paths.steam / "steamapps/common/Proton 10.0/proton", "fake")
    monkeypatch.setattr(launch.os, "environ", {"SteamAppId": "42"})
    real_load = launch.recipes.load
    monkeypatch.setattr(launch.recipes, "load", lambda *a: {"runtime": {"proton": "Proton 10.0", "env": {"EXTRA": "recipe"}, "launch_options": ["recipe-option"]}})
    def fail(*a, **kw):
        if failure == "localfix":
            raise OSError("localfix write denied")
        raise RuntimeError("injected engine problem")
    if failure == "state":
        atomic(paths.data / "state.json", "{corrupt")
    elif failure == "recipe":
        monkeypatch.setattr(launch.recipes, "load", real_load)
        atomic(paths.cache / "recipes/42.toml", "[invalid")
    elif failure == "content":
        monkeypatch.setattr(content, "reconcile", fail)
    elif failure == "localfix":
        monkeypatch.setattr(localfix, "generate", fail)
    elif failure == "save":
        monkeypatch.setattr(launch, "save_state", fail)
    elif failure == "lock":
        monkeypatch.setattr(launch, "locked", fail)
    elif failure == "library":
        atomic(paths.steam / "steamapps/libraryfolders.vdf", '"bad" {')
    elif failure == "log":
        atomic(paths.data / "logs", "not a directory")
    calls = []
    monkeypatch.setattr(launch.os, "execve", lambda exe, args, env: calls.append((exe, args, env)))
    command = ["game with spaces.exe", "--json", "$literal", "semi;colon"]
    launch.launch(paths, "waitforexitandrun", command)
    assert len(calls) == 1
    assert calls[0][1] == [str(paths.steam / "steamapps/common/Proton 10.0/proton"), "waitforexitandrun", *command]
    assert calls[0][2].get("EXTRA") is None


def test_pinned_recipe_keeps_pin_and_no_recipe_logs_newest(paths, monkeypatch):
    atomic(paths.tools / "GE-Proton9-9/proton", "fake")
    atomic(paths.tools / "GE-Proton/proton", "fake")
    atomic(paths.steam / "steamapps/common/Proton 10.0/proton", "fake")
    monkeypatch.setattr(launch.recipes, "load", lambda *a: {"runtime": {"proton": "GE-Proton9-9"}})
    assert "GE-Proton9-9" in launch.prepare(paths, "run", ["game.exe"], {"SteamAppId": "42"})[0][0]
    monkeypatch.setattr(launch.recipes, "load", lambda *a: {"runtime": {"proton": "GE-Proton99-1"}})
    assert "GE-Proton9-9" in launch.prepare(paths, "run", ["game.exe"], {"SteamAppId": "42"})[0][0]
    monkeypatch.setattr(launch.recipes, "load", lambda *a: None)
    assert "Proton 10.0" in launch.prepare(paths, "run", ["game.exe"], {"SteamAppId": "42"})[0][0]
    assert "Proton 10.0" in (paths.data / "logs/42-last.log").read_text()


def test_no_proton_logs_clear_error(paths):
    from deckcompanion.common import Error
    with pytest.raises(Error) as failed:
        launch.prepare(paths, "run", ["game.exe"], {"SteamAppId": "42"})
    assert failed.value.code == "no_proton"
    assert "No installed Proton available" in (paths.data / "logs/42-last.log").read_text()


def test_install_activates_validated_release_index(paths, source, monkeypatch):
    from deckcompanion import __version__, recipes
    from deckcompanion.common import write_json
    monkeypatch.setattr(steam, "running", lambda: False)
    recipe = 'schema_version=1\nappid="42"\nname="Downloaded recipe"\nversion="1"\n'
    index = {"schema_version": 1, "engine": {"version": __version__, "url": "https://example.org/engine", "sha256": "a" * 64},
             "recipes": {"42": {"toml": recipe, "sha256": hashlib.sha256(recipe.encode()).hexdigest()}}}
    write_json(source / "index.json", index)
    install.install(paths, source)
    assert recipes.load(paths, "42")["name"] == "Downloaded recipe"


def test_shortcut_stable_icon_artwork_and_uninstall(paths, source, monkeypatch):
    monkeypatch.setattr(steam, "running", lambda: False)
    for name in ("portrait", "wide", "hero", "logo", "icon"):
        atomic(source / f"assets/steam/{name}.png", name.encode())
    install.install(paths, source)
    shortcut_path = steam.shortcut_path(paths)
    real_save = steam.save_state
    monkeypatch.setattr(steam, "save_state", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        steam.apply(paths)
    assert not shortcut_path.exists() and not steam.mapping_path(paths).exists()
    assert not list((shortcut_path.parent / "grid").glob("*.png"))
    assert state(paths)["shortcut_pending"] is True
    monkeypatch.setattr(steam, "save_state", real_save)
    steam.apply(paths)
    entry = vdf.binary_loads(shortcut_path.read_bytes())["shortcuts"]["0"]
    icon = paths.data / "assets/icon.png"
    assert entry["icon"] == str(icon)
    assert icon.read_bytes() == b"icon"
    ident = entry["appid"] & 0xffffffff
    grid = shortcut_path.parent / "grid"
    for name, suffix in (("portrait", "p"), ("wide", ""), ("hero", "_hero"), ("logo", "_logo"), ("icon", "_icon")):
        assert (grid / f"{ident}{suffix}.png").read_bytes() == name.encode()
    atomic(grid / "other.png", "foreign")
    install.uninstall(paths)
    assert sorted(p.name for p in grid.glob("*.png")) == ["other.png"]
    assert not icon.exists()


def test_release_includes_steam_art_and_standalone_assets(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("builder", ROOT / "scripts/build_release.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    root = tmp_path / "source"
    atomic(root / "assets/steam/portrait.png", b"portrait")
    for name in ("install.sh", "DeckCompanion-Installer.desktop"):
        atomic(root / name, (ROOT / name).read_bytes())
    monkeypatch.setattr(builder, "ROOT", root)
    output = tmp_path / "output"
    result = builder.build(output, offline=True)
    with tarfile.open(result["archive"]) as tar:
        assert tar.extractfile("assets/steam/portrait.png").read() == b"portrait"
    for name in ("install.sh", "DeckCompanion-Installer.desktop"):
        assert (output / name).read_bytes() == (root / name).read_bytes()


@pytest.mark.parametrize("bad_hash", [False, True])
def test_standalone_bootstrap_engine_tag_urls_checksum_and_apply(tmp_path, monkeypatch, bad_hash):
    script = (ROOT / "install.sh").read_text()
    code = script.split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        data = b"fake payload"
        info = tarfile.TarInfo("src/deckcompanion/__main__.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    payload = buffer.getvalue()
    urls, calls = [], []
    base = "https://github.com/rennerdo30/DeckCompanion/releases/download/v0.3.0/"
    api = "https://api.github.com/repos/rennerdo30/DeckCompanion/releases?per_page=100&page=1"
    def urlopen(request, timeout):
        url = request.full_url
        urls.append(url)
        if url == api:
            releases = [{"tag_name": tag, "published_at": date, "assets": [{"name": "index.json", "browser_download_url": base + "index.json"}], **flags}
                        for tag, date, flags in [("v0.3.0", "2026-10-01", {}), ("content-2026.11.01.1", "2026-11-01", {}),
                            ("decky-v9.0.0", "2026-11-02", {}), ("v99.0.0", "2026-11-03", {"draft": True}),
                            ("v98.0.0", "2026-11-04", {"prerelease": True})]]
            data = json.dumps(releases).encode()
        elif url.endswith("index.json"):
            assert url.startswith(base), "bootstrap must select the engine asset"
            data = json.dumps({"engine": {"url": base + "deckcompanion-0.3.0.tar.gz", "sha256": "0" * 64 if bad_hash else hashlib.sha256(payload).hexdigest()}}).encode()
        else:
            data = payload
        result = io.BytesIO(data)
        result.url = url
        return result
    class Process:
        pid = 12345
        def __init__(self, command, **kwargs):
            calls.append(command)
        def wait(self, timeout):
            return 0
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", Process)
    monkeypatch.setattr(sys, "argv", ["-", "--json"])
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    monkeypatch.delenv("DECKCOMPANION_INDEX_URL", raising=False)
    if bad_hash:
        with pytest.raises(ValueError, match="SHA256"):
            exec(compile(code, "install-bootstrap", "exec"), {})
        assert calls == []
    else:
        with pytest.raises(SystemExit) as exited:
            exec(compile(code, "install-bootstrap", "exec"), {})
        assert exited.value.code == 0
        assert len(calls) == 2
        assert "install" in calls[0] and "apply" in calls[1] and "--restart-steam" in calls[1]
    assert urls[:2] == [api, base + "index.json"]


def test_piped_shell_uses_bootstrap_even_inside_a_checkout(tmp_path):
    shell = shutil.which("bash") if os.name != "nt" else "C:/Program Files/Git/bin/bash.exe"
    if not shell or not Path(shell).exists():
        pytest.skip("POSIX shell unavailable")
    # Only shell routing runs: mock Python and timeout; no network or Steam.
    fake_bin = tmp_path / "bin"
    atomic(fake_bin / "python3", '#!/bin/sh\nprintf "%s\\n" "$*" > args.txt\ncat > bootstrap.py\n')
    atomic(fake_bin / "timeout", '#!/bin/sh\nshift\nexec "$@"\n')
    for file in fake_bin.iterdir():
        file.chmod(0o755)
    atomic(tmp_path / "src/deckcompanion/__main__.py", "# local checkout")
    atomic(tmp_path / "installer", (ROOT / "install.sh").read_bytes())
    command = 'PATH="$PWD/bin:$PATH"; export PATH; cat installer | sh'
    proc = subprocess.Popen([shell, "-c", command], cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    owned_pid = proc.pid
    try:
        out, err = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=2)
        raise
    assert proc.returncode == 0, (owned_pid, out, err)
    assert (tmp_path / "args.txt").read_text().strip() == "-"
    assert "urllib.request" in (tmp_path / "bootstrap.py").read_text()
    assert b"Apply now" not in out


def test_native_programs_bypass_proton(paths, tmp_path, monkeypatch):
    # As Steam's default tool we also receive native shortcuts (our own UI, emulators).
    elf = tmp_path / "deckcompanion-ui"
    elf.write_bytes(b"\x7fELF\x02\x01rest")
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\n")
    exe = tmp_path / "game.exe"
    exe.write_bytes(b"MZ\x90\x00")
    calls = []
    monkeypatch.setattr(launch.os, "execv", lambda path, args: calls.append((path, args)))
    monkeypatch.setattr(launch, "prepare", lambda *a: (_ for _ in ()).throw(AssertionError("Proton used")))
    launch.launch(paths, "waitforexitandrun", [str(elf), "--flag"])
    launch.launch(paths, "run", [str(script)])
    launch.launch(paths, "getcompatpath", [str(elf)])
    assert calls == [(str(elf), [str(elf), "--flag"]), (str(script), [str(script)])]
    assert launch.native([str(exe)]) is False and launch.native([]) is False
