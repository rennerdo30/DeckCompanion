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

## Decky Loader and independent plugin

```sh
deckcompanion decky status --json
deckcompanion decky install --json
deckcompanion decky panel --json
deckcompanion decky panel --appid 2300320 --json
deckcompanion decky plugin-status --json
deckcompanion decky plugin-install --json
```

`decky status` returns `installed` (the `~/homebrew/services/PluginLoader` file),
`version` from `~/homebrew/services/.loader.version` (nullable), `service` from
unprivileged `systemctl is-active plugin_loader` (5-second timeout; `unknown` if
unavailable), and `latest: {version, source, stale, checked_at?}`. Latest stable
Loader metadata comes from GitHub `SteamDeckHomebrew/decky-loader/releases/latest`,
cached for six hours. Offline/rate-limited lookup keeps stale cached metadata;
without a cache the latest version is null, never guessed from a bundled pin.

`decky install` returns `{needs_desktop: true, command, instructions, launched}`.
The command is the official fast installer:

```sh
curl -L https://github.com/SteamDeckHomebrew/decky-installer/releases/latest/download/install_release.sh | sh
```

Decky installs a system service and its installer needs root. DeckCompanion never
collects, stores or forwards passwords. In a Linux Desktop graphical session
(DISPLAY or WAYLAND_DISPLAY, outside Game Mode), with Konsole and systemd-run
available, it submits a separate user unit running Konsole and that command.
The official installer prompts for sudo in its own terminal. Submission times out
after five seconds; the detached terminal has a 30-minute maximum lifetime.
`launched: true` confirms scheduling only, not successful installation. In Game Mode,
headless sessions, Windows or failed handoffs, run the command manually in Desktop
Konsole. No automatic session switch, service restart or sudo wrapper is provided.
Rerunning the official installer supplies the supported Loader update/reinstall path.

`decky panel` returns `{game: GAME|null, selection, settings, proton, shortcut_appid}`
without network. It prefers the optional current Steam appid, otherwise a running
game, then the most recently played/launched game from Steam and engine history.
The full-app shortcut is excluded. Empty history returns null, not an arbitrary
recipe. GAME adds the saved nullable `proton_override`; recipe, selected Proton/reason,
mod states and last-launch fields retain their normal meanings. `proton` lists
installed builds. `shortcut_appid` is the unsigned decimal appid of the actual owned
shortcut, or null until installed/applied for the active Steam account. General
`status` also adds this field; the plugin passes it to `SteamClient.Apps.RunGame`.

The panel uses `enable`/`disable`, `mod`, `game-option --proton` (or `auto`) and
`settings --splash`. Mapping changes stay queued, with an Apply message. Every backend
subprocess is recorded, has a 20-second timeout and a 2-MiB stdout cap, and is cleaned
up on timeout/unload. Registered `kind: non_steam` games expose mods and splash but
disable routing and Proton choice by default. Future GAME `options.takeover` and
`options.proton` booleans override that default as engine support becomes available.

`decky plugin-status` returns the installed plugin version, `decky_installed`,
`plugins_writable`, `can_install`, instructions and independent `latest` metadata.
It queries GitHub releases (up to ten 100-item pages within the network budget),
selecting the highest stable `decky-vMAJOR.MINOR.PATCH` with a matching
`deckcompanion-decky-<version>.zip`, repository HTTPS URL, size at most 8 MiB and
GitHub `sha256:` digest. Engine/prerelease tags are ignored. This has a separate
six-hour cache and stale fallback. Before a plugin release exists, it reports
`source: unavailable` and `can_install: false`.

`decky plugin-install` checks the downloaded SHA256 before writes. It requires
installed Decky and existing user-writable `~/homebrew/plugins`. It extracts only
expected files with an expansion limit and matching package version. No root,
chmod/chown or password handling. Modified/unowned plugins are preserved with
`reason: existing_plugin_preserved`. Engine-managed updates verify the old receipt,
retain `.DeckCompanion-previous-<timestamp>`, and roll back on activation/receipt
failure. Bytecode caches do not count as edits. `changed` distinguishes update/no-op;
reload plugins in Decky after success. Receipt: `decky-plugin-installed.json` in the
engine data directory. Engine versions, recipes and the current pointer are untouched.
For root-owned directories or manual plugin updates, use Decky's developer
**Install Plugin from ZIP** action. Manual installations remain Decky-owned.

Build the plugin separately under `decky-v<version>`; it never enters an engine
tarball or requires an engine version bump. See `decky-plugin/README.md` for builds,
verified dependency versions and open hardware checks.

## Non-Steam Windows games

