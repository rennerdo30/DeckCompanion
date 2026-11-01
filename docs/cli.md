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
- `enable APPID --json` / `disable APPID --json` -> GAME. Queues a mapping change;
  does not write Steam files. Enable also attempts immediate mod reconciliation.
- `default --on|--off --json` -> the same object as `status`. Queues setting or
  restoring global `CompatToolMapping` key `"0"`; Steam files stay unchanged until
  `apply`. Install queues `default_on` automatically. The previous complete entry
  is saved at apply time and restored by off/uninstall only while ours is selected.
- `mod APPID MOD_ID --on|--off --json` -> GAME plus
  `"mod_result":{"changed_now":true,"message":"Installed v0.7.0"}`.
  Saves preference and attempts to install, adopt, update or remove content immediately.
  `changed_now` is true only when this mod's content or ownership changed during this command.
- `apply [--restart-steam] --json` -> `{"applied":[{"appid":"2300320","action":"enable"}],"steam_restarted":true}`.
  Global default changes have `{"action":"default_on"}` or `{"action":"default_off"}`.
  Installer shortcut changes have `{"action":"shortcut"}`. Without restart, Steam must already
  be closed. On Linux with Steam running, restart hands off and returns immediately:
  `{"applied":[],"scheduled":true,"steam_restarted":false,"helper":"deckcompanion-apply-<timestamp>"}`.
  The UI can show **Steam will restart now**. Scheduling does not confirm writes succeeded.
  Windows/dev retains in-process shutdown, a 10-second wait and Steam startup.
  Writes recheck Steam and back up originals. Never kill detected Steam processes.
  An empty queue returns `{"applied":[],"steam_restarted":false}` without scheduling
  a helper or restarting Steam, including with `--restart-steam`.
- `status --json` -> `{"version":"0.3.4","install_path":"/home/deck/.local/share/deckcompanion","steam_running":false,"default_tool":false,"pending":[],"proton":[{"name":"GE-Proton11-11","path":"..."}],"last_apply":null}`.
  `default_tool` reflects the current on-disk global mapping, even while a change
  is queued. `pending` retains game entries and adds `{"action":"default_on"}`
  or `{"action":"default_off"}` without an appid for a queued global change.
  `last_apply` is null before a helper run, then the content of `logs/apply-last.json`.

- `settings --json` -> `{"splash":true,"splash_seconds":2.5,"show_games_without_recipe":true,"check_updates_on_start":true}`.
  Returns all persisted settings plus defaults; reading does not write the state file.
  `settings --set key=value [--set key=value ...] --json` validates all entries before
  saving. Boolean keys `splash`, `show_games_without_recipe` and
  `check_updates_on_start` accept true/false or on/off; all default on. Numeric
  `splash_seconds` defaults to 2.5 and clamps finite numbers to 1–6. Unknown keys,
  malformed entries, invalid booleans and nonfinite numbers fail without partial writes.
  `settings --splash on|off --splash-seconds N --json` updates either or both values
  under `settings` in state, preserving other settings and game data. Defaults are
  on and 2.5 seconds; finite durations clamp to 1–6 seconds. Changes take effect on
  the next game launch and need no Steam restart. Nonfinite values are rejected.
  Splash flags are aliases and take precedence over `--set` for the same key.
  Library visibility and automatic update checks are UI preferences; the UI runs
  `update` on an independent background worker once on open when enabled. Global
  default-tool selection retains its separate `default --on|--off` / Apply contract.
  `status.install_path` identifies the data/install root for the Settings About card.

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
 "mods":[{"id":"fs25-deck-mode","name":"FS25 Deck Mode","version":"v0.7.0",
          "enabled":true,"installed_version":"v0.7.0",
          "state":"installed","state_message":"Installed v0.7.0"}],"last_launch":null,
 "art":{"portrait":"/home/deck/.local/share/Steam/appcache/librarycache/2300320/0123456789012345678901234567890123456789/library_600x900.jpg",
        "hero":null,"header":null,"logo":null},
 "proton":{"name":"GE-Proton11-11","reason":"pinned"}}
