# Recipes, schema version 1

`recipes/<appid>.toml`, parsed with Python 3.11 `tomllib`. Required fields:
`schema_version = 1`, string `appid`, `name`, and `version`. IDs and versions are
portable filename components. Bundled recipes are the offline baseline; a
validated cached recipe takes precedence. No shell scripts or arbitrary code.

```toml
schema_version = 1
appid = "2300320"
name = "Farming Simulator 25"
version = "1.0.0"
notes = "Not verified on hardware."
[runtime]
proton = "GE-Proton11-11" # exact pin, or GE-Proton for newest installed
# url = "https://.../GE-Proton11-11-x86_64.tar.gz"
# sha256 = "<64 lowercase hexadecimal characters>"
launch_options = [] # argument array appended after the game command, no shell
[runtime.env]
PROTON_LOG = "0"
[wine]
winetricks = ["d3dcompiler_47"]
[wine.dll_overrides]
d3dcompiler_47 = "n,b"
[wine.env]
EXAMPLE = "value"
[[wine.registry]]
key = 'HKCU\Software\Example'
name = "Example"
type = "REG_SZ"
value = "value"
[[mods]]
id = "example"
name = "Example mod"
version = "1.0"
url = "https://example.org/mod.zip"
sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
mode = "copy" # or extract_zip
root = "prefix" # STEAM_COMPAT_DATA_PATH; alternative: game install directory
target = "pfx/drive_c/mods/example.zip" # copy: complete filename; extract_zip: directory
enabled = true
[[settings]]
id = "initial-display"
root = "prefix"
target = "pfx/drive_c/example.ini"
format = "ini"
section = "Display"
key = "Windowed"
value = "true"
# XML instead: format = "xml", xpath = "./display/windowed", value = "true"
```

Paths must be relative, contained, without `..`, drive letters, or backslashes.
Symlink redirects outside their declared root are refused. Mods require HTTPS and
SHA256 before installation. An empty hash means **unresolved source recipe**:
it can be displayed but cannot be installed. The release/index builder resolves
`release_repo` + `asset` with GitHub's Releases API and records concrete version,
URL, and digest. `latest` is never resolved at game launch. FS25 Deck Mode remains
a ZIP file copied into the FS25 mods directory, not extracted.

Prefix content defers until the next launch after Proton creates `pfx`. This avoids
creating a partially initialized Wine prefix or depending on protonfixes lifecycle
for file content. Settings are stamped per prefix and id, preserving later user edits.
Wine steps are generated as a managed protonfixes local fix and chain the bundled
upstream fix first. Upstream import and prefix timing need hardware verification.

602520 retains the useful `wmp11` step. Its old blanket permission changes and
network-cloned mf-install are not migrated. 2087030 retains `d3dcompiler_47` and
`win7` as historical settings, not a compatibility guarantee. 1063730 is dropped:
the prototype referenced a missing `fix-eac.sh`; no replacement is invented.

Runtime API references (no umu-launcher code copied):
[localfix semantics](https://github.com/Open-Wine-Components/umu-protonfixes#local-fixes),
[protonfixes util](https://github.com/Open-Wine-Components/umu-protonfixes/blob/master/util.py).