```sh
deckcompanion add-game --exe "$HOME/Games/Example/Game.exe" --name "Example" --store gog --umu-id umu-42 --json
deckcompanion umu install --json
deckcompanion apply --restart-steam --json
deckcompanion run-umu example
deckcompanion remove-game example --json
```

`add-game` requires an existing `.exe` and a name. Optional store is `egs`, `gog`
or `none` (default). Returns a GAME with `kind: "non_steam"`, `slug`, `store`,
`umu_id`, numeric shortcut `appid`, and `shortcut_pending: "add"`. `games` includes
these entries; `game APPID` reads detail and logs. Identical executable paths cannot
be registered twice. Name collisions get a path-derived suffix. Removed registrations
are retained as recovery records and their prefixes are never reused for another game.

Both add and remove queue Steam writes. `status.pending` and `apply.applied` contain
`{"slug":"example","action":"add"}` / `"remove"`; existing closed-Steam guards,
backups and detached Linux apply apply to them. The shortcut executes the installed
`bin/deckcompanion` stub with `run-umu SLUG`, uses the executable's parent as StartDir,
and reuses DeckCompanion's installed icon. Removal preserves game files, manifests
and saves under `prefixes/SLUG/`; it returns `prefix_preserved: true`.

`run-umu` executes the external zipapp with `WINEPREFIX=prefixes/SLUG/pfx`,
`GAMEID=umu-ID`, `STORE` and absolute `PROTONPATH`. It reuses installed Proton ranking
with GE preferred, recipe pins and the bounded optional-content/splash path. Mods and
settings reuse the normal manifest ownership and containment checks. First-launch
prefix content may be deferred until umu initializes `pfx`; it applies on next launch.
`mod APPID MOD --on|--off` saves non-Steam preferences for the next launch.
Non-Steam games use Remove instead of Steam compatibility mapping enable/disable.
Recipe files are named `recipes/umu-ID.toml`, using the existing schema with
`appid = "umu-ID"`; recipe version/cache selection and validation are shared.

An explicit umu ID is used directly (a missing `umu-` prefix is added). Otherwise
registration tries an exact, unique title/store match in the official umu-database
CSV, cached at `cache/umu-database.csv`. Offline, unknown and ambiguous matches use
`umu-default`. No fuzzy title guesses. Registration never requires network success.

`umu install --json` downloads official umu-launcher **1.4.4**, verifies its published
archive SHA256 (`eb590691841f7fad3fc3ad8fd5db4ccb87849fe7948e62b28ece7a4ee48cc851`)
and pinned LICENSE SHA256, and atomically installs both into `umu/1.4.4/` under the
data root. Existing modified installations are preserved and reported as conflicts.
`update` attempts the same installation and adds an `umu` result; offline failures
leave the previous runtime intact and do not fail the engine update. Runtime download
has a bounded 20-second budget; launch uses its shared short budget and tells the user
to run `umu install` if it cannot install. Required missing Proton/umu is an actionable
error; optional recipe, content and splash failures log and continue the handoff.
The GPL-3.0 zipapp runs only as a separate process. No runtime code is imported,
vendored or included in the MIT release tarball. **Not verified on hardware.**

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

- `settings --json` -> `{"splash":true,"splash_seconds":4.0,"show_games_without_recipe":true,"check_updates_on_start":true,"auto_install_ge":true,"cleanup_old_ge":true,"takeover_all":true}`.
  Returns all persisted settings plus defaults; reading does not write the state file.
  `settings --set key=value [--set key=value ...] --json` validates all entries before
  saving. Boolean keys `splash`, `show_games_without_recipe`,
  `check_updates_on_start`, `auto_install_ge`, `cleanup_old_ge` and `takeover_all` accept true/false or on/off; all default on. Numeric
  `splash_seconds` defaults to 4 and clamps finite numbers to 1–10. Unknown keys,
  malformed entries, invalid booleans and nonfinite numbers fail without partial writes.
  `settings --splash on|off --splash-seconds N --json` updates either or both values
  under `settings` in state, preserving other settings and game data. Defaults are
  on and 4 seconds; finite durations clamp to 1–10 seconds. Changes take effect on
  the next game launch and need no Steam restart (except `takeover_all`, described below). Nonfinite values are rejected.
  Splash flags are aliases and take precedence over `--set` for the same key.
  Library visibility and automatic update checks are UI preferences; the UI runs
  `update --detach` once on open when enabled. Global
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
`steam -shutdown`, and polls every 50 ms for both `steam` and `steamwebhelper` to
disappear, within 120 seconds per cycle including the shutdown command. Before
shutdown it takes the state lock, serializes the queued transaction and stages
backups. The closed interval only commits the transaction, without further process
queries or lock waits. Disk failures roll back all written files, retaining the queue.
After Steam returns it verifies mapping values, owned shortcut fields and artwork
hashes. A missed window or overwritten values triggers one more shutdown/write cycle.
After two failed cycles, `apply-last.json` reports a clear error and retains the queue.
The state lock stays held until verification; read-only status remains available.

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
`steam_restarted`, `attempts`, `verified`, `errors` (code/message objects), and `timings`
(seconds for prepare and the last shutdown/apply/restore phases and total).
An in-flight receipt has no `finished_at`;
an interrupted or service-timeout run can leave that receipt for diagnosis. A second
helper fails with `busy` without touching the active receipt or Steam. The internal
CLI exits 1 when the receipt contains errors. Not verified on hardware.

