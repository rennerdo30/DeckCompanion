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
| L1 / R1; Q / E | Switch Games / Updates |
| Right stick; Page Up / Page Down | Scroll launch log in detail |
| Mouse / touch | Activate controls and scroll |

The restart dialog initially focuses **Keep editing**. Activation is serialized during engine operations. Cancel/back returns to the selected library game. Drawn face-button and shoulder glyphs are the default; keyboard input switches the footer to keyboard labels, and gamepad input switches it back. The green outline identifies keyboard/controller focus. Mod controls show staged enabled state, available version and installed version separately. The Enabled filter reflects applied state; pending changes remain labelled until Apply.

## Architecture

- `main.tscn` / `scripts/main.gd`: responsive containers, library/detail/status screens, loading/error/empty states, restart confirmation and input hints. Base layout 1280×800; canvas scaling supports desktop windows and 1920×1080.
- `scripts/backend.gd`: one command at a time, signal completion, JSON/shape validation and a stateful fixture backend. Production `OS.execute` runs on a worker `Thread`, never the UI thread. Linux uses `/usr/bin/timeout` with 45 seconds plus a 3-second kill grace; Windows uses the bundled process runner with a 45-second timeout. A timed-out mutation prompts a refresh rather than claiming success. Exit joins the bounded worker.
- `scripts/navigation.gd`: pure filters, wraparound navigation and explicit focus wiring. `hints.gd` and `landscape.gd` draw original primitives.
- `fixtures/`: FS25 (`2300320`, `FS25_DeckMode`), no-recipe, failed-launch, pending, error and offline examples. Updates are checked only on request, with index age shown after the engine returns its timestamp.

**Renderer:** Compatibility (OpenGL 3.3), because this is a static 2D management interface needing no Vulkan features. It avoids Vulkan pipeline overhead and maximizes compatibility; this choice is not a Deck performance measurement. The app runs windowed by default; Steam/gamescope owns presentation when launched as a non-Steam game.

## Checks and screenshots

```powershell
E:/Development/_cache/godot/4.7.2/Godot_v4.7.2-stable_win64.exe --headless --path ui --editor --import --quit
E:/Development/_cache/godot/4.7.2/Godot_v4.7.2-stable_win64.exe --headless --path ui --script res://tests/run.gd -- --mock
```

The focused runner covers JSON/schema failures, stateful mock enable/mod/apply, pending preservation on errors, offline library access, filters, actual focus paths, input hints, tab input and cancel-first confirmation. `--mutate-navigation` deliberately reverses navigation in an in-memory script copy: the focus checks must fail (exit 1); no source files change.

Capture three real viewport PNGs from a short windowed mock run (self exits):

```powershell
E:/Development/_cache/godot/4.7.2/Godot_v4.7.2-stable_win64.exe --path ui --resolution 1280x800 --max-fps 30 -- --mock --capture-dir=<repo>/ui/docs/screenshots
```

Review captures: `ui/docs/screenshots/games.png`, `detail.png`, `updates.png`. Automated capture used an offscreen hidden window to avoid taking desktop focus. Runtime output/logs live outside Git in the portable tool cache.

## Linux export

`export_presets.cfg` defines **Linux x86_64**, with embedded PCK and output `ui/build/deckcompanion-ui`. The single release binary is intended for the stable installed path `~/.local/share/deckcompanion/bin/deckcompanion-ui` (matching the core sibling's payload `bin/deckcompanion-ui`). The parent owns packaging/integration.

The official standard templates archive is **1,281,349,702 bytes**. It was not downloaded; no Linux export was produced. To export, download the official matching archive, extract `templates/linux_release.x86_64` into `E:/Development/_cache/godot/4.7.2/templates/`, then set `custom_template/release` in the preset to that extracted file (machine-local, do not commit). No global template installation is needed:

```powershell
New-Item -ItemType Directory -Force ui/build
E:/Development/_cache/godot/4.7.2/Godot_v4.7.2-stable_win64.exe --headless --path ui --export-release "Linux x86_64" build/deckcompanion-ui
```

**Not verified on hardware:** Steam Deck/Game Mode launch, gamescope focus, physical controller mappings, touch, real Steam restart and frame time/power. No Steam Deck or remote device was contacted. Windows screenshots and headless logic checks do not establish hardware acceptance. Linux export and live Python-engine integration also remain unverified.
