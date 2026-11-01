# DeckCompanion for Decky

Optional Quick Access Menu panel backed by the installed DeckCompanion JSON CLI.
Version **0.1.0** has its own tag **decky-v0.1.0** and asset
**deckcompanion-decky-0.1.0.zip**, separate from engine tarballs.
The build does not publish a release or submit to the Decky store.
When the owner publishes this tag, set GitHub's **Set as the latest release** off
(`make_latest: false`): engine bootstrap URLs still use
`releases/latest/download/index.json`. Plugin discovery uses the releases list
and is unaffected by that flag.

## Build

Use Node and npm (or the template's `pnpm install` / `pnpm build`). From this directory:

```sh
npm ci --ignore-scripts
npm run check
npm run build
python3 ../scripts/build_decky_plugin.py
```

Rollup writes `dist/index.js`; the stdlib packager writes
`dist/deckcompanion-decky-<package.json version>.zip`, with a `DeckCompanion/` root.
It includes licenses/notices and the exact unmodified `@decky/api` sources for LGPL
relinking. See THIRD_PARTY_NOTICES.md. No compiled backend or Docker is required.
The Python adapter runs `~/.local/share/deckcompanion/bin/deckcompanion`, using
`decky.DECKY_USER_HOME` for the user's home.

**Verified primary sources on 2026-11-01:**

- Latest stable [Decky Loader v3.3.1](https://github.com/SteamDeckHomebrew/decky-loader/releases/tag/v3.3.1).
- Current [template](https://github.com/SteamDeckHomebrew/decky-plugin-template/tree/90d0780e882a17f5714fc6de044c645f22608290), commit `90d0780e882a17f5714fc6de044c645f22608290`.
- npm latest: [`@decky/ui` 4.16.4](https://registry.npmjs.org/@decky/ui/latest),
  [`@decky/api` 1.1.3](https://registry.npmjs.org/@decky/api/latest),
  [`@decky/rollup` 1.0.2](https://registry.npmjs.org/@decky/rollup/latest), Rollup 4.67.4.
- Parent-approved compiler exception: **TypeScript 6.0.3**, newest stable 6.x in
  npm. Latest TypeScript **7.0.2** fails `@decky/rollup` 1.0.2 /
  `@rollup/plugin-typescript` at module load:
  `Cannot read properties of undefined (reading 'ES2015')`. Revisit when
  `@decky/rollup` updates; the template's build pipeline is retained.
- Local build: **Node 26.4.0 / npm 11.17.0**; `npm run build` succeeds:
  `./src/index.tsx → dist...`, `created dist in 1.1s`. pnpm is not installed locally.

## Install and update

Once an owner-approved `decky-v<version>` GitHub release and versioned ZIP exist,
run `deckcompanion decky plugin-status --json`, then
`deckcompanion decky plugin-install --json`. The engine selects the latest stable
plugin independently of engine versions and verifies GitHub's asset SHA256 digest.
Before the first plugin release it reports unavailable.

Decky must be installed and `~/homebrew/plugins` user-writable. For a root-owned
directory use Decky's supported path: enable developer options in Decky Settings,
choose **Install Plugin from ZIP**, and select the downloaded versioned ZIP.
No chmod/chown, sudo copying or passwords are used by DeckCompanion. Reload plugins
afterwards. Manually installed/edited plugins stay intact; update those through Decky.
Engine-managed updates retain a previous copy for recovery.

## Panel

Current game comes from Decky's Steam UI store; otherwise the engine picks a
running/last game. Refreshes every five seconds while mounted do not overlap.
The panel shows recipe, selected Proton/reason, mod states and last launch. Routing
toggles queue changes with an Apply message. Mods show immediate/deferred state.
The splash toggle is global. **Open DeckCompanion** uses the actual shortcut appid
from the engine; it is disabled until the shortcut exists.

Registered umu games already run through DeckCompanion. Their routing/Proton controls
are disabled until GAME `options.takeover`/`options.proton` capabilities say otherwise;
mods and splash remain usable. Errors appear inline, including missing CLI and the
20-second timeout. This is an unprivileged plugin (`flags: []`).

Native Decky focus/scroll controls are used. Body/control text is at least 19 px,
game names 20 px, targets at least 40 px, with mint focus outlines. Hardware checks
remain open: QAM readability, controller/touch focus and scrolling, current-game
detection, RunGame, mod reconciliation, Desktop sudo prompt and plugin reload/update.
No device connection was used for implementation.