## Installed-game takeover and Valve recommendations

`takeover_all` defaults **on**. `settings --set takeover_all=on|off --json`
returns the complete SETTINGS object above and queues mapping changes in state.
`game-option APPID --takeover off --json` excludes one game and queues restoration;
`--takeover on` clears its exclusion (it still respects the global setting).
These commands return the same GAME object as `game APPID --json`.
`disable APPID` also sets an exclusion; `enable APPID` clears it.
`game-option APPID --proton "Proton 10.0" --json` persists an engine runtime override;
`--proton auto` clears that explicit override. Takeover and Proton options can be combined.

`apply`, `install` and `update` discover new installations and reconcile takeover.
An otherwise empty `apply` still discovers new games. `update` adds the boolean
`steam_changes_pending` to its existing result. Settings and discovery never write
Steam files. Queued writes use the existing `apply --restart-steam` detached helper,
backups, semantic verification and one retry; use `status.pending` and
`status.last_apply` to follow them. Global mapping `0` remains independently controlled
by `default --on|--off`; takeover-off restores per-game entries only.

Steam titles must have a library `appmanifest_*.acf`. Classification skips
uninstalled manifests (`StateFlags & 1`) and appinfo types other than game/application.
Explicit manifest `UserConfig/platform_override_dest` (or manifest `oslist`) wins.
Otherwise, match `InstalledDepots` against appinfo depot `config/oslist`: Linux
depots mean native, Windows depots mean Windows. If depot evidence is absent, use
`common/oslist`, then the full depot OS list; include Windows-only entries, skip
Linux-capable or unknown entries. Thus a cross-platform game with an installed
Windows depot is included, while its installed Linux build is skipped. Missing or
unreadable appinfo does not turn unknown games into Windows games.
Existing shortcuts in every `userdata/*/config/shortcuts.vdf` are eligible only
when their quoted/unquoted `Exe` ends in `.exe` (case insensitive). Their unsigned
32-bit appid is used. Native shortcuts and the UI/umu native wrappers are excluded;
shortcut contents are never changed by takeover. `.exe` shortcuts appear in GAMES
with `kind:"shortcut"`; registered umu games retain `kind:"non_steam"`.

Every eligible game gets `{"name":"DeckCompanion","config":"","priority":"250"}`,
including games with an existing per-game tool. State snapshots the complete previous
entry before queueing the first write; repeated apply/install/update retains it.
Global off, exclusion, removal of the installation/shortcut, and uninstall restore
that exact entry (including extra fields), or remove it if none existed. As with
manual disable, a later foreign mapping is preserved during restoration. State,
cache and backups remain recovery data. Uninstall suppresses future takeover until
the next install.

GAME and each GAMES row add these exact fields:

```json
{"takeover":"on","previous_mapping":"proton-stable","valve_recommendation":"proton-experimental",
 "proton":{"name":"Proton 11.0","reason":"user_override"}}
```

`takeover` is `"on"` when eligible, globally on and included (wanted, possibly pending),
`"excluded"` for an eligible game with a per-game exclusion, otherwise `"n/a"`.
`enabled` and `pending` retain their applied/queued meanings. `previous_mapping`
is the saved previous tool name or null; the complete entry is internal state.
Registered umu wrappers report `takeover:"n/a"`, `previous_mapping:null` and
`valve_recommendation:null`. `valve_recommendation` is the raw local runtime string
or null, even when an override or recipe wins. No installed runtime means `proton:null`.
STATUS adds these exact fields (example counts):

```json
{"takeover_all":true,"takeover_counts":{"eligible":4,"on":3,"excluded":1,"applied":2,"pending":1}}
```

`eligible` counts installed Windows titles/shortcuts; `on`/`excluded` count wanted
states. `applied` counts enabled takeover-owned entries (including pending restores);
`pending` counts takeover-owned queued enables/restores. Discovery itself is read-only;
pending counts reflect the persisted queue after a mutating command.

