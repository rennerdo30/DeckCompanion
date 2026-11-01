# Engine and index updates

`update` asks GitHub Releases for `rennerdo30/DeckCompanion`'s latest release,
downloads its `index.json`, validates every embedded TOML recipe/digest, and
atomically activates that cache. It never invokes Git or updates at game launch.
`DECKCOMPANION_RELEASE_API` or `DECKCOMPANION_INDEX_URL` override metadata sources.
All downloads use HTTPS, a two-second socket timeout and a two-second wall deadline
per request. An offline request retains the last validated index; no cache means
bundled recipes and the current engine remain usable.

Index schema 1 has `engine: {version,url,sha256}` and `recipes: {appid:
{toml,sha256}}`. The cache adds `fetched_at`. Engine releases are flat tarballs with
`src/`, `vdf/`, `recipes/`, `compat/`, and optional installer/UI files. Archives
reject path escapes, external links, devices and expansion beyond 4 GiB. Verified
engines install to `versions/<ver>/`; `.release.json` records the archive digest.
An existing version with a different receipt is never overwritten. No version is
deleted by update. A tiny text `current` pointer switches with atomic replacement;
`previous` retains the prior installed version for `rollback`. Rollback also keeps
the version you just left, allowing a return. Running processes keep their loaded
version; the next launch reads `current`. Recipe cache updates are separate from
engine rollback; rollback does not discard mod manifests or user data.

SHA256 verifies integrity against the HTTPS index, **not publisher authenticity**.
Signing hook: `update.signature_hook`. Future signing must verify a detached
Ed25519 signature over canonical index bytes, pin public keys in the launcher,
support key rotation/revocation and reject unsigned metadata after migration.
No keys or verifier are shipped yet; an index claiming a signature is refused.

`python3 scripts/build_release.py --output dist` builds the tarball, index and
`checksums.sha256` locally. It resolves FS25's `latest` through the GitHub API,
selects `FS25_DeckMode.zip`, and records tag, immutable download URL and SHA256.
Missing API digests use a bounded asset download for mods; GE requires its API
SHA256. Repeated runtime pins share one API response. `--offline` builds a
development bundle with unresolved source mods (visible but not installable).
The engine archive bundles baseline recipes; the resolved index is activated by
`update`. Nothing publishes. If `bin/deckcompanion-ui` is present, it is included;
`assets/logo/` and `assets/steam/` are included when present. The latter supplies
portrait/wide/hero/logo/icon PNGs for Steam's grid. Install refreshes a stable
`assets/icon.png` under the data root, independent of engine version directories.
`install.sh` and `DeckCompanion-Installer.desktop` are also emitted separately
beside the tarball, index and checksums for upload as release assets. The standalone
installer fetches `releases/latest/download/index.json`, then the index's HTTPS
engine asset URL, verifies SHA256 before extraction, and caches the validated
index during install. Downloads and subprocesses are bounded. It supports piped
stdin without prompts and applies through the installed CLI so a detached helper
outlives temporary extraction cleanup. The desktop asset opens Konsole and runs
the same pipe with timeouts. Install queues the shortcut while Steam runs;
only apply writes Steam VDF files. See the [CLI lifecycle](cli.md).
The builder never reads or modifies the sibling's `ui/` tree.

Not verified on hardware. Tests use fake tarballs and mocked release responses.
