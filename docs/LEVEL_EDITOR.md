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

The desktop GUI is read-only. Select an area and one of its assets to inspect
the path, size, hash, and binary header; use the WebGL viewer below when you
need to see decoded DRM geometry and markers in 3D.

## Interactive 3D viewer

The extracted DRM terrain can also be inspected in a local WebGL viewer. It
does not require Node, a package install, or an internet connection:

```bash
python tools/level_viewer.py --open
```

If the browser does not open automatically, visit the URL printed by the
command. Drag the canvas to orbit, hold Shift while dragging to pan, and use
the mouse wheel to zoom. The asset selector lists every extracted `.drm`, and
the sidebar reports geometry bounds and scene markers. The `wireframe overlay`
and `scene markers` checkboxes are display-only; source files are never changed.

The server accepts an alternate extraction root or initial asset when needed:

```bash
python tools/level_viewer.py \
  --root game/bigfile/kain2/area \
  --drm city/bin/city2.drm \
  --port 8765
```

The same viewer and DRM tools work against the PC remaster's loose `AREA`
directory. For example, the installed `city9.drm` was parsed successfully
with the original relocation layout, geometry arrays, and marker structures:

```powershell
python tools/level_viewer.py `
  --root 'F:\SteamLibrary\steamapps\common\Soul Reaver I-II\1\AREA' `
  --drm CITY/BIN/city9.drm `
  --open
```

Keep a backup of the Steam install and write modified assets to a staging
folder first. The remaster also contains packed archives, so a file may be
loaded from an archive rather than the loose tree; that precedence needs to be
verified per asset before distributing a replacement.