Appinfo is read-only and streamed by sized record, supporting magic `0x07564428`
and `0x07564429` (the latter uses the trailing key string table). Unknown binary
value types skip their containing record; malformed/truncated files fall back to
no metadata. Extracted OS data, recommendation configuration and Steam Play tool
maps are cached in `<cache>/appinfo.json` by absolute source path, nanosecond mtime
and size; missing or changed sources never reuse stale metadata.

The checked field names are `common/steam_deck_compatibility/configuration/recommended_runtime`,
and app `891390`'s `extended/app_mappings/<appid>/tool` plus
`extended/compat_tools/<tool>/appid` (see [Protontricks' implementation](https://github.com/Matoking/protontricks/blob/master/src/protontricks/steam.py)
and [SteamAppInfo's format specification](https://github.com/ValveResourceFormat/SteamAppInfo#appinfovdf)).
The cache retains the complete Deck `configuration`, including unknown keys, and
the two Steam Play maps exactly as parsed (key names normalized to lowercase).
The Deck profile wins over an app-mapping override. No real Deck appinfo was inspected.

Selection precedence is installed exact recipe pin, explicit user override (or
the game's saved pre-takeover Steam tool), then Valve recommendation for recipe-less
games, then the existing fallback. A recipe without an installed pin retains its
GE/newest fallback and does not use Valve metadata. Reasons are `pinned`,
`user_override`, `valve_recommended`, `recipe_fallback`, or `newest`.
`proton-experimental` selects `Proton - Experimental`; `proton-stable` selects the
newest installed numbered Valve build; `proton_10`/`proton_10.0` and explicit build
names match installed versions. Other tool IDs use `compat_tools` appids to find
the installed build. Unavailable recommendations (including `native` when handed
a Windows executable) log the raw value and fall back to newest; no runtime is
downloaded. An unavailable user override falls back without trying Valve's choice.
Launch and GAME/GAMES use the same selector; splash `proton.reason` reflects it too.

**Not verified on hardware:** real SteamOS appinfo parsing, classification of actual
installed depots/shortcuts, Steam recognizing per-game takeover after retry, exact
restoration and launching via Valve's recommended runtime. Parent owns those checks.

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
`name` and `reason`: see the selection precedence and reasons above.
This uses launch's installed-build selector without downloads, extraction, logs,
or state changes. Launch never downloads a Proton runtime; use update or install.

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
A game without a recipe or usable override/recommendation uses the newest installed Proton by parsed numeric version,
including GE-Proton/UMU-Proton under `compatibilitytools.d` and Valve Proton from
all Steam libraries. A `proton` script must exist. Runtime/DeckCompanion/other
non-Proton folders are excluded. Symlink aliases resolve to one target.
`GE-Proton11-11` ranks as (11,11), `UMU-Proton-10.0-4` as (10,0,4),
`Proton 10.0` as (10,0). Experimental ranks as the newest installed Valve major
plus 0.5; GE wins ties. Recipe pins retain their installed fallback
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
advance for GE archives; runtime downloads never run on the launch path.

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

The splash defaults to 4 seconds (1–10 in Settings). Its visible **A Skip** and
**Y Hold** buttons also accept touch/mouse input; B skips. Hold pauses countdown
and labels progress **Paused**. **A Continue** starts the game immediately; a held
splash starts automatically after 120 seconds.

The engine records its child PID, waits at most the duration + 120 + 2 seconds
(capped at 130 seconds),
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

`proton --json` is a local-only read returning `{builds:[...],active,job,status_file}`.
Each row has `name`, `state`, `installed`, optional `path`, `wanted_by` (recipe names),
`latest`, `installed_bytes`, and progress fields `percent`, `downloaded_bytes`,
`total_bytes` or `error`. States: wanted, scheduled, downloading, verifying,
unpacking, installed, failed. Latest is unknown until metadata has been fetched.

`proton install NAME --json` downloads, verifies and installs a versioned GE build.
`--detach` submits a bounded Linux user service and returns
`{scheduled:true,active:true,status_file:".../proton-job.json"}` immediately.
Poll `proton --json`; atomic per-build receipts are in `proton-status/NAME.json`.
The job file holds active state, PID once started, completion result or failure.
Scheduling does not confirm installation. Retry resumes interrupted partial files.
Only one install/update worker runs; polling and settings remain available.

`update --detach --json` schedules engine/index and GE updates through the same
worker. Its final result is in `job.result`, including `engine`, `index` and `proton`.
Synchronous `update --json` adds `proton` with builds, removed names and errors.
GE failures are reported in `proton.errors` and the job receipt without discarding
a successful engine/index update. See [updates.md](updates.md) for budgets/cleanup.

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
