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
- `game APPID --json` -> GAME plus `notes` (string) and `log_tail` (array of strings, last 200 lines, newlines stripped).
- `enable APPID --json` / `disable APPID --json` -> GAME. Queues a change; does not write Steam files.
- `mod APPID MOD_ID --on|--off --json` -> GAME. Saves preference immediately; content reconciles at launch.
- `apply [--restart-steam] --json` -> `{"applied":[{"appid":"2300320","action":"enable"}],"steam_restarted":true}`.
  Installer shortcut changes have `{"action":"shortcut"}`. Without restart, Steam must already
  be closed. On Linux with Steam running, restart hands off and returns immediately:
  `{"applied":[],"scheduled":true,"steam_restarted":false,"helper":"deckcompanion-apply-<timestamp>"}`.
  The UI can show **Steam will restart now**. Scheduling does not confirm writes succeeded.
  Windows/dev retains in-process shutdown, a 10-second wait and Steam startup.
  Writes recheck Steam and back up originals. Never kill detected Steam processes.
- `status --json` -> `{"version":"0.2.0","steam_running":false,"pending":[],"proton":[{"name":"GE-Proton11-11","path":"..."}],"last_apply":null}`.
  `last_apply` is null before a helper run, then the content of `logs/apply-last.json`.

## Detached Linux apply

Internal command `apply-helper [--game-mode]` runs through
`systemd-run --user --unit=deckcompanion-apply-<timestamp> --collect --property=RuntimeMaxSec=300`.
The caller waits only for bounded service submission, not Steam shutdown. If
`systemd-run` is missing, a double-fork/`setsid` supervisor bounds the helper to
300 seconds. A present but failing user manager reports an error. The user service
does not depend on the UI or SSH session lifetime; nohup is not used.

The helper pins the invoking engine and forwards configured roots and Steam user ID.
It holds `apply.lock` for its whole run, records its PID, detects Game Mode, sends
`steam -shutdown`, and waits up to 120 seconds total (including the shutdown command)
for both `steam` and `steamwebhelper` to disappear. It then takes the state lock,
applies the queue with existing guards/backups, and restores Steam even after a disk
write failure. A shutdown timeout writes no Steam files and retains the queue.
The state lock is held only during writes, so status remains readable during shutdown.

Game Mode detection uses `/proc/*/comm` names `gamescope` or `start-gamescope-session`
(including Linux's truncated `start-gamescope` name), `gamescope`/`steam` in
`XDG_CURRENT_DESKTOP`, or true/1/yes `SteamDeck` / `SteamGamepadUI`. The caller passes
detected Game Mode across the systemd environment boundary; the helper also detects it.
After applying, Game Mode gets 10 seconds to return automatically, requiring both
Steam and live gamescope (inherited env alone is insufficient). Otherwise run
`steamos-session-select gamescope`, or `holo-session-select gamescope` if necessary.
Desktop Mode starts `steam -silent` in a separate user service, with double-fork
fallback if the runner is missing. Startup is checked for 10 seconds; restored Steam
is intentionally long-lived.

`~/.local/share/deckcompanion/logs/apply-last.json` (under `DECKCOMPANION_HOME` when
overridden) records `pid`, `started_at`, `finished_at`, `game_mode`, `applied`,
`steam_restarted`, `errors` (code/message objects), and `timings` (seconds for completed
shutdown/apply/restore phases and total). An in-flight receipt has no `finished_at`;
an interrupted or service-timeout run can leave that receipt for diagnosis. A second
helper fails with `busy` without touching the active receipt or Steam. The internal
CLI exits 1 when the receipt contains errors. Not verified on hardware.

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

## Launch (slice 2)

`launch [--json] VERB CMD...` is called by the stable compatibility launcher. It
passes argument boundaries intact to `<proton>/proton VERB CMD...` with `execve`.
Successful launch replaces the process, so it emits no JSON success object. Its
errors use the normal JSON envelope. After VERB, every argument belongs to the
game, including `--json`. Runtime env and launch options come from the recipe.

`last_launch.ok` means preparation/handoff succeeded, not that the game ran or
exited successfully. The engine cannot observe the result after `execve`.
Missing/corrupt optional content logs a warning and uses installed content. All
launch network requests share a two-second wall budget, implemented in an owned
worker subprocess so DNS and slow bodies are bounded too. No index/latest API
requests at launch. Hash-verified cached mods work offline. Install a runtime in
advance for large GE archives; a two-second launch budget rarely accommodates one.

Localfixes call upstream `early()` and `main()` from the running protonfixes package
before our additions. Wine installs/registry steps use `@util.once(retry=True)`;
environment and DLL overrides apply each invocation. Foreign localfixes are
preserved. Prefix content is deferred until a subsequent launch when `pfx` exists.
Symlinked Documents directories outside the prefix are refused and logged.
Content warnings also appear in `game APPID --json`'s `notes`, including the mod,
requested path, resolved destination and containment reason. `log_tail` retains
the same message; game listing shapes remain unchanged.

## Update and rollback (slice 3a)

`update --json` -> `{"engine":{"current":"0.2.0","available":"0.2.0","updated":false},"index":{"updated":false,"fetched_at":null}}`.
`fetched_at` is an ISO8601 string after the first successful fetch; null if none.
Offline operation uses the cache. A cached release can still install offline.
`rollback --json` -> `{"version":"0.1.0","rolled_back":true}`; no previous version
is an error. See [updates.md](updates.md) for integrity, cache and signing limits.

## Installer commands (slice 3)

`install [--source EXTRACTED_RELEASE] --json` ->
`{"installed":true,"version":"0.2.0","shortcut_pending":true,"steam_changes_pending":true,"restart_required":true,"legacy_tool_preserved":false}`.
Engine versions, compat tool, bin stubs and UI/assets install while Steam is running.
Only Steam VDF writes (shortcut and queued mappings) wait for `apply` with Steam closed.
`steam_changes_pending` includes both mappings and the shortcut; it can be false for
an engine-only install without queued changes. Installs no packages and changes no
persistent environment.
The source defaults to the current engine/checkout root. UI shortcut is queued
only if the release contains `bin/deckcompanion-ui`. `apply` chooses the Steam user.
If bundled, `assets/logo/deckcompanion-icon-256.png` supplies the shortcut's installed
icon path. The SVG alone is retained as an asset but is not used as a Steam icon.

`uninstall --json` -> `{"uninstalled":true,"preserved":[],"steam_restart_required":true}`.
It requires Steam closed, restores prior mappings, and preserves user changes and
state/cache/backups. `preserved` lists modified files. See the README for migration.
No command or integration has been verified on hardware.
