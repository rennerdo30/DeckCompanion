# Third-party notices

`vdf/` is ValvePython vdf 3.4, copyright 2015 Rossen Georgiev, MIT.
Source: https://github.com/ValvePython/vdf/tree/v3.4 . Vendored Python files
are unchanged from the prototype. Its complete license is in `vdf/LICENSE`.

No umu-launcher code is included. protonfixes APIs are called at runtime from
the user's GE-Proton installation. The development-only submodule below is
excluded from release tarballs, including all of its nested dependencies.

## umu-protonfixes (development and tests)

Source: https://github.com/Open-Wine-Components/umu-protonfixes
Commit: `f4940924eefd2c973f5ec57dac26aa3dcefb33ca` (GE-Proton11-11 gitlink).
Unmodified git submodule at `third_party/umu-protonfixes`; see
[docs/protonfixes.md](docs/protonfixes.md). Complete upstream notice:

```text
BSD 2-Clause License

Copyright (c) 2018, Chris Simons

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

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
