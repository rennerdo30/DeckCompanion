# Independent update channels

`update` refreshes engine, content, mods, GE-Proton and umu independently. Games
never check release APIs. `status --json` reads cached channel metadata without
network access; existing status fields remain available. The Updates UI can use
its `channels` object (see [CLI JSON](cli.md)). Content or mod changes require no
engine release. The Decky plugin has a separate channel managed by its plugin.

## Content: recipes and launcher catalog

Content is published as a GitHub release tagged `content-<content_version>`.
`update` lists releases through the GitHub API, ignores drafts/prereleases and
engine tags, then selects the newest content release by publication time. Listing
pages (100 releases each, maximum 10) and the index download share one two-second
wall budget. Incomplete listings, offline requests and invalid data retain the
active cache. `DECKCOMPANION_CONTENT_API` overrides the releases-list endpoint.

`content-index.json` schema 1 contains:

```json
{
  "schema_version": 1,
  "content_version": "2026.11.01.1",
  "min_engine": "0.4.3",
  "recipes": {"42": {"toml": "...", "sha256": "64 lowercase hex characters"}},
  "launchers": {"toml": "...", "sha256": "64 lowercase hex characters"}
}
```

Recipe keys may also be `umu-*` identifiers. Launcher TOML is optional until the
catalog exists. Every embedded TOML digest, recipe schema and the index schema
are validated before atomically replacing `cache/index.json`. `fetched_at` records
activation time. A future `min_engine` returns `engine_too_old` with a clear
message; installed content and games continue working. An older content version
cannot replace a newer one. Recipe versions compare numerically first; content
wins equal recipe versions, then content version or legacy engine provenance
breaks ties. Newer cached recipes survive older content and engine installs.
Engine rollback preserves content, mod manifests and user data.

For the owner to publish after approval:

1. Edit `recipes/*.toml` and optionally `content/launchers.toml`; increase changed
   recipe versions. Keep independently tracked mods at `version = "latest"` with
   `release_repo` and `asset`. Pinned versions remain supported.
2. Run `python3 scripts/build_content.py --version 2026.11.01.1 --min-engine <first-compatible-engine> --output dist/content`.
   This validates TOML and hashes, preserves latest mods, and can enrich deliberate
   mod/GE pins from upstream. `--offline` skips upstream enrichment. The launcher
   file is skipped gracefully when absent. Review the index and checksum file.
3. After owner approval, create the `content-2026.11.01.1` release and upload
   `content-index.json` and `checksums.sha256`. Set **make latest = false** (CLI:
   `--latest=false`) for **all non-engine releases**, including content and Decky.
   This is a second safety net for older engine installers.
4. Run Update and inspect `status --json`. Check recipes/catalog on hardware.
   The builder does not publish, tag, push or change the engine version.

SHA256 verifies integrity against HTTPS metadata, not publisher authenticity.
The signature hook rejects indexes claiming an unsupported signature. A future
signature verifier must bind canonical bytes, pinned public keys and key rotation.
GitHub's [release API](https://docs.github.com/en/rest/releases/releases)
provides release lists and asset digests.

## Mods and umu

For `version = "latest"`, Update resolves each mod's own stable release and named
asset. It prefers the asset's `sha256:` API digest and verifies downloaded bytes
before caching the resolution. If upstream provides no digest, it downloads and
hashes the asset, recording a warning on that resolution; this establishes a
local integrity baseline, not independent publisher verification. Failed updates
keep the previous verified resolution. `cache/mod-resolutions.json` records
versions, immutable URLs, hashes, warnings and the last successful update time.
Pinned mods are unchanged. Recipe loading applies only matching cached latest
resolutions; launch never resolves releases. Unresolved mods defer installation
with an Update-first message. Update downloads resolved mod bytes for offline use.

umu tracks `Open-Wine-Components/umu-launcher` releases. Update requires an API
SHA256 for the zipapp archive, verifies it, stages the executable and the licence
from the selected upstream tag, then atomically selects the complete runtime.
The licence URL and locally calculated hash are recorded in its receipt. umu is
run as a separate process; its GPL code is never imported or shipped in engine
archives. Old versions are retained. Offline/failed updates keep the verified
active runtime. Launch reads only that runtime and verifies its receipt, without
network access. Run `umu install --pin <tag>` to pin a safe version; `--pin latest`
resumes tracking. A pin persists even if its download fails; the previous active
runtime stays available until a successful install. `umu install` also refreshes
upstream metadata explicitly.