```
`pending` is null, `enable`, or `disable`; `last_launch` is null or
`{"time":"ISO8601","ok":true}`. `recipe_version` is null for games without recipes.
Enabled state means the queued mapping was applied; it is independent of mod preferences.
Disabling restores the previous compatibility mapping if ours is still selected.
Foreign mappings, shortcut entries, and unmarked protonfixes files are preserved.

`art` is always an object with `portrait`, `hero`, `header`, and `logo`, each an
absolute path to an existing local file or null. Lookup prefers sorted hashed
subfolders under `<steam>/appcache/librarycache/<appid>/`, then files directly in
that folder, then the older `<appid>_library_600x900.jpg`, `<appid>_library_hero.jpg`,
`<appid>_header.jpg`, and `<appid>_logo.png` layout. Both JPG and PNG are supported.
The filenames in hashed folders are `library_600x900`, `library_hero`, `header`,
and `logo`. Non-Steam shortcut fallback uses the selected user's
`userdata/<user>/config/grid/<appid>{p,_hero,,_logo}.png`. Missing artwork stays
null so the UI can use its own fallback. No artwork is downloaded.

`proton` is null when no installed Proton is usable, otherwise an object with
`name` and `reason`: `pinned` for an installed exact recipe pin, `recipe_fallback`
for the recipe's installed GE/newest fallback, or `newest` without a recipe.
This uses launch's installed-build selector without downloads, extraction, logs,
or state changes. A subsequent successful optional pin download at launch can
supersede the displayed installed fallback.

Each mod keeps `enabled` (wanted) and `installed_version` and adds `state` and
`state_message`. The state is one of:

- `installed`: the manifest matches the recipe version, checksum and target, and
  its contained files still match their recorded checksums.
- `will_install_on_launch`: waiting for a prefix, for the game to close, or for
  the next reconciliation. This label does not claim installation.
- `update_available`: an intact older mod remains installed.
- `off`: the preference is off; the message says if removal must wait for the game.
- `blocked`: content could not be applied safely, for example changed player files,
  an unsafe destination, an unresolved download, or unknown process state.

`state_message` is a short player-facing explanation, also used by `mod_result`.
Detailed containment/download warnings remain in the game's `notes`.

## Immediate mod changes

`mod`, `enable`, and `install` attempt reconciliation when the installed game's
library has `steamapps/compatdata/<appid>/pfx`. For saved/uninstalled games, existing
prefixes are searched across Steam libraries. Without a prefix, installation waits
for launch. Identical hand-installed files are adopted into the ownership manifest;
different or edited files are preserved. Initial Wine/settings work stays on the
launch path. Launch reconciliation remains the safety net.

On Linux, inspect `/proc/<pid>/environ` and `/proc/<pid>/cmdline` for processes owned
by the invoking desktop user. An exact NUL-delimited `SteamAppId=<appid>` environment
entry or a `reaper` command containing adjacent `SteamLaunch`, `AppId=<appid>`
arguments means the game is running. Other users' processes are excluded; exited
processes are skipped. Any unreadable remaining process or unavailable `/proc`
(including Windows development) blocks immediate content writes. This is a
process snapshot, not a lock against an external Steam launch starting afterward.
Steam itself may stay running. Immediate downloads use the same two-second network
budget as launch; all games in one install share that budget. These checks never
start or stop game processes. Not verified on hardware.

Recipe selection compares each game's numeric recipe version first, then the engine
version that published/bundled it. Cached index entries use `engine_version` when
present, otherwise the index's `engine.version`; standalone cached TOML may declare
`engine_version`, otherwise its provenance is unknown/oldest. Bundled recipes use
the running engine version. Equal candidates prefer the resolved indexed recipe.
Install refreshes older indexed recipes (including their checksum and per-entry
engine provenance), invalidates older standalone entries, and preserves newer cached
recipes. The index's engine release metadata is retained independently.

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
A game without a recipe uses the newest installed Proton by parsed numeric version,
including GE-Proton/UMU-Proton under `compatibilitytools.d` and Valve Proton from
all Steam libraries. A `proton` script must exist. Runtime/DeckCompanion/other
non-Proton folders are excluded. Symlink aliases resolve to one target.
`GE-Proton11-11` ranks as (11,11), `UMU-Proton-10.0-4` as (10,0,4),
`Proton 10.0` as (10,0). Experimental ranks as the newest installed Valve major
plus 0.5; GE wins ties. Recipe pins retain their download/installed fallback
(prefer installed GE, otherwise the newest available Proton).
The selected runtime is logged.

Corrupt state/recipes and exceptions during recipe/runtime/content/localfix/state
preparation log a warning and still hand off to the selected or newest installed
Proton with the original game arguments and environment. Logging failures fall
back to stderr. A corrupt state file is preserved. With no Proton available,
launch exits with `no_proton` and a clear log message.
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
the same launch message; immediate warnings are kept in `notes` and mod state.

## Launch splash (0.3.4)

After preparation, `run` and `waitforexitandrun` launch the installed UI as
`<data>/bin/deckcompanion-ui --splash <temporary-json-file>` before execve into
Proton. `DECKCOMPANION_UI` overrides this with one executable path (a bare name
may resolve on PATH); no shell command is evaluated. The file is closed before
spawning and removed after the UI exits. Native ELF/script passthrough runs first,
so the management UI's own Steam shortcut and other native programs never get a
splash. Other Proton verbs do not show it. Off skips the subprocess entirely.

The engine records its child PID, waits at most the configured duration + 2 seconds,
then kills only that child if needed (up to 2 seconds to reap). Missing executables,
no display, crashes, timeout, corrupt settings and payload errors are logged to the
game's existing launch log; the same prepared Proton arguments/environment still
reach execve. Logging failure falls back to stderr. No display on Linux skips the UI.

The UTF-8 JSON contains `name`, `appid`, `art` (the same local paths as GAME),
`proton: {name, reason}` for the actual selected runtime, `recipe_version`,
`fixes: {upstream_chained, wine_steps_count, wine_steps}`, `mods` with metadata,
preference/installed version and `state`/`state_message`, `warnings`, and
`duration_seconds`. `upstream_chained` means our managed localfix was successfully
generated with upstream chaining; it does not assert that Proton has executed it
or that this game has a bundled upstream fix. Wine steps describe configured
Winetricks, registry, DLL and environment work; once-only steps may already have
run on an earlier launch. Failed/disabled localfix generation reports no chain and
zero Wine steps. This is a preparation summary, not a game-success receipt.

Not verified on hardware: Game Mode focus, gamescope window stacking and timing.

## Update and rollback (slice 3a)

`update --json` -> `{"engine":{"current":"0.3.2","available":"0.3.2","updated":false},"index":{"updated":false,"fetched_at":null}}`.
`fetched_at` is an ISO8601 string after the first successful fetch; null if none.
Offline operation uses the cache. A cached release can still install offline.
`rollback --json` -> `{"version":"0.1.0","rolled_back":true}`; no previous version
is an error. See [updates.md](updates.md) for integrity, cache and signing limits.

## Installer commands (slice 3)

`install [--source EXTRACTED_RELEASE] --json` ->
`{"installed":true,"version":"0.3.2","shortcut_pending":true,"steam_changes_pending":true,"restart_required":true,"legacy_tool_preserved":false}`.
Engine versions, compat tool, bin stubs and UI/assets install while Steam is running.
Only Steam VDF writes (shortcut and queued mappings) wait for `apply` with Steam closed.
`steam_changes_pending` includes default/game mappings and the shortcut. Install
queues the global default only when its managed mapping differs. Shortcut entries,
artwork bytes and queued per-game mappings are compared against Steam's current disk
state; matching writes are dropped. Steam-added shortcut properties, ordering and
player tags are preserved. Apply also skips files with identical bytes.
An engine-only update with matching Steam files returns
`{"installed":true,"version":"0.3.2","shortcut_pending":false,"steam_changes_pending":false,"restart_required":false,"legacy_tool_preserved":false}`.
It needs no apply/restart; the installer's unconditional empty-queue apply is harmless.
Installs no packages and changes no
persistent environment.
The source defaults to the current engine/checkout root. UI shortcut is queued
only if the release contains `bin/deckcompanion-ui`. `apply` chooses the Steam user.
`assets/steam/icon.png` (or the legacy `assets/logo/deckcompanion-icon-256.png`)
is refreshed to the stable `assets/icon.png` under the data root for the shortcut.
When writing the shortcut, available `assets/steam/{portrait,wide,hero,logo,icon}.png`
are copied to the selected account's `config/grid/`, using the unsigned 32-bit
shortcut appid: `<appid>p.png`, `<appid>.png`, `<appid>_hero.png`,
`<appid>_logo.png`, `<appid>_icon.png`. Missing images are skipped. Expected source
sizes are portrait 600x900, wide 920x430 and hero 1920x620; the parent supplies art.
Uninstall removes these shortcut images across accounts. The SVG remains an asset.
The standalone/piped installer applies automatically through the installed CLI;
while Steam runs it schedules the detached helper without waiting for shutdown.

`uninstall --json` -> `{"uninstalled":true,"preserved":[],"steam_restart_required":true}`.
It requires Steam closed, restores prior mappings, and preserves user changes and
state/cache/backups. `preserved` lists modified files. See the README for migration.
No command or integration has been verified on hardware.
