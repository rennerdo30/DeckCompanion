# DeckCompanion

App to simplify installation of fixes for non working games on the steam deck

General Idea
---
This tool should be selected as a Steam Play compatibility tool.
DeckCompanion will then internally select a working proton version and will apply fixes to the wine prefix, if needed.

Installation
---
Open `Konsole` and enter the following command:
```bash
cd /home/$USER/.local/share/Steam/compatibilitytools.d/ && git clone https://github.com/rennerdo30/DeckCompanion.git
```
Then restart Steam.

DeckCompanion now shows up under *Properties → Compatibility → Force the use of a specific Steam Play
compatibility tool* for every game.

How it works
---
When Steam launches a game through DeckCompanion, it:

1. Runs `git pull` on itself, so bundled fixes stay up to date.
2. Looks up a config for the running game (see below).
3. Searches every Steam library (`steamapps/common`) and `compatibilitytools.d` for the Proton
   version named in that config.
4. Runs the config's fix scripts against the game's wine prefix, in order.
5. Hands the original launch command over to that Proton build, with any extra environment
   variables from the config applied.

Everything is logged to `/tmp/DeckCompanion.log`.

Game configuration
---
Configs live in `games/<steam-app-id>/config.json`. If a game has no directory of its own,
`games/config.json` is used as the default. A config looks like this:

```json
{
    "proton-version": "Proton 7.0",
    "env": {
        "SOME_VARIABLE": "value"
    },
    "scripts": [
        { "order": 1, "path": "fix-permissions.sh" },
        { "order": 2, "path": "protontricks.sh" }
    ]
}
```

- `proton-version` — the *folder name* of the Proton build to use, e.g. `Proton 7.0` or
  `Proton - Experimental`.
- `env` — optional environment variables passed to Proton.
- `scripts` — optional bash scripts, resolved relative to the game's config directory and executed
  in `order`. They run with `PROTON`, `STEAM_COMPAT_TOOL_PATHS` and `WINEPREFIX` already set, so they
  can call `protontricks`, patch files in the prefix, and so on.

To add a fix for a game, create `games/<steam-app-id>/` with a `config.json` and the scripts it
references. A few example configs are bundled in this repo.

Requirements
---
- Steam Deck (or any Linux Steam install) with at least one Proton build installed
- Python 3 and `git` — both ship with SteamOS
- Whatever the individual fix scripts need (`protontricks`, for example)

`vdf` is vendored in this repo, so there is nothing to install with `pip`.