Existing pre-channel umu installations remain usable from their original runner
receipts without rewriting their files. Their existing licence becomes the local
licence integrity baseline when adopted; upstream archive digests are checked
again on the next online update.

## Engine releases

Engine discovery scans releases for `v<semver>` tags only, then selects the newest
publication. It ignores content/Decky tags and drafts. Prereleases are excluded
unless `DECKCOMPANION_ENGINE_PRERELEASES=1` is set (also supported by the standalone
bootstrap). Discovery is paginated and bounded; `releases/latest` is never used.
The selected engine release supplies `index.json` with
`engine: {version,url,sha256}`; new engine indexes have an empty `recipes` map.
Legacy engine indexes remain readable, but never replace active content.
Content or Decky releases marked latest do not affect engine discovery.
`DECKCOMPANION_RELEASE_API` and `DECKCOMPANION_INDEX_URL` override engine metadata
sources; the release API override must return a releases list. Engine downloads
retain bounded HTTPS requests and
verified immutable version directories, atomic `current`/`previous` pointers and
rollback. Existing versions with different receipts are preserved.

`python3 scripts/build_release.py --output dist` only packages engine code and
bundled fallback recipes (and the fallback launcher catalog when present). It
never resolves mod versions or fetches upstream metadata. `--offline` remains a
compatible alias for this already offline build. It emits the tarball, engine
index, checksums and installer assets. Optional exported UI and artwork are
included, but the builder never reads or changes `ui/`. It excludes development
`third_party/` sources and bytecode. Archives reject path escapes, external links,
devices and expansion beyond 4 GiB. Installer behavior and Steam pending/apply
rules remain described in [CLI lifecycle](cli.md).

**Not verified on hardware:** actual Steam/umu/mod installation and live delivery.
Tests use fake trees, archives and mocked release APIs.

## GE-Proton builds

`update` also installs every versioned GE-Proton pin from enabled recipes, then
the newest stable release when `auto_install_ge=true` (default). Disabling that
setting still installs enabled recipe pins. Launch never downloads runtimes: it
keeps the installed-build fallback. The Updates tab lists wanted/installed builds,
offers Install/retry and polls progress; Settings exposes both GE preferences.

Metadata comes from the `GloriousEggroll/proton-ge-custom` GitHub releases API.
Select the x86_64 archive, prefer its `digest: sha256:…`, and enforce any recipe
SHA256 pin before activation. Older releases without the archive API digest may
provide a matching `.sha512sum`; verify SHA512 as SHA512, additionally calculate
SHA256, and compare the recipe SHA256 if present. A checksum file asset's own API
digest is never used as the archive digest. Validated metadata is cached for
offline retries. These checks trust upstream HTTPS metadata, not a signature.

Runtime transfers have a separate 3,600-second wall budget, 30-second connect/read
socket timeout, 2 GiB compressed cap, and 4 GiB extraction cap. Check free space
for the archive, maximum expansion and 256 MiB margin before downloading.
Interrupted `.part` files stay in `cache/ge/downloads/` for a Range retry. A server
ignoring Range restarts the file; mismatched Content-Range, truncation and excess
bytes fail. Full corrupt files are removed before retry. Extraction uses the same
contained tar implementation as engine updates, stages beside compatibility tools
and activates the complete directory atomically. Existing directories are preserved.

UI-triggered update/install uses `systemd-run --user` with a 3,600-second service
limit and a pinned engine/explicit data roots; closing the UI does not cancel the
service. Missing/failing user systemd reports an actionable error. Direct CLI
installs work without systemd. `proton.lock` excludes competing runtime jobs;
short state operations and read-only polling remain available. Expired service
receipts become retryable failures; bytes are never trusted without verification.

`cleanup_old_ge=true` (default) retains enabled recipe pins, newest GE and external
builds. Cleanup only removes recorded installs with matching ownership receipts
and a complete unchanged file/link inventory, and waits while Steam runs. Unknown
latest metadata or any install error skips cleanup. Disabling cleanup preserves
old builds. Receipts live in `proton-installed.json` under the data root, separate
from game settings; every downloaded build also carries `.deckcompanion-ge.json`.
No umu-launcher code is imported or copied. Real downloads and service lifetime
remain **not verified on hardware**.
