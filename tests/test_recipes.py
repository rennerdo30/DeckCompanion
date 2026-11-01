from copy import deepcopy
import pytest
from deckcompanion import recipes
from deckcompanion.common import Error, inside


def test_bundled_recipes(paths):
    recipe = recipes.load(paths, "2300320")
    assert recipe["runtime"]["proton"] == "GE-Proton11-11"
    assert recipe["mods"][0]["mode"] == "copy"
    assert recipes.load(paths, "602520")["wine"]["winetricks"] == ["wmp11"]
    assert recipes.load(paths, "1063730") is None


@pytest.mark.parametrize("edit", [
    lambda r: r.update(schema_version=2),
    lambda r: r.update(appid="../bad"),
    lambda r: r["mods"][0].update(target="../outside"),
    lambda r: r["mods"][0].update(sha256="broken"),
    lambda r: r["mods"][0].update(url="file:///local"),
    lambda r: r["runtime"].update(env={"A": 42}),
    lambda r: r.update(wine={"registry": [{}]}),
    lambda r: r["mods"].append(deepcopy(r["mods"][0])),
])
def test_invalid_recipes(paths, edit):
    recipe = recipes.load(paths, "2300320")
    edit(recipe)
    with pytest.raises(Error):
        recipes.validate(recipe)


def test_filename_and_toml_errors(paths):
    paths.cache.joinpath("recipes").mkdir(parents=True)
    file = paths.cache / "recipes/2300320.toml"
    file.write_text('schema_version = 1\nappid = "44"\nname = "X"\nversion = "1"')
    with pytest.raises(Error, match="filename"):
        recipes.load(paths, "2300320")
    file.write_text("[broken")
    with pytest.raises(Error, match="Invalid recipe"):
        recipes.load(paths, "2300320")


def test_path_traversal(tmp_path):
    for value in ("/abs", "../outside", "a/../b", "a\\b", "c:/x"):
        with pytest.raises(Error):
            inside(tmp_path, value)
