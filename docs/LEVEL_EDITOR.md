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
`.drm`/`.pcm` scene and object data, `.crm` room data, `.smf` streamed model
data, and `.snf` stream metadata. The manifest includes sizes, headers, and
SHA-1 hashes so future parser changes can be regression-tested against the
extracted data.

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

Binary patch helpers also reject an output path that resolves to the input
scene. Use a separate staging filename and replace the game asset only after
testing.

## Desktop browser

```bash
python tools/level_editor.py --gui
```

The desktop GUI is read-only. Select an area and one of its assets to inspect
the path, size, hash, and binary header; use the WebGL viewer below when you
need to see decoded DRM geometry and markers in 3D.

## Interactive 3D viewer

The extracted DRM terrain and PCM assets can also be inspected in a local
WebGL viewer. It does not require Node, a package install, or an internet
connection:

```bash
python tools/level_viewer.py --open
```

If the browser does not open automatically, visit the URL printed by the
command. Drag the canvas to orbit, hold Shift while dragging to pan, and use
the mouse wheel to zoom. The asset selector lists every extracted `.drm` and
`.pcm`. Area PCMs are decoded as scene terrain with markers; object PCMs and
standalone SRMs from the sibling `OBJECT` directory are decoded as model
meshes for placement previews. The `wireframe overlay`, `scene markers`, and
`portal frames` checkboxes are display-only; source files are never changed.
Portal frames show both serialized triangle winding sets in different colors
(cyan/orange), while the sidebar reports each portal's name, signal ID, stream
ID, and bounds. The same read-only data is available as JSON at
`/api/portals?path=CITY/BIN/city9.drm` for comparing reciprocal portals
between scenes without launching the game.
The viewer also lists each serialized BSP root sphere/box; `/api/bsp?path=...`
exposes the same data for checking room-origin discrepancies.
For a combined portal/BSP report, add `--alignment` to
`tools/compare_portals.py`; it prints the translated room bounds and the BSP
root-center deltas for each reciprocal pair.

To validate a complete staged City 9/10/11 set before copying it into an AREA
directory, use:

```powershell
python tools/validate_scene_set.py `
  --city9 'build\city9-apr-bspfixed-final.pcm' `
  --city10 'build\city10-fixcity11-single-return.drm' `
  --city11 'build\city11-fixcity11-native.drm'
```

For a reciprocal comparison from PowerShell, use the read-only diagnostic
command below. It matches the two scenes by stream-unit IDs and reports the
maximum doorway-polygon delta, ignoring triangle winding and triangulation
order:

```powershell
python tools/compare_portals.py `
  --left 'F:\SteamLibrary\steamapps\common\Soul Reaver I-II\1\AREA\CITY\BIN\city9.drm' `
  --right 'F:\SteamLibrary\steamapps\common\Soul Reaver I-II\1\AREA\CITY\BIN\city10.drm'
```

The server accepts an alternate extraction root or initial asset when needed
(`--drm` is retained as the option name for compatibility):

```bash
python tools/level_viewer.py \
  --root game/bigfile/kain2/area \
  --drm city/bin/city2.drm \
  --port 8765
