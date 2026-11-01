# Engine CLI, protocol v1

Python >= 3.11, standard library only. From a source checkout set `PYTHONPATH=src`
(include the repository root when invoking outside it) and run
`python3 -m deckcompanion`. Installed entry point: `deckcompanion` in
`~/.local/share/deckcompanion/bin/`. The Godot UI calls this executable directly,
without a shell. Stable UI executable: `bin/deckcompanion-ui` under the same data root.

Every command accepts `--json` before or after the command and emits exactly one
JSON object on stdout. Normal responses are JSON even without this flag. Exit 0
means success; errors exit 1 with
`{"error":{"code":"steam_running","message":"..."}}`. Parser errors follow this
contract too. `--help` is the human help exception. No progress messages on stdout.

## Commands available in slice 1

- `games --json` -> `{"games":[GAME,...]}`. Includes installed games, bundled/cached
  recipes, and games with saved state. IDs are strings. No network requests.
- `game APPID --json` -> GAME plus `notes` (string) and `log_tail` (string, last 200 lines).
- `enable APPID --json` / `disable APPID --json` -> GAME. Queues a change; does not write Steam files.
- `mod APPID MOD_ID --on|--off --json` -> GAME. Saves preference immediately; content reconciles at launch.
- `apply [--restart-steam] --json` -> `{"applied":[{"appid":"2300320","action":"enable"}],"steam_restarted":true}`.
  Installer shortcut changes have `{"action":"shortcut"}`. Without restart, Steam must already
  be closed. With restart, send `steam -shutdown`, wait at most 10 seconds, recheck
  before each write, back up originals, apply, then `steam -silent` (10 second timeout).
  If Steam does not close, refuse and retain the queue. Never kill Steam.
- `status --json` -> `{"version":"0.2.0","steam_running":false,"pending":[],"proton":[{"name":"GE-Proton11-11","path":"..."}]}`.

GAME:
```json
{"appid":"2300320","name":"Farming Simulator 25","installed":true,
 "has_recipe":true,"enabled":false,"pending":null,"recipe_version":"1.0.0",
 "mods":[{"id":"fs25-deck-mode","name":"FS25 Deck Mode","version":"unresolved",
          "enabled":true,"installed_version":null}],"last_launch":null}
```
`pending` is null, `enable`, or `disable`; `last_launch` is null or
`{"time":"ISO8601","ok":true}`. `recipe_version` is null for games without recipes.
Enabled state means the queued mapping was applied; it is independent of mod preferences.
Disabling restores the previous compatibility mapping if ours is still selected.
Foreign mappings, shortcut entries, and unmarked protonfixes files are preserved.

## Environment / isolation

`XDG_DATA_HOME`, `XDG_CONFIG_HOME`, `DECKCOMPANION_HOME` (state, versions, logs, bin),
`DECKCOMPANION_CACHE`, `DECKCOMPANION_CONFIG` (protonfixes root),
`DECKCOMPANION_STEAM_ROOT`, `DECKCOMPANION_COMPAT_TOOLS`, `DECKCOMPANION_RECIPES`
override every root. Empty overrides fail. `STEAM_COMPAT_CLIENT_INSTALL_PATH` is
the default Steam root when provided. `DECKCOMPANION_STEAM_USER_ID` chooses a
userdata account for the shortcut; required if there is more than one account.
No writes to Steam on list/query/queue commands. State writers serialize with an OS
lock. A malformed isolation override never falls back to the real home directory.

Upcoming stacked slices add `launch`, `update`, `rollback`, `install`, `uninstall`.
No command or integration has been verified on hardware.
