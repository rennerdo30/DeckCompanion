"""Public regressions also runnable unchanged on the pre-DC-13 base."""
import json
import pytest
from deckcompanion import cli, launch, net
from deckcompanion.common import atomic


def test_proton_json_lists_wanted_enabled_recipe(paths, capsys):
    from deckcompanion.common import save_state
    save_state(paths, {"games": {"2300320": {"enabled": True}}})
    assert cli.main(["proton", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    build = next(b for b in result["builds"] if b["name"] == "GE-Proton11-11")
    assert build["state"] == "wanted" and build["wanted_by"] == ["Farming Simulator 25"]
    assert not result["active"]


@pytest.mark.parametrize("key", ["auto_install_ge", "cleanup_old_ge"])
def test_ge_settings_default_and_roundtrip(paths, key, capsys):
    assert cli.main(["settings", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[key] is True
    assert cli.main(["settings", "--set", key + "=false", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[key] is False


def test_launch_never_fetches_even_with_resolved_runtime_url(paths, monkeypatch):
    atomic(paths.tools / "GE-Proton11-7/proton", b"installed")
    calls = []
    def attempted(*a, **kw):
        calls.append(a)
        raise OSError("must not fetch in launch")
    monkeypatch.setattr(net, "cached", attempted)
    runtime = {"proton": "GE-Proton11-11", "url": "https://example.org/proton", "sha256": "a" * 64}
    selected = launch.resolve_proton(paths, {"runtime": runtime}, net.Budget(), lambda _: None)
    assert selected.name == "GE-Proton11-7"
    assert calls == []
