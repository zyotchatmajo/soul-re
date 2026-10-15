# Level editor first milestone

The repository now includes a read-only browser for the extracted Soul Reaver
BIGFILE area assets. The binary formats contain PlayStation pointer-rich
structures, so the browser is intentionally an inspection tool until geometry
parsing and relocation are verified.

## Extract the prototype assets

From WSL, in the repository root:

```bash
export PATH=/home/kevin/.local/bin:/home/kevin/.venvs/soul-re/bin:$PATH
cd-dat-utils unpack config/cd-dat-utils/config.yaml
```

The command creates `game/bigfile` from `game/BIGFILE.DAT`.

## Text inventory

From the repository root on Windows or WSL:

```bash
python tools/level_editor.py
python tools/level_editor.py --area city
python tools/level_editor.py --manifest build/level-inventory.json
python tools/level_editor.py --inspect city/bin/city2.drm
python tools/level_editor.py --inspect city/bin/city2.drm --obj build/city2-terrain.obj
python tools/level_editor.py --inspect city/bin/city2.drm --scene build/city2-scene.json --svg build/city2-preview.svg
```

The report groups files by area and identifies the companion asset types:
`.drm` object data, `.crm` room data, `.smf` streamed model data, and `.snf`
stream metadata. The manifest includes sizes, headers, and SHA-1 hashes so
future parser changes can be regression-tested against the extracted data.

The DRM inspector validates the relocation-table boundary, reads the serialized
`Level`/`Terrain` headers, reports geometry counts and bounds, and can export
the terrain vertex/face arrays to a coordinate-only Wavefront OBJ. The OBJ is
an inspection export; it does not replace the original game asset. It also
extracts intro, planning, and sound markers into JSON and draws a dependency-
free top-down SVG preview with those markers labeled.

## Safe marker edits

The scene JSON is also the first editable sidecar format. Change only the
`x`, `y`, or `z` values for a marker, then write a new DRM copy:

```bash
python tools/level_editor.py \
  --inspect city/bin/city2.drm \
  --patch-scene build/city2-scene-edited.json \
  --output build/city2-edited.drm
```

The command validates every marker slot and signed 16-bit coordinate, patches
only marker position fields, re-parses the result, and refuses to overwrite the
source DRM. Geometry, relocation entries, and all other fields are unchanged.
The new DRM still needs to be packed into a BIGFILE before an emulator can run
it. Use the repository helper from the WSL environment after creating an
edited staging tree:

```bash
source /home/kevin/.venvs/soul-re/bin/activate
python3 tools/pack_bigfile.py config/cd-dat-utils/config.yaml \
  --input build/bigfile-edited \
  --output build/BIGFILE-edited.DAT
```

The helper refreshes the 32-bit word-sum checksums used by the game's streamed
loader and writes a new container; it never overwrites `game/BIGFILE.DAT`.

## Desktop browser

```bash
python tools/level_editor.py --gui
```

The GUI is read-only for now. Select an area and one of its assets to inspect
the path, size, hash, and binary header. The next implementation step is to
decode a selected `.crm`/`.drm` into vertices, faces, instances, and markers,
then add an export path that round-trips one asset before enabling edits.