```

### Editor workspace

The dependency-free viewer uses three working areas:

- **Left inspector:** a searchable Scene objects list, selected-object summary,
  duplicate/delete actions, explicit X/Y/Z and yaw controls, and grouped identity,
  runtime name, asset, and animation properties. Native objects have blue
  `NATIVE` badges; editor-created objects and duplicates have green `CUSTOM`
  badges. The selected row is highlighted, and its viewport crosshair is gold.
  Search accepts names, assets, decimal/hex runtime IDs, and native/custom origin.
- **Viewport:** display toggles and Reset view affect the camera or rendering.
  Object transforms remain available through the inspector buttons and existing
  shortcuts; dragging is only needed for camera navigation or optional placement.
- **Scene inspection:** independently collapsible Markers, Stream portals, BSP
  roots, Events, Diagnostics, and Keyboard & help panels. The header's Scene
  panels button hides this column. On narrower screens it starts hidden and
  opens over the viewport; on phone-sized screens the viewport and panels stack.

Apply stays at the bottom of the left inspector on desktop, separate from camera
controls. Export JSON and Import JSON are in Save & apply. Fields update the
in-memory editor as they are typed; Update selected does not write to disk.
Place at center and Click to place create a new custom object using the current
properties. Buttons explain their purpose on hover, have keyboard focus states,
and disable when their action is unavailable. Object rows support Tab and Enter
or Space selection. Arrow keys, PageUp/PageDown, Q/E, Shift, Ctrl+D, and Delete
retain their existing editing shortcuts; typing in form fields suppresses them.

The safety bar always shows the loaded source path and SHA-1, whether direct
writes are enabled, and the target assembled from the configured write root and
scene-relative path. **An enabled write root is not proof that it is a staging
copy:** verify the displayed destination. Native deletion asks for confirmation
and queues removal until Apply. Every disk Apply confirmation identifies the
target, loaded hash, and backup behavior. Status messages distinguish errors,
warnings, and successful operations. Companion-file and imported-sidecar warnings
remain visible outside the collapsible panels.

V1 and V2 sidecars remain compatible. A known imported SHA-1 mismatch disables
scene Apply until a matching sidecar is imported or its original scene is opened
and the sidecar imported there. Export retains imported source/hash provenance,
so exporting a stale sidecar does not silently mark it current. A different
source path produces a warning (a copied staging tree may legitimately have a
different path). Legacy sidecars without a hash still load. The backend's token,
hash, backup, and idempotence checks remain in place; no automatic writes occur.

The Scene objects list loads the scene's existing intro/object records as well
as editor-created object/spawn/trigger/light markers. Select a native object
from the list, then move, rotate, rename, change its asset, duplicate, or
delete it. `Click to place` raycasts a new placement onto the terrain.
`runtime object name` is the exact
ASCII name written to the 16-byte runtime object table; if it is blank, the
name is derived from the selected asset. `Export JSON` writes a
`soul-re-placement-v2` sidecar and `Import JSON` accepts both v1 and v2.
The transform-control buttons beside the coordinate fields provide explicit
X/Y/Z movement and yaw rotation steps, so editing does not require dragging
the viewport. The viewport remains an orbit/pan surface; click a visible
placement marker to select it.
V2 sidecars include the loaded scene SHA-1. Apply checks that hash before
writing, so a sidecar from an older or different staged scene is rejected.

With an explicit `--write-root`, `Apply scene to AREA` synchronizes the listed
objects and explicit queued removals. New records use globally reserved IDs, Z-axis
yaw, zeroed pointer/spectral fields, and the object-name table is updated when
needed. Existing editor records are updated by ID, deleted records are removed
from the replacement intro array, and a second identical apply is byte-
idempotent. Native intro records can be edited by their explicit runtime ID;
deleting a native object in the UI requires confirmation. Unreferenced native
records remain unchanged. The operation creates a timestamped backup first,
requires the read and write copies to have matching SHA-1 hashes, and is
protected by a local session token and write lock. Keep `--write-root` pointed
at a staging tree until the edited file has been tested in-game.
Native point and spot lights are now loaded into the same inspector and their
positions can be applied while preserving the runtime light-table links. Light
duplication/deletion is intentionally deferred; trigger and spawn table writing
still requires mapping the event/planning structures.

## Direct loose scene testing

For a copied/staged PC remaster install, pass an explicit write root to enable
the marker editor's `Apply to AREA` button. The server refuses direct writes
unless this flag is present, and every changed scene asset receives a timestamped
`.bak-YYYYMMDD-HHMMSS` backup first:

```powershell
python tools/level_viewer.py `
  --root 'build\area-staged' `
  --write-root 'build\area-staged' `
  --drm CITY/BIN/city9.drm `
  --open
```

Populate `build\area-staged` with copied AREA assets before starting this command.
Select a marker, edit its X/Y/Z fields, and click `Apply to AREA`. This writes
only marker coordinates through the existing safe scene patcher. For custom
objects, select `object`, choose an OBJECT PCM/SRM (or type a runtime name),
place it, and click `Apply scene to AREA`; the selected mesh stem becomes the
16-byte runtime object name (for example `RAZIEL_1.SRM` becomes `raziel`).

The same viewer and DRM/PCM tools work against the PC remaster's loose `AREA`
directory. For example, the installed `city9.drm` and `ALUKA/aluka10.pcm`
were parsed successfully with the original relocation layout, geometry arrays,
and marker structures:

```powershell
python tools/level_viewer.py `
  --root 'F:\SteamLibrary\steamapps\common\Soul Reaver I-II\1\AREA' `
  --drm CITY/BIN/city9.drm `
  --open
```

When a scene has both `.DRM` and `.PCM` companions, the viewer displays a
warning so the edited copy can be matched to the file the runtime actually
loads. Marker edits also refuse to move intro records attached to a
multi-spline path; those need a path-aware editor rather than a single-point
coordinate change.

To inspect an area PCM directly, use the same command with its relative path:

```powershell
python tools/level_viewer.py `
  --root 'F:\SteamLibrary\steamapps\common\Soul Reaver I-II\1\AREA' `
  --drm ALUKA/BIN/aluka10.pcm `
  --open
```

Keep a backup of the Steam install and write modified assets to a staging
folder first. The remaster also contains packed archives, so a file may be
loaded from an archive rather than the loose tree; that precedence needs to be
verified per asset before distributing a replacement.

Event Apply currently permits only fixed-size event fields (event number,
variables, and puppet state). Raw script-word edits are rejected until the
opcode widths and VM stack effects are fully validated; this prevents a bad
word from producing an invalid event program.

### Validation

Run the Python checks from the repository root:

```bash
python -m py_compile tools/level_editor.py tools/level_viewer.py
python -m unittest discover -s tests -v
```

The HTTP Apply regression test uses a temporary copy of the installed test scene
and verifies a native edit, backup creation, and a byte-identical second Apply.
Asset-dependent tests skip when their fixture assets are not installed. The
viewer server initializes its changed-field count before accumulating object-name
changes; the binary patch helpers and endpoint payloads are unchanged.

For UI verification, check the embedded script with `node --check` after
extracting it to a temporary `.js` file (Node is only a validation tool, not a
viewer dependency). Check desktop, tablet, and phone widths; native/custom
selection; button and keyboard transforms; duplicate/delete confirmations;
v1/v2 sidecars; stale-hash warnings; disabled writes; and Apply/reapply against
disposable staged copies only.
