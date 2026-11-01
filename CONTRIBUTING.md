# Contributing recipes and fixes

A recipe tells DeckCompanion how to run one game well on the Steam Deck. That can mean which Proton build to use, small Wine fixes, mods to install and settings to change. Recipes are plain data files, never scripts. They reach every Deck through the content channel, without a new DeckCompanion release.

## Pick the right place for your fix

| Your fix | Where it belongs |
|---|---|
| A generic Wine/Proton fix that every Proton user needs (winetricks verb, DLL override, launch argument) | **Upstream** in [umu-protonfixes](https://github.com/Open-Wine-Components/umu-protonfixes) (`gamefixes-steam/<appid>.py`). GE-Proton ships it, and DeckCompanion picks GE-Proton automatically for games that have a community fix. |
| A Deck-specific setup: a pinned Proton build, mods, settings for the 1280x800 screen or controller defaults | **A DeckCompanion recipe** in this repository (`recipes/<appid>.toml`) |
| You're not sure | Open an issue with the **Game report** template. We'll help you decide. |

## Write a recipe

1. **Find the app ID** in the game's Steam store URL, for example `2300320` for Farming Simulator 25. For a game added through umu, use its umu ID (`recipes/umu-<id>.toml`).
2. **Create `recipes/<appid>.toml`.** The full schema is in [docs/recipes.md](docs/recipes.md). A minimal recipe:

   ```toml
   schema_version = 1
   appid = "2300320"
   name = "Farming Simulator 25"
   version = "1.0.0"
   notes = "One or two sentences for players: what this recipe does and anything they need to do in the game."

   [runtime]
   proton = "GE-Proton11-11"   # exact build, or "GE-Proton" for the newest installed GE

   [[mods]]
   id = "fs25-deck-mode"
   name = "FS25 Deck Mode"
   version = "latest"           # resolved from the mod's own releases, verified by SHA256
   url = "https://github.com/rennerdo30/FS25_DeckMode/releases/latest"
   sha256 = ""
   release_repo = "rennerdo30/FS25_DeckMode"
   asset = "FS25_DeckMode.zip"
   mode = "copy"
   root = "prefix"
   target = "pfx/drive_c/users/steamuser/Documents/My Games/FarmingSimulator2025/mods/FS25_DeckMode.zip"
   enabled = true
   ```
3. **Bump `version`** whenever you change an existing recipe. The newer version is what reaches players.

### Rules for recipes

- **Data only:** no shell scripts, no arbitrary code, no downloads outside the declared `url`.
- **Mods come from their official source:** the author's GitHub releases or official download page. They need a licence that allows redistribution or automatic download, and either a pinned `sha256` or `version = "latest"` with `release_repo` + `asset`, so DeckCompanion can verify the GitHub digest.
- **Mods must be removable:** DeckCompanion records every file it installs and removes them when the mod is turned off. Use `copy` for single files and `extract_zip` only into a dedicated folder.
- **Settings are applied once** and never overwrite a value the player changed later. Only touch what the Deck needs.
- **Player-facing `notes`:** say what happens and what the player needs to do, for example ticking a mod in the game's mod list. No developer notes.
- **Keep it minimal:** if a game runs fine with Valve's recommended Proton, it doesn't need a recipe.

## Test it

On a Steam Deck (Desktop Mode, Konsole), with DeckCompanion installed:

```sh
cp recipes/<appid>.toml ~/.local/share/deckcompanion/versions/$(cat ~/.local/share/deckcompanion/current)/recipes/
~/.local/share/deckcompanion/bin/deckcompanion game <appid> --json   # recipe, Proton, mod states
~/.local/share/deckcompanion/bin/deckcompanion enable <appid> --json
~/.local/share/deckcompanion/bin/deckcompanion apply --restart-steam --json
```

Then launch the game from Steam. The splash shows the Proton build and mods in use. The launch log is in `~/.local/share/deckcompanion/logs/<appid>-last.log`.

On a PC you can validate the file without a Deck. `pytest` checks every bundled recipe:

```sh
python -m pytest -q tests/test_recipes.py
```

## Submit it

1. Fork the repository, add or change `recipes/<appid>.toml`, and open a pull request.
2. In the pull request, say:
   - which game and SteamOS version you tested on
   - which Proton build was used
   - what the recipe changes and why
   - whether the game was launched and played (not just started)
3. Maintainers review it, build a new content release and publish it. Every Deck picks it up on its next update check.

## Report a game that needs help

Open an issue with the **Game report** template: game name and app ID, what goes wrong on the Deck, which Proton builds you tried, and the launch log (`~/.local/share/deckcompanion/logs/<appid>-last.log`).
