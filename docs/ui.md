# DeckCompanion management UI

Godot **4.7.2-stable**, GDScript, MIT. The [official release](https://github.com/godotengine/godot/releases/tag/4.7.2-stable) was published August 18, 2026. The portable Windows build was checked against the release's `SHA512-SUMS.txt`. No installer, PATH or registry changes. Only built-in nodes/fonts and original primitive artwork are used.

## Run

```powershell
E:/Development/_cache/godot/4.7.2/Godot_v4.7.2-stable_win64.exe --path ui -- --mock
```

Or set `DECKCOMPANION_UI_MOCK=1` for this process. Optional mock flags: `--scenario=offline` makes an update check fail while keeping the library usable; `--scenario=error` makes Apply fail without losing pending edits; `--scenario=empty` supplies an empty library. Mock changes last for this session only. The banner always labels mock data. The mock restart never closes Steam.

Without mock mode, `DECKCOMPANION_CLI` supplies one executable path (not a shell command). Otherwise the resolver tries `~/.local/share/deckcompanion/bin/deckcompanion`, then the original `~/.local/share/deckcompanion/current/bin/deckcompanion` contract. Every invocation appends `--json` after the command and arguments. The core sibling can supply a launcher wrapping `python3 -m deckcompanion`; no shell command interpolation is used.

## Controls

| Input | Action |
| --- | --- |
| D-pad / left stick; arrows / Tab | Move focus; focused controls scroll into view |
| A; Enter / Space | Confirm or activate |
| B; Escape | Back; cancel restart dialog |
| X; X | Cycle library filters; enable/disable in detail |
| Y; Y | Open Apply confirmation |
| L1 / R1; Q / E | Cycle Games / Updates / Settings, backwards / forwards |
| Right stick; Page Up / Page Down | Scroll library preview or launch log in detail |
| Mouse / touch | Activate controls and scroll |

The restart dialog initially focuses **Keep editing**. Activation is serialized during engine operations. Cancel/back returns to the selected library game. Drawn face-button and shoulder glyphs are the default; keyboard input switches the footer to keyboard labels, and gamepad input switches it back. The green outline identifies keyboard/controller focus. The Enabled filter reflects applied state; pending Steam changes remain labelled until Apply.

The library previews the focused game immediately: local artwork, fix state, selected Proton and reason, mod states, last launch handoff/time and the first line of recipe notes. `last_launch.ok` confirms preparation/handoff, not that the game completed. The slogan appears only with an empty library. The detail screen reuses artwork as a banner.

Mod rows separate preference (`[On]` / `[Off]`) from content: Installed, Will install on next launch, Update available, Off, or Blocked with the engine's explanation. Older engines without `state` use `installed_version`; a preference checkmark never stands for installation. Mod toggles show `mod_result.message`, or **Saved. Takes effect on next launch.** when absent. They never add mock pending Steam changes. Enable/disable/default changes retain the Apply notice.

## Architecture

Launch mode: `deckcompanion-ui --splash <UTF-8 JSON file>` routes through
`boot.tscn` before constructing any management UI/backend. It is fullscreen and
uses only the supplied launch summary and local artwork; no engine CLI or network
calls. The first frame uses generated artwork; the existing image worker decodes
hero/header/portrait while the logo loads through Godot's threaded resource loader.
A/B on any device, Enter/Escape, touch or left click skips; duration completion and
skip both exit 0. Bad/missing JSON exits 1 so the engine can log and continue.

**Settings → General → Launch splash** saves on/off and a 1–6 second duration
through `settings`. Focusable 0.5-second decrement/increment buttons work with the
normal controller navigation, and the card scrolls into view. Preferences save
immediately, without Apply/restart. The mock backend retains them for its session.

Settings is the third tab. General also contains the global default-tool control,
which still queues a change for Apply. Library can hide games without a recipe.
Updates controls automatic checks on open (default on) and Check now; its separate
backend keeps navigation available during the request. The Updates tab contains
engine/index/Proton status only. About shows engine/UI versions, the install path,
the exact uninstall command (no uninstall button), and scrollable MIT/third-party
dialogs. `ui/legal/` copies the repository MIT and third-party/vdf notices; the
dialog also includes Godot's runtime license texts. The export bundles those files.

All management scroll areas (including nested launch logs and license dialogs) use
`drag_scroll.gd`: native touch or left-held mouse motion, a 12 px threshold, deferred
button cancellation and light inertia. Taps keep their normal button action.
Nested scroll areas own their gesture exclusively; keyboard/controller input stops
inertia, while focus-follow and right-stick scrolling retain their usual behavior.
Splash details use the same helper; a tap skips and a swipe scrolls the summary.

Splash fixture capture (run offscreen with the window positioned outside the desktop):

```powershell
E:/Development/_cache/godot/4.7.2/Godot_v4.7.2-stable_win64.exe --path ui --resolution 1280x800 --position -32000,-32000 --max-fps 30 -- --splash res://fixtures/splash.json --splash-capture=<absolute-repo>/ui/docs/screenshots/splash.png
```

The capture switch keeps this fixture run windowed; normal launch mode is fullscreen.
`ui/tests/run.gd` covers fixture rendering, artwork/fallback, warnings, mod states,
skip/duration, both viewport sizes and settings round-trip. `ui/tests/splash_mode.gd`
also exercises the real boot route with `-- --splash res://fixtures/splash.json` and
lets it exit automatically. The new tests fail against archived `c88008b` (four UI
failures for DC-18); existing UI checks remain green. The screenshot is mock artwork, not a
Steam Deck capture. **Not verified on hardware:** splash focus, stacking and timing.

- `main.tscn` / `scripts/main.gd`: responsive containers, library/detail/status screens, loading/error/empty states, restart confirmation and input hints. Base layout 1280×800; canvas scaling supports desktop windows and 1920×1080.
- `scripts/backend.gd`: one command at a time, signal completion, JSON/shape validation and a stateful fixture backend. Production `OS.execute` runs on a worker `Thread`, never the UI thread. Linux uses `/usr/bin/timeout` with 45 seconds plus a 3-second kill grace; Windows uses the bundled process runner with a 45-second timeout. A timed-out mutation prompts a refresh rather than claiming success. Exit joins the bounded worker.
- `scripts/art_cache.gd` / `game_art.gd`: one image-decoding worker, latest waiting selection, eight cached results (including failed paths), maximum decoded upload size 1280×800. Prefer hero, then header, then portrait; missing files use an original palette-based geometric landscape. Texture upload runs on the main thread after decoding. Focus changes retain the preview nodes, and stale completions cannot replace the current game's image.
- `scripts/game_text.gd`: shared preview/detail labels and backwards-compatible optional fields. A separate backend instance reads `game APPID` only when the library object lacks notes, caches eight note summaries, and never disables navigation. No network art requests.
- `scripts/navigation.gd`: pure filters, wraparound navigation and explicit focus wiring. `hints.gd` draws original controller glyphs.
- `fixtures/`: FS25 (`2300320`, `FS25_DeckMode` v0.7.0), local art, Proton reasons, mod states, no-art/no-recipe, failed-launch, pending, error and offline examples. Original fixture PNGs can be regenerated with `tests/create_fixture_art.gd`; provenance is in `fixtures/art/README.md`. Updates are checked only on request, with index age shown after the engine returns its timestamp.

**Renderer:** Compatibility (OpenGL 3.3), because this is a static 2D management interface needing no Vulkan features. It avoids Vulkan pipeline overhead and maximizes compatibility; this choice is not a Deck performance measurement. The app runs windowed by default; Steam/gamescope owns presentation when launched as a non-Steam game.

## Checks and screenshots

```powershell
E:/Development/_cache/godot/4.7.2/Godot_v4.7.2-stable_win64_console.exe --headless --path ui --import
E:/Development/_cache/godot/4.7.2/Godot_v4.7.2-stable_win64_console.exe --headless --path ui --script res://tests/run.gd -- --mock
```

The focused runner covers JSON/schema failures, stateful mock enable/mod/apply, pending preservation on errors, offline library access, filters, actual focus paths, input hints, tab input and cancel-first confirmation. The 0.3.2 checks exercise D-pad preview changes, image fallback/cache/stale results, background notes, every mod state, mutation notices and legacy objects. The runner was also applied to archived `3e55e57` with no implementation changes: 12 failures reproduce the missing preview, incorrect mod labels, mock pending changes and toggle messages. `--mutate-navigation` deliberately reverses navigation in an in-memory script copy: the focus checks must fail (exit 1); no source files change.

Capture three real viewport PNGs from a short windowed mock run (self exits):

```powershell
E:/Development/_cache/godot/4.7.2/Godot_v4.7.2-stable_win64.exe --path ui --resolution 1280x800 --max-fps 30 -- --mock --capture-dir=<repo>/ui/docs/screenshots
```

Review captures: `ui/docs/screenshots/games.png`, `detail.png`, `updates.png`. Automated capture used an offscreen hidden window to avoid taking desktop focus. Runtime output/logs live outside Git in the portable tool cache.

## Linux export

`export_presets.cfg` defines **Linux x86_64**, with embedded PCK and output `ui/build/deckcompanion-ui`. The single release binary is intended for the stable installed path `~/.local/share/deckcompanion/bin/deckcompanion-ui` (matching the core sibling's payload `bin/deckcompanion-ui`). The parent owns packaging/integration.

The matching Linux release template is installed in Godot's user template directory. Export with an absolute output path (replace the checkout path below for another worktree). The generated binary is ignored and is not committed:

```powershell
New-Item -ItemType Directory -Force ui/build
E:/Development/_cache/godot/4.7.2/Godot_v4.7.2-stable_win64_console.exe --headless --path ui --export-release "Linux x86_64" E:/AgentData/DeckCompanion/t3-807c8d8b/ui/build/deckcompanion-ui
```

**Not verified on hardware:** Steam Deck/Game Mode launch, gamescope focus, physical controller mappings, touch, real Steam restart and frame time/power. No Steam Deck or remote device was contacted. Windows screenshots, headless logic checks and a Linux export do not establish hardware acceptance. The parent owns deployment and integration with the parallel 0.3.2 engine.
