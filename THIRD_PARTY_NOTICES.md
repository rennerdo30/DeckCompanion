# Third-party notices

`vdf/` is ValvePython vdf 3.4, copyright 2015 Rossen Georgiev, MIT.
Source: https://github.com/ValvePython/vdf/tree/v3.4 . Vendored Python files
are unchanged from the prototype. Its complete license is in `vdf/LICENSE`.

No umu-launcher code is included. protonfixes APIs are called at runtime from
the user's GE-Proton installation; no protonfixes source is vendored.

## umu-launcher (downloaded at runtime)

umu-launcher 1.4.4 is GPL-3.0, maintained by Open Wine Components:
https://github.com/Open-Wine-Components/umu-launcher/releases/tag/1.4.4 .
DeckCompanion downloads the official zipapp archive, verifies the release SHA256,
and installs its LICENSE alongside `umu-run` in the user's data directory.
It executes umu-run as a separate process; no umu-launcher code is copied, imported,
vendored or distributed in DeckCompanion's MIT release tarball.
Corresponding source and license: https://github.com/Open-Wine-Components/umu-launcher/tree/1.4.4 .
Game ID lookup uses the official CSV at
https://github.com/Open-Wine-Components/umu-database (downloaded and cached at runtime).

## Inter (artwork lettering)

The wordmark in `assets/steam/` is lettering converted to outlines from Inter 4.1 by Rasmus Andersson, SIL Open Font License 1.1 (https://github.com/rsms/inter). No font files are distributed.
