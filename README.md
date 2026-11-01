# DeckCompanion

An offline-capable recipe engine and controller-friendly companion for Steam Deck.
Python >= 3.11, standard library only at runtime; vendored MIT `vdf` handles Steam's
text and binary formats. Target: SteamOS 3.9. Project license: MIT, rennerdo30.

## Install (Desktop Mode)

Close Steam first. Download `DeckCompanion-Installer.desktop` from a reviewed
release, mark it executable if Desktop Mode requests it, and double-click. It
gets `install.sh` over HTTPS with timeouts. The standalone installer retrieves
an index, verifies the engine SHA256, and installs without root, pacman, pip,
system services or persistent environment changes. Released assets must exist
before the online installer works; this repository does not publish automatically.

From an extracted release or checkout, run `sh install.sh`. The engine lives in
`~/.local/share/deckcompanion/versions/0.2.0/`, with a stable compat launcher in
Steam's `compatibilitytools.d/DeckCompanion/` and a CLI at
`~/.local/share/deckcompanion/bin/deckcompanion`. A release containing
`bin/deckcompanion-ui` also installs its launcher and queues a non-Steam shortcut.
The installer asks to apply the queue and start Steam; alternatively run:

```sh
~/.local/share/deckcompanion/bin/deckcompanion apply --restart-steam --json
```

Select DeckCompanion as a game's compatibility tool in Steam, or use the UI to
enable games and apply changes. Engine-only releases work without a UI. No PATH
changes are made; use the full CLI path or your own shell alias.

## How it works

- Recipes select pinned GE-Proton, environment, Wine fixes, mods and initial settings.
  Games without recipes use newest installed GE, then Valve Proton.
- Cached recipes/assets work offline. Optional launch downloads share a two-second
  wall budget. An unavailable pin falls back to installed Proton and logs why.
  Preinstall large runtimes: launch is intentionally too short for most GE downloads.
- Managed protonfixes localfixes chain the bundled upstream fix first. User localfixes
  are preserved. Wine installs/registry steps run once per prefix.
- SHA256 and manifests track mods. FS25 Deck Mode stays zipped in FS25's mods folder.
  Existing/user-modified files are preserved and logged.
- Content waits until the next launch after Proton creates `pfx`. Initial settings
  use per-prefix stamps and never overwrite later changes.
- Steam mappings and shortcuts change only with Steam closed, with backups. Queue
  enable/disable, then apply. No CEF or remote debugging.
- `update` installs verified releases and atomically switches `current`; `rollback`
  selects the previous engine. No launch-time Git operations remain.

Everything lives under your home directory, surviving SteamOS system updates.
All roots can be overridden for tests. See [CLI contract](docs/cli.md),
[recipes](docs/recipes.md), and [updates/signing design](docs/updates.md).

## Migration from the 2022 prototype

The root `DeckCompanion` and `toolmanifest.vdf` command path remain. Old git-clone
installs receiving this revision use a migration shim on their next invocation:
it executes the installed engine, or falls back to installed Proton while logging
how to run `install.sh`. No old scripts or self-updates run in the shim. Install
with Steam closed. The installer preserves the clone's Git metadata and tracked
compatibility launcher, rather than deleting or overwriting that checkout.

An already-running old launcher may finish its old code after its last `git pull`;
restart the game to enter the shim. 602520/2087030 retain useful historical Wine
verbs; 1063730's missing `fix-eac.sh` was dropped.

## Uninstall

Close Steam and run `sh uninstall.sh` from a release/checkout, or
`~/.local/share/deckcompanion/bin/deckcompanion uninstall --json`. This restores
prior compatibility mappings when ours is selected, removes our shortcut/localfixes
and unmodified installed files, and removes unchanged manifest-owned mods.
Modified files are listed and preserved. State, cache, manifests, logs and Steam
backups remain for recovery; an old clone is preserved. Prefixes, saves, games,
downloaded Proton runtimes and unrelated shortcuts are never removed.

## Development and local releases

Set `PYTHONPATH=src` and use `python3 -m deckcompanion`. Pytest is dev-only:
`python3 -m pytest -q tests`. Tests use temporary trees, fake archives and mocked
network/process calls. No Steam or remote device needed.
`python3 scripts/build_release.py --output dist` resolves API metadata and produces
engine/index/checksums and installer assets locally; nothing publishes.
`--offline` produces a development bundle with unresolved source mods (not installable
until a resolved index is fetched). Optional exported UI input: `bin/deckcompanion-ui`.

## Not verified on hardware

No Steam Deck or remote device was connected. Real SteamOS shutdown/restart,
Steam mapping/shortcut recognition, GE's protonfixes lifecycle, prefix timing,
controller UX and game/mod behavior are **not verified on hardware**.
`last_launch.ok` records preparation, not game success. Symlink redirects from
Documents outside a prefix are refused and logged. SHA256 trusts the HTTPS index;
publisher signature verification is designed but not implemented.

Use reviewed recipes/releases, back up saves, close Steam before disk integration,
and try disposable prefixes first. No `sudo`, system partition writes, telemetry,
credential access or automatic uploads are required.
