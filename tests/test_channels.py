"""Independent updates using only isolated files and mocked release metadata."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tomllib
import pytest
from deckcompanion import __version__, cli, content, install, net, recipes, steam, umu, update
from deckcompanion.common import Error, atomic, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]


def entry(text):
    return {"toml": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}


def recipe(version="2", name="Channel game", latest=False):
    text = f'schema_version=1\nappid="42"\nversion="{version}"\nname="{name}"\n'
    if latest:
        text += '''[[mods]]
id="mod"
name="Independent mod"
version="latest"
release_repo="example/mod"
asset="mod.zip"
mode="copy"
target="pfx/mod.zip"
'''
    return text


def index(version="2026.11.01.1", text=None):
    return {"schema_version": 1, "content_version": version, "min_engine": "0.4.3",
            "recipes": {"42": entry(text or recipe())}, "launchers": entry('schema_version=1\n')}


def release(tag, name="content-index.json", url="https://example.org/content", date="2026-11-01T00:00:00Z"):
    return {"tag_name": tag, "published_at": date, "assets": [{"name": name, "browser_download_url": url}]}


@pytest.fixture
def isolated(paths, tmp_path, monkeypatch):
    paths = replace(paths, recipes=tmp_path / "recipes")
    monkeypatch.setenv("DECKCOMPANION_RECIPES", str(paths.recipes))
    monkeypatch.setattr("deckcompanion.proton.sync", lambda p: {})
    monkeypatch.setattr(umu, "install", lambda p: {"installed": False})
    monkeypatch.setattr(steam, "running", lambda: False)
    return paths


def content_network(monkeypatch, candidate, listing=None):
    responses = listing or [release("v9", "index.json"), release("content-" + candidate["content_version"])]
    calls = []
    def fetch(url, target, budget=None, **kwargs):
        calls.append((url, budget))
        if "?per_page=" in url:
            payload = responses
        elif url == "https://example.org/content":
            payload = candidate
        else:
            raise Error("network_timeout", "Offline engine channel")
        write_json(target, payload)
    monkeypatch.setattr(net, "fetch", fetch)
    return calls


def test_update_content_without_an_engine_release(isolated, monkeypatch):
    candidate = index()
    calls = content_network(monkeypatch, candidate)
    result = update.update(isolated)
    assert recipes.load(isolated, "42")["name"] == "Channel game"
    assert result["engine"]["updated"] is False
    active = read_json(isolated.cache / "index.json")
    assert active["launchers"] == candidate["launchers"]
    assert result["channels"]["content"]["version"] == candidate["content_version"]
    assert result["channels"]["content"]["state"] == "current"
    budgets = [budget for url, budget in calls if "?per_page=" in url or url == "https://example.org/content"][-2:]
    assert len(budgets) == 2 and budgets[0] is budgets[1]
    assert budgets[0].remaining() <= 2


def test_latest_published_content_not_engine_or_draft(isolated, monkeypatch):
    candidate = index()
    listing = [release("content-2027.1", date="2027-01-01"), release("v99", "index.json", date="2028-01-01"),
               dict(release("content-2028.1", date="2028-01-01"), prerelease=True),
               release("content-" + candidate["content_version"], date="2029-01-01"),
               dict(release("content-2029.1", date="2030-01-01"), draft=True)]
    content_network(monkeypatch, candidate, listing)
    assert update.update(isolated)["content"]["state"] == "current"


@pytest.mark.parametrize("edit,state", [
    (lambda x: x.update(schema_version=2), "invalid_index"),
    (lambda x: x.update(min_engine="999.0"), "engine_too_old"),
    (lambda x: x.update(min_engine="future"), "invalid_index"),
    (lambda x: x["recipes"]["42"].update(toml=recipe(name="Corrupt")), "checksum_mismatch"),
    (lambda x: x["launchers"].update(sha256="0" * 64), "checksum_mismatch"),
    (lambda x: x.update(launchers=[]), "invalid_index"),
    (lambda x: x.update(recipes=[]), "invalid_index"),
    (lambda x: x.update(signature="unverified"), "unsupported_signature"),
    (lambda x: x.update(content_version="2020.1"), "invalid_index"),
])
def test_invalid_or_incompatible_content_preserves_active_cache(isolated, monkeypatch, edit, state):
    previous = index()
    write_json(isolated.cache / "index.json", previous)
    candidate = deepcopy(previous)
    edit(candidate)
    content_network(monkeypatch, candidate)
    result = update.update(isolated)
    assert read_json(isolated.cache / "index.json") == previous
    assert recipes.load(isolated, "42")["name"] == "Channel game"
    assert result["content"]["state"] == state


def test_offline_and_no_content_use_cache_or_bundle(isolated, monkeypatch):
    atomic(isolated.recipes / "42.toml", recipe(name="Bundled"))
    monkeypatch.setattr(net, "fetch", lambda *a, **k: (_ for _ in ()).throw(Error("network_timeout", "Offline")))
    assert update.update(isolated)["content"]["state"] == "network_timeout"
    assert recipes.load(isolated, "42")["name"] == "Bundled"
    previous = index()
    write_json(isolated.cache / "index.json", previous)
    assert update.update(isolated)["content"]["version"] == previous["content_version"]
    assert read_json(isolated.cache / "index.json") == previous


@pytest.mark.parametrize("bundled,expected", [("1", "Channel game"), ("2", "Channel game"), ("3", "Bundled")])
def test_content_precedence_reuses_recipe_freshness(isolated, bundled, expected):
    atomic(isolated.recipes / "42.toml", recipe(bundled, "Bundled"))
    write_json(isolated.cache / "index.json", index())
    assert recipes.load(isolated, "42")["name"] == expected
    recipes.refresh_cache(isolated)
    assert recipes.load(isolated, "42")["name"] == expected


def test_new_content_keeps_newer_recipe_and_engine_install_preserves_catalog(isolated, monkeypatch):
    previous = index(text=recipe("4", "Newer recipe"))
    write_json(isolated.cache / "index.json", previous)
    candidate = index("2026.11.01.2")
    content_network(monkeypatch, candidate)
    update.update(isolated)
    assert recipes.load(isolated, "42")["name"] == "Newer recipe"
    engine = {"schema_version": 1, "engine": {"version": __version__}, "recipes": {"42": entry(recipe("2", "Engine"))}}
    active = recipes.merge_index(read_json(isolated.cache / "index.json"), engine)
    assert active["content_version"] == candidate["content_version"]
    assert active["launchers"] == candidate["launchers"]
    assert "Newer recipe" in active["recipes"]["42"]["toml"]


def test_content_activation_write_failure_is_atomic(isolated, monkeypatch):
    from deckcompanion import channels
    previous = index()
    write_json(isolated.cache / "index.json", previous)
    content_network(monkeypatch, index("2026.11.01.2"))
    real_write = channels.write_json
    def fail(path, payload):
        if path.name == "index.json":
            raise OSError("Disk full")
        real_write(path, payload)
    monkeypatch.setattr(channels, "write_json", fail)
    assert update.update(isolated)["content"]["updated"] is False
    assert read_json(isolated.cache / "index.json") == previous


def test_latest_mod_does_not_require_build_time_download_fields(isolated):
    atomic(isolated.recipes / "42.toml", recipe(latest=True))
    mod = recipes.load(isolated, "42")["mods"][0]
    assert mod["version"] == "latest" and mod["sha256"] == ""


def mod_network(monkeypatch, version="v2", payload=b"mod bytes", digest=True):
    calls = []
    def fetch(url, target, *args, **kwargs):
        calls.append(url)
        if url.endswith("/releases/latest"):
            info = release(version, "mod.zip", "https://example.org/" + version + "/mod.zip")
            info["assets"][0]["digest"] = "sha256:" + hashlib.sha256(payload).hexdigest() if digest else None
            write_json(target, info)
        else:
            atomic(target, payload)
    monkeypatch.setattr(net, "fetch", fetch)
    return calls


def test_mod_updates_without_content_or_engine_release_and_launch_uses_cache(isolated, monkeypatch, tmp_path):
    from deckcompanion import channels
    atomic(isolated.recipes / "42.toml", recipe(latest=True))
    calls = mod_network(monkeypatch)
    first = channels.resolve_mods(isolated)
    assert first["resolved"]["example/mod/mod.zip"]["version"] == "v2"
    mod_network(monkeypatch, version="v3", payload=b"new bytes")
    channels.resolve_mods(isolated)
    assert recipes.load(isolated, "42")["mods"][0]["version"] == "v3"
    monkeypatch.setattr(net, "fetch", lambda *a, **k: pytest.fail("Launch must use cached resolution and bytes"))
    prefix = tmp_path / "prefix"
    (prefix / "pfx").mkdir(parents=True)
    result = content.reconcile(isolated, "42", recipes.load(isolated, "42"), prefix, None, {}, net.Budget(), lambda s: None)
    assert result == {"mod": "v3"} and (prefix / "pfx/mod.zip").read_bytes() == b"new bytes"
    assert calls.count("https://api.github.com/repos/example/mod/releases/latest") == 1


def test_mod_digest_fallback_records_warning_and_verified_cache(isolated, monkeypatch):
    from deckcompanion import channels
    atomic(isolated.recipes / "42.toml", recipe(latest=True))
    mod_network(monkeypatch, digest=False)
    result = channels.resolve_mods(isolated)
    item = result["resolved"]["example/mod/mod.zip"]
    assert "no digest" in item["warning"]
    assert net.digest(isolated.cache / "downloads" / item["sha256"]) == item["sha256"]


@pytest.mark.parametrize("failure", ["offline", "digest", "mismatch", "missing_asset"])
def test_failed_mod_resolution_keeps_previous_verified_version(isolated, monkeypatch, failure):
    from deckcompanion import channels
    atomic(isolated.recipes / "42.toml", recipe(latest=True))
    mod_network(monkeypatch)
    previous = channels.resolve_mods(isolated)
    def fail(url, target, *a, **k):
        if failure == "offline":
            raise Error("network_timeout", "Offline")
        candidate = release("v3", "other" if failure == "missing_asset" else "mod.zip", "https://example.org/v3/mod.zip")
        candidate["assets"][0]["digest"] = "sha256:bad" if failure == "digest" else "sha256:" + "a" * 64
        write_json(target, candidate) if "/releases/" in url else atomic(target, b"wrong bytes")
    monkeypatch.setattr(net, "fetch", fail)
    result = channels.resolve_mods(isolated)
    assert result["resolved"] == previous["resolved"] and result["updated_at"] == previous["updated_at"]
    assert result["warnings"]


def test_pinned_mod_never_tracks_latest(isolated, monkeypatch):
    from deckcompanion import channels
    text = recipe(latest=True).replace('version="latest"', 'version="v1"') + 'url="https://example.org/v1/mod.zip"\nsha256="' + "a" * 64 + '"\n'
    atomic(isolated.recipes / "42.toml", text)
    channels.resolve_mods(isolated)  # Autouse fixture rejects any network request.
    assert recipes.load(isolated, "42")["mods"][0]["version"] == "v1"


def umu_network(monkeypatch, version="9.1", corrupt=False, missing_digest=False):
    blob = io.BytesIO()
    with tarfile.open(fileobj=blob, mode="w") as archive:
        item = tarfile.TarInfo("umu/umu-run")
        payload = ("#!zipapp " + version).encode()
        item.size = len(payload)
        archive.addfile(item, io.BytesIO(payload))
    payload = blob.getvalue()
    calls = []
    def fetch(url, target, *a, **k):
        calls.append(url)
        if "api.github.com" in url:
            info = release(version, f"umu-launcher-{version}-zipapp.tar", f"https://example.org/{version}/umu.tar")
            if not missing_digest:
                info["assets"][0]["digest"] = "sha256:" + hashlib.sha256(payload).hexdigest()
            write_json(target, info)
        elif url.endswith("/LICENSE"):
            atomic(target, b"license fixture")
        else:
            atomic(target, b"wrong" if corrupt else payload)
    monkeypatch.setattr(net, "fetch", fetch)
    return calls


def test_umu_tracks_upstream_pin_and_cache_only_launch(paths, monkeypatch):
    calls = umu_network(monkeypatch)
    assert umu.install(paths)["version"] == "9.1"
    assert calls[0].endswith("/releases/latest")
    umu.pin(paths, "9.0")
    calls = umu_network(monkeypatch, "9.0")
    assert umu.install(paths)["version"] == "9.0"
    assert calls[0].endswith("/releases/tags/9.0")
    monkeypatch.setattr(net, "fetch", lambda *a, **k: pytest.fail("Launch must never fetch umu"))
    assert umu.install(paths, net.Budget())["version"] == "9.0"
    umu.pin(paths, "latest")
    umu_network(monkeypatch, "10.0")
    assert umu.install(paths)["version"] == "10.0"
    assert (paths.data / "umu/9.0/umu-run").is_file()


@pytest.mark.parametrize("failure", ["corrupt", "missing_digest", "offline"])
def test_bad_or_offline_umu_keeps_active_runtime(paths, monkeypatch, failure):
    umu_network(monkeypatch)
    umu.install(paths)
    old = read_json(paths.cache / "umu-current.json")
    if failure == "offline":
        monkeypatch.setattr(net, "fetch", lambda *a, **k: (_ for _ in ()).throw(Error("network_timeout", "Offline")))
    else:
        umu_network(monkeypatch, "9.2", corrupt=failure == "corrupt", missing_digest=failure == "missing_digest")
    result = umu.install(paths)
    assert result["version"] == "9.1" and result["cached"] is True
    assert read_json(paths.cache / "umu-current.json") == old
    assert not (paths.data / "umu/9.2").exists()


def builder(name):
    spec = importlib.util.spec_from_file_location("channel_build_" + name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_engine_builder_never_resolves_or_publishes_content(tmp_path):
    build = builder("build_release")
    result = build.build(tmp_path / "engine")  # no network mock: fixture denies it.
    assert read_json(Path(result["index"]))["recipes"] == {}
    with tarfile.open(result["archive"]) as archive:
        bundled = tomllib.loads(archive.extractfile("recipes/2300320.toml").read().decode())
    assert bundled["mods"][0]["version"] == "latest" and not bundled["mods"][0]["sha256"]


@pytest.mark.parametrize("launchers", [True, False])
def test_content_builder_keeps_latest_and_hashes_optional_catalog(tmp_path, monkeypatch, launchers):
    build = builder("build_content")
    root = tmp_path / "repo"
    atomic(root / "recipes/42.toml", recipe(latest=True))
    if launchers:
        atomic(root / "content/launchers.toml", 'schema_version=1\n')
    monkeypatch.setattr(build, "ROOT", root)
    result = build.build(tmp_path / "out", "2026.11.01.1", min_engine="999.0")
    built = read_json(Path(result["index"]))
    assert tomllib.loads(built["recipes"]["42"]["toml"])["mods"][0]["version"] == "latest"
    assert ("launchers" in built) is launchers
    if launchers:
        assert built["launchers"] == entry('schema_version=1\n')
    assert result["tag"] == "content-2026.11.01.1"
    assert (tmp_path / "out/checksums.sha256").read_text().startswith(net.digest(Path(result["index"])))


def test_status_channels_are_read_only_and_preserve_existing_fields(isolated):
    result = cli.execute(cli.parser().parse_args(["status", "--json"]), isolated)
    assert result["version"] == __version__ and "pending" in result
    assert result["channels"] == {"engine": {"current": __version__, "available": __version__},
        "content": {"version": None, "fetched_at": None, "state": "bundled"},
        "mods": {"resolved": {}, "updated_at": None}, "umu": {"version": None}}
    assert not (isolated.cache / "index.json").exists()


def test_engine_latest_pointing_to_content_finds_engine_release(isolated, monkeypatch):
    from test_update_build import index as engine_index
    candidate = engine_index(isolated, __version__)
    def fetch(url, target, *a, **k):
        if "?per_page=" in url:
            result = [release("v" + __version__, "index.json", "https://example.org/engine-index"),
                      release("content-2026.11.01.1", date="2026-11-02"), release("decky-v99.0.0", date="2026-11-03"),
                      dict(release("v99.0.0", date="2026-11-04"), draft=True),
                      dict(release("v98.0.0", date="2026-11-05"), prerelease=True)]
        elif url.endswith("engine-index"):
            result = candidate
        else:
            raise Error("network_timeout", "Offline content")
        write_json(target, result)
    monkeypatch.setattr(net, "fetch", fetch)
    assert update.update(isolated)["engine"]["updated"] is True


def test_bad_engine_archive_does_not_block_other_channels(isolated, monkeypatch):
    engine = {"schema_version": 1, "engine": {"version": "999", "url": "https://example.org/engine", "sha256": "a" * 64}, "recipes": {}}
    candidate = index()
    def fetch(url, target, *a, **k):
        if url.endswith("/index"):
            write_json(target, engine)
        elif "?per_page=" in url:
            write_json(target, [release("v999.0.0", "index.json", "https://example.org/index"),
                                release("content-" + candidate["content_version"])])
        elif url.endswith("/content"):
            write_json(target, candidate)
        else:
            atomic(target, b"corrupt engine")
    monkeypatch.setattr(net, "fetch", fetch)
    result = update.update(isolated)
    assert result["engine"]["updated"] is False and "checksum" in result["engine"]["error"].lower()
    assert result["content"]["state"] == "current"


def test_index_asset_digest_checked_before_activation(isolated, monkeypatch):
    candidate = index()
    selected = release("content-" + candidate["content_version"])
    selected["assets"][0]["digest"] = "sha256:" + "a" * 64
    content_network(monkeypatch, candidate, [selected])
    result = update.update(isolated)
    assert result["content"]["state"] == "checksum_mismatch"
    assert not (isolated.cache / "index.json").exists()


def test_release_listing_paginates_and_uses_one_deadline(isolated, monkeypatch):
    from deckcompanion import channels
    candidate = index()
    calls = []
    def fetch(url, target, budget, **kwargs):
        calls.append((url, budget))
        if url.endswith("&page=1"):
            result = [release("v1", "index.json")] * 100
        elif url.endswith("&page=2"):
            result = [release("content-" + candidate["content_version"])]
        else:
            result = candidate
        write_json(target, result)
    monkeypatch.setattr(net, "fetch", fetch)
    assert channels.refresh(isolated)["state"] == "current"
    assert len(calls) == 3 and all(b is calls[0][1] for _, b in calls)


def test_existing_pre_channel_umu_receipt_remains_launchable_offline(paths):
    target = paths.data / "umu/8.0"
    atomic(target / "umu-run", b"previous runtime")
    atomic(target / "LICENSE", b"previous licence")
    receipt = {"version": "8.0", "url": "https://example.org/8.0/umu.tar", "archive_sha256": "a" * 64,
               "runner_sha256": net.digest(target / "umu-run")}
    write_json(target / "receipt.json", receipt)
    before = (target / "receipt.json").read_bytes()
    assert umu.install(paths, net.Budget())["path"] == str(target / "umu-run")
    assert (target / "receipt.json").read_bytes() == before


def test_content_builder_enriches_deliberate_pins_not_latest(tmp_path, monkeypatch):
    build = builder("build_content")
    root = tmp_path / "repo"
    text = recipe(latest=True).replace('version="latest"', 'version="v1"').replace('id="mod"', 'id = "mod"')
    text += 'url = "https://example.org/unresolved"\nsha256 = ""\n[runtime]\nproton = "GE-Proton11-11"\n'
    atomic(root / "recipes/42.toml", text)
    monkeypatch.setattr(build, "ROOT", root)
    calls = []
    def fetch(url, target, *a, **k):
        calls.append(url)
        tag = "v1" if "/example/mod/" in url else "GE-Proton11-11"
        name = "mod.zip" if tag == "v1" else tag + "-x86_64.tar.gz"
        info = release(tag, name, f"https://example.org/{tag}/{name}")
        info["assets"][0]["digest"] = "sha256:" + "b" * 64
        write_json(target, info)
    monkeypatch.setattr(net, "fetch", fetch)
    result = build.build(tmp_path / "out", "2026.11.01.1")
    built = tomllib.loads(read_json(Path(result["index"]))["recipes"]["42"]["toml"])
    assert built["mods"][0]["version"] == "v1" and built["mods"][0]["sha256"] == "b" * 64
    assert built["runtime"]["sha256"] == "b" * 64
    assert len(calls) == 2 and all("/tags/" in call for call in calls)


@pytest.mark.parametrize("preview,expected", [(False, "v1.0.0"), (True, "v2.0.0-rc.1")])
def test_engine_release_channel_opt_in_only(isolated, monkeypatch, preview, expected):
    listing = [release("v1.0.0", date="2026-11-01"), release("content-2026.11.01.1", date="2026-11-02"),
               release("decky-v10.0.0", date="2026-11-03"),
               dict(release("v2.0.0-rc.1", date="2026-11-04"), prerelease=True),
               dict(release("v99.0.0", date="2026-11-05"), draft=True)]
    monkeypatch.setenv("DECKCOMPANION_ENGINE_PRERELEASES", "1" if preview else "0")
    monkeypatch.setattr(net, "fetch", lambda url, target, *a, **k: write_json(target, listing))
    assert update.engine_release(isolated)["tag_name"] == expected


def test_bootstrap_engine_selector_matches_mixed_release_policy(monkeypatch):
    import ast
    code = (ROOT / "install.sh").read_text().split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]
    tree = ast.parse(code)
    safe = ast.Module(body=[n for n in tree.body if isinstance(n, (ast.Import, ast.FunctionDef))], type_ignores=[])
    namespace = {}
    exec(compile(safe, "bootstrap-selector", "exec"), namespace)
    listing = [release("content-2026.11.01.1", date="2026-11-02"), release("decky-v9.0.0", date="2026-11-03"),
               release("v1.0.0", "index.json", "https://example.org/v1/index.json", date="2026-11-01"),
               dict(release("v2.0.0-rc.1", "index.json", "https://example.org/v2/index.json", date="2026-11-04"), prerelease=True),
               dict(release("v99.0.0", date="2026-11-05"), draft=True)]
    namespace["get"] = lambda *a: json.dumps(listing).encode()
    monkeypatch.delenv("DECKCOMPANION_ENGINE_PRERELEASES", raising=False)
    assert namespace["release_index"]() == "https://example.org/v1/index.json"
    monkeypatch.setenv("DECKCOMPANION_ENGINE_PRERELEASES", "1")
    assert namespace["release_index"]() == "https://example.org/v2/index.json"


def test_readme_and_desktop_installer_use_the_website_bootstrap():
    import configparser
    import shlex
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ROOT / "DeckCompanion-Installer.desktop")
    command = shlex.split(parser["Desktop Entry"]["Exec"])[7]
    # The website serves the repository's install.sh, which selects the newest stable v* release.
    assert "https://deckcompanion.renner.dev/install.sh" in command
    assert "curl -fsSL https://deckcompanion.renner.dev/install.sh | sh" in (ROOT / "README.md").read_text()
    assert "releases/latest" not in command and command.endswith("| timeout 300s sh")
