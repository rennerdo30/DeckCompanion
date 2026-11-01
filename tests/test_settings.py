import pytest
from deckcompanion import cli
from deckcompanion.common import Error, read_json


def invoke(paths, *args):
    return cli.execute(cli.parser().parse_args(["settings", *args, "--json"]), paths)


def test_general_settings_round_trip_and_alias_precedence(paths):
    values = invoke(paths, "--set", "show_games_without_recipe=false", "--set", "check_updates_on_start=off",
                    "--set", "splash=false", "--set", "splash_seconds=99")
    assert values == {"splash": False, "splash_seconds": 10, "show_games_without_recipe": False, "check_updates_on_start": False,
                      "auto_install_ge": True, "cleanup_old_ge": True, "takeover_all": True}
    assert invoke(paths) == values
    values = invoke(paths, "--set", "splash=false", "--splash", "on", "--set", "splash_seconds=1", "--splash-seconds", "3.5")
    assert values["splash"] is True and values["splash_seconds"] == 3.5
    assert read_json(paths.data / "state.json")["settings"] == values


@pytest.mark.parametrize("invalid", ["unknown=true", "splash", "splash=maybe", "show_games_without_recipe=1",
    "check_updates_on_start=", "splash_seconds=abc", "splash_seconds=nan", "splash_seconds=inf"])
def test_invalid_settings_do_not_partially_write(paths, invalid):
    baseline = invoke(paths, "--set", "splash=true")
    with pytest.raises(Error):
        invoke(paths, "--set", "splash=false", "--set", invalid)
    assert invoke(paths) == baseline
