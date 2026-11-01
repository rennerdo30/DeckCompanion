# Upstream protonfixes and managed localfixes

`third_party/umu-protonfixes` is an unmodified development/test submodule of
https://github.com/Open-Wine-Components/umu-protonfixes, pinned to
`f4940924eefd2c973f5ec57dac26aa3dcefb33ca`. This matches the protonfixes gitlink
in **GE-Proton11-11**, the FS25 recipe's runtime pin.

Pin evidence supplied by the parent on 2026-11-01: GitHub's
[`repos/GloriousEggroll/proton-ge-custom/contents/protonfixes?ref=GE-Proton11-11`](https://api.github.com/repos/GloriousEggroll/proton-ge-custom/contents/protonfixes?ref=GE-Proton11-11)
returned `type: submodule`, `sha: f4940924eefd2c973f5ec57dac26aa3dcefb33ca`, and
the submodule URL `https://github.com/open-wine-components/umu-protonfixes`.
The implementation session found this commit in the clone and checked it out
without additional network access. It did not independently query the GE API.

## Development and CI

From the repository root, initialize only this submodule:

```sh
git submodule update --init third_party/umu-protonfixes
python3 -m pytest -q
```

Use a development virtual environment with pytest installed. CI jobs that need
upstream coverage should run that same non-recursive initialization command
after checkout. With no initialized submodule, the upstream tests skip cleanly;
engine and packaging tests still run. CI checkout must leave recursive submodule
checkout disabled. Do not use `--recursive` or initialize upstream's own
`subprojects/` dependencies: these include umu-database (GPL-3.0), winetricks
(LGPL), and other separately licensed projects that our tests do not need.

`scripts/build_release.py` uses an explicit payload allowlist and rejects any
`third_party` directory through its recursive archive filter. The entire submodule
and its dependencies are excluded from release tarballs. Release builds require
no submodule initialization. The runtime always uses the selected Proton's bundled
protonfixes, never the development submodule. Its BSD-2-Clause notice is retained
in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

## Loader contract and test boundary

The upstream loader checks `~/.config/protonfixes/localfixes/<appid>.py` first.
A localfix replaces that app's bundled fix; it is not automatically appended.
DeckCompanion marks generated files with `# managed-by: DeckCompanion` and refuses
to overwrite or remove foreign files.

The generated `early()` and `main()` explicitly import the active package's
`protonfixes.gamefixes-steam.<appid>` module. Importlib accepts the hyphenated
directory name. For each stage, `<stage>_with_id(appid)` takes precedence over
`<stage>()`, matching upstream's loader. The main hook runs upstream first,
then recipe installs/registry under the real `@util.once(retry=True)` decorator,
then per-launch DLL overrides/environment. A recipe install exception or explicit
`protontricks` false return logs a warning, leaves no success stamp, and retries
on the next launch; runtime settings are still attempted. Runtime failures are
logged without undoing the already-executed upstream fix. Upstream hook failures
retain upstream's own exception behavior.

Tests load real upstream `fix.py`, `util.py`, configuration and per-game modules
under a namespace package, bypassing `__init__.py`'s Proton/GUI startup. HOME,
USERPROFILE, XDG roots, Steam variables and prefix point at temporary directories.
The logger and side-effecting util operations are recorded; process/network calls
are denied and Linux `/proc` checks are disabled. The real loader discovers our
generated files, and the real once decorator writes stamps in the temporary prefix.

Coverage uses FS25 (`2300320`, no app-specific upstream fix at this pin), Assetto
Corsa (`244210`, real early/main hooks), and Oblivion (`22330`, real main_with_id).
FS25's bundled default fix still runs; both its shipped recipe and a representative
Wine overlay are tested without changing the shipped recipe. Tests cover ordering,
per-prefix stamps, recipe revisions, retry after failures and foreign-file safety.
They prove Python loader behavior without launching Proton or Wine.

## Bumping the pin

1. Choose the intended GE-Proton tag and inspect its `protonfixes` gitlink using
   the GitHub contents API above (substitute the tag), or `git ls-tree <tag> protonfixes`
   in an existing GE checkout. Record the tag, SHA and source here.
2. Fetch that commit into `third_party/umu-protonfixes` with a bounded Git command,
   then `git -C third_party/umu-protonfixes checkout --detach <sha>`. Never initialize
   nested submodules. If a matching tag cannot be determined, record why before
   choosing and recording the current upstream default-branch commit.
3. Run the upstream tests plus the engine suite with the submodule, then run the
   suite in a source archive without it to verify clean skips. Review upstream's
   loader/API/license changes and confirm no `third_party/` tar members.
4. Commit the updated gitlink and this document together. A pin bump is a test
   baseline update, not a replacement of users' bundled runtime code.

## Parent hardware check (DC-7)

**Not verified on hardware.** Deploy through the parent's approved flow, launch
FS25 with the pinned GE-Proton and capture Proton/protonfixes stderr/log output.
Look for the bundled default's `main stage global defaults`, the loader's
`main stage local protonfix`, `DeckCompanion: no upstream gamefix for 2300320`
(expected at this pin), then `DeckCompanion: recipe 2300320 main`. An installed
Proton with an FS25-specific fix should instead show `DeckCompanion: upstream
2300320 main` before the recipe line. Check early-stage ordering where applicable.
Verify actual launch success, prefix timing, repeat-launch behavior and Deck Mode
adoption separately; engine preparation logs alone do not prove game success.

## Contributing generic fixes

Send broadly applicable Wine/Proton fixes to
[umu-protonfixes](https://github.com/Open-Wine-Components/umu-protonfixes) with the
appid/store, Proton version, reproducible failure, minimal fix and launch evidence.
Follow its contribution guidance and existing gamefix API. Keep DeckCompanion
recipes for user choices, mod delivery and settings; avoid duplicating generic
fixes once the selected runtime includes them upstream.
