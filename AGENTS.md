# DeckCompanion agent rules

Read `E:/Development/AGENTS.md` and `E:/Development/general-agent-rules.md` first, then this file. Stricter rules here win for this repository.

- **Product:** a Steam Play compatibility tool plus a controller-first management UI for the Steam Deck (SteamOS 3.9+). Python stdlib engine in `src/deckcompanion/`, Godot 4.7 UI in `ui/`, recipes in `recipes/`.
- **Pair Desk project:** `deckcompanion` (issue prefix DC). Handoff, plans and hardware evidence live there.
- **License:** MIT. Never import or copy umu-launcher code (GPL-3.0). It is downloaded at runtime and run as a separate process. umu-protonfixes (BSD-2) is a pinned submodule in `third_party/`, never shipped in the release tarball.
- **Commits:** author `rennerdo30 <9086097+rennerdo30@users.noreply.github.com>`.
- **Steam Deck access:** only when the owner allows it, through the Steam Devkit Client key. Never read or print the key. Use `systemd-run --user` for remote jobs, and make every deploy a new engine version (installed versions are immutable).

## UI rules: the Steam Deck screen is small (owner, 2026-11-01)

The UI runs on a 7-inch 1280x800 screen held at arm's length. Every screen must stay usable there. These are hard minimums, not suggestions.

**Text sizes** (Godot font size in px at 1280x800):
- Body text and list titles: at least 19. Primary game names in lists: 20.
- Secondary lines, descriptions and badges: at least 15. Nothing readable may go below 15, including hints, log lines and legal text in dialogs.
- Screen and hero titles: 26 to 32.

**Touch and focus targets:**
- Buttons, chips and switches are at least 40 px high.
- Library rows are at least 72 px high.
- Every focused control shows the mint focus outline.

**Chrome budget:**
- At most about 100 px of the 800 px height for the top bar, the notice line and the hint row together. Content gets the rest.
- No big page titles.
- No permanent status bars: the status is a corner chip ("✓ Up to date" / "N changes · Apply").
- The notice line appears only while there is something to say.

**Layout:**
- Use the width: two columns where content allows (library list | preview, detail setup | notes and log, settings in two columns).
- No large empty areas.
- Long text wraps or scrolls inside its card; it never pushes controls off screen.

**Controller first:**
- Everything is reachable with the D-pad, A, B, X, Y, LB/RB and the sticks, on every controller device (Steam Input's virtual pad is not device 0).
- Up and down follow the screen geometry. Up from the first content row goes to the active filter or tab, and down never jumps sideways into the top bar.
- B goes back. LB/RB switch tabs. "Back to Steam" is always visible.

**Touch:**
- Swiping scrolls every scroll area; a tap under the drag threshold still presses.

**Palette:**
- Use the existing tokens in `ui/scripts/main.gd` (INK, MUTED, MINT, CREAM, WARN, SURFACE*).
- No third-party asset packs.

**Verification before calling UI work done:**
- Headless UI tests pass.
- Mock screenshots at 1280x800 and 1920x1080.
- Whenever the Deck is available, a real Game Mode screenshot (`gamescopectl screenshot`) checked for readability.
- A UI change that shrinks text or targets below these minimums is a defect.

## Modules update independently (owner, 2026-11-01)

The owner does not want DeckCompanion releases just to update its modules. Everything below has its own update channel, checked by `update` in the background (and never in the launch path):
- **Recipes and the launcher catalog** are data in a separate content index, not engine code or engine release assets.
- **Mods** resolve their newest release from the mod's own source with a verified SHA256 (a recipe may still pin a version on purpose).
- **Proton builds and the umu-launcher runtime** track their upstream releases.
- **The Decky plugin** has its own version and release asset.

An engine release is only needed for engine code changes. Adding a hard-coded list, a build-time pin or a release-coupled asset for any of these is a defect.
