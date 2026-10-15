# PC remaster modding investigation

The PC remaster is a viable target for the level tools, but it is not the
same executable as the PS1 decompilation. The remaster uses 64-bit `SRX.exe`
and `1/sr1.dll` binaries, while retaining the original Soul Reaver asset
concepts and virtual paths.

## Evidence from the installed files

The install contains both a loose tree and packed data:

```text
1/AREA/<area>/BIN/<asset>
1/BIGFILE.DAT
1/TEXTURES.BIG
1/OBJECT/*.SRA
1/OBJECT/*.SRM
1/OBJECT/*.SRL
```

The DLL contains the corresponding paths (`kain2/area/...`,
`kain2/object/...`) and a `bigfile.dat` loader string. This indicates that
the remaster kept a virtual filesystem close to the original game's layout.

The existing DRM parser successfully reads the remaster's loose assets without
changes:

| Asset | Vertices | Faces | Markers | Bounds |
| --- | ---: | ---: | ---: | --- |
| `AREA/CITY/BIN/city9.drm` | 1,960 | 3,486 | 56 | `(-6400,-12160,-652)` → `(12829,0,9587)` |
| `AREA/MRLOCK/BIN/mrlock2.drm` | 3,731 | 6,296 | 1 | `(-15181,-23573,-5438)` → `(627,-4654,12801)` |

The viewer now also accepts the remaster's PCM assets. Area PCMs use the same
scene/terrain structures as DRM files and retain their intro markers; standalone
object PCMs use the object/model layout and are decoded as independent meshes.
For example, `AREA/ALUKA/BIN/aluka10.pcm` decodes to 547 vertices, 796 faces,
and one marker, while `OBJECT/BLADE/BLADE.PCM` decodes to 195 vertices and 366
faces.

The viewer can therefore inspect remaster levels directly:

```powershell
python tools/level_viewer.py `
  --root 'F:\SteamLibrary\steamapps\common\Soul Reaver I-II\1\AREA' `
  --drm CITY/BIN/city9.drm `
  --open
```

The viewer's new Placements panel can add object, spawn, trigger, and light
sidecar entries by raycasting onto the terrain. Exported
`soul-re-placement-v1` JSON is an editor artifact for now; it is not yet
written into the remaster's runtime object tables.

## What we can mod first

The lowest-risk first target is a loose `.drm` or area `.pcm` scene asset. The
current tools can export its geometry and markers, change signed 16-bit marker
positions, validate the result, and write a new asset without touching the
Steam installation. Standalone object PCMs are currently read-only in the
viewer. Texture replacements are another practical target because the
remaster exposes DDS files in `AREA/TEX` and `AREA/PIX`.

The packed `1/BIGFILE.DAT` is a separate problem. Its header is a flat table of
1,325 entries and its folder/encryption layout is different from the PS1
prototype archive. The current prototype packer intentionally rejects it, so
we should not overwrite that archive until its entry format, checksums, and
loose-file precedence are verified.

## Safe development workflow

1. Copy the `1` directory to a staging folder; never experiment in the Steam
   install directly.
2. Point the viewer and inspector at the staging `AREA` directory.
3. Export a scene JSON, edit only the desired marker coordinates, and write a
   new `.drm` or area `.pcm` with `--patch-scene --output`.
4. Replace the staged loose asset and boot the staged copy to verify that the
   remaster loads it.
5. Only after that test succeeds should we implement a remaster BIGFILE
   extractor/repacker or a distributable mod package.

For convenience, the viewer can perform step 3 directly when started with an
explicit `--write-root`. It creates a timestamped backup before applying
marker coordinates; without that flag the `Apply to AREA` control remains
disabled.

The installed copy currently has a pre-existing DLL backup/modification, so a
staging copy is especially important for separating our asset tests from
unrelated executable changes.
