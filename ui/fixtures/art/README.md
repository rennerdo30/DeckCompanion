Original mock artwork, drawn from SVG primitives in `ui/tests/create_fixture_art.gd`
and rasterized by Godot 4.7.2. MIT, same as the UI. No third-party images, logos or fonts.
Regenerate with Godot `--headless --path ui --script res://tests/create_fixture_art.gd`.

Mock JSON uses resource paths; the fixture loader makes them absolute in an unpacked
project. Exported mock mode reads the bundled PNGs from the PCK. Production uses the
engine's absolute local JPEG/PNG paths and never downloads artwork.

The PNG import settings use **Keep File (exported as is)** so the same worker can
decode source bytes in the exported mock. See Godot's [import process](https://docs.godotengine.org/en/stable/tutorials/assets_pipeline/import_process.html).
