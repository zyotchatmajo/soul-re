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

The remaster's larger character and enemy models are stored as SRM files rather
than PCMs. The viewer now decodes the SR1 SRM mesh layout as well: the installed
`1/OBJECT` tree contains 349 SRMs and 29 object PCMs, for 378 selectable object
meshes in total. SRL files are animation matrix streams: after the 20-byte
header, each frame contains a fixed number of 48-byte (3x4 float) bone
transforms. The viewer reads those transforms and the SRM vertex bone
indices/weights, so an SRM placement can be previewed with a selected SRL
animation.

SRA files are the original relocatable object animation archives, not AI
scripts. The viewer's SRA browser reads their root keylist table and exposes
each embedded clip's key count, duration, section count, and channel-stream
offsets. For example, `MORBOSS.SRA` contains 14 clips and automatically pairs
with `MORBOSS_1.SRM` in the viewer. The viewer now decodes the remaster's
float-channel SRA blocks, including the packed G2
rotation/scale/translation flags, builds frame matrices, and enables
**Play/Pause SRA clip** for the paired SRM preview. Clips whose stream and flag
counts differ report that they were decoded through their shared channel prefix.
The separate **Play/Pause SRL playback** button remains live for the already-
supported SRL matrix streams. Monster AI/state behavior is in executable
overlays and object/event data, so it is not expected to be contained in the SRA
archive.

SRM previews now read the per-vertex material index and UV bytes, pair each
material with its `<name>_D.DDS` diffuse texture from `1/TEX`, and render the
material groups in WebGL. The viewer converts DDS to browser-compatible PNG on
demand; `--texture-root` can override the automatic sibling `TEX` directory.

The viewer can therefore inspect remaster levels directly:

```powershell
python tools/level_viewer.py `
  --root 'F:\SteamLibrary\steamapps\common\Soul Reaver I-II\1\AREA' `
  --object-root 'F:\SteamLibrary\steamapps\common\Soul Reaver I-II\1\OBJECT' `
  --texture-root 'F:\SteamLibrary\steamapps\common\Soul Reaver I-II\1\TEX' `
  --drm CITY/BIN/city9.drm `
  --open
```

The viewer's new Placements panel can add object, spawn, trigger, and light
sidecar entries by raycasting onto the terrain. Exported
`soul-re-placement-v1` JSON is an editor artifact, and the viewer can also
append its `object` entries to a staged scene's runtime intro table when
started with `--write-root`. Each apply makes a timestamped backup. The mesh
stem is written as the runtime's 16-byte object name; non-object placement
kinds remain sidecar-only until their table formats are decoded.

For a skeletal preview, choose an SRM in the object field, choose an SRL in the
animation field, and enable **play animation**. The animation selector is kept
on the placement and is included in exported placement JSON; it does not alter
the game asset until the placement is applied to the AREA scene.

## What we can mod first

The lowest-risk first target is a loose `.drm` or area `.pcm` scene asset. The
current tools can export its geometry and markers, change signed 16-bit marker
positions, validate the result, and write a new asset without touching the
Steam installation. Object PCMs and SRMs are currently read-only previews in
the viewer; their meshes are referenced by newly appended scene intro records
rather than modified in place. Texture replacements are
another practical target because the remaster exposes DDS files in `AREA/TEX`
and `AREA/PIX`.

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

## Importing an early-build level

The July 14, 1999 prototype in `D:\\2 16` uses an alternate BIGFILE index, but
the disc's `CATDATA` still gives us the stored offset for every file.  The
extractor reads `CATDATA` from the sibling file or directly from the PSX image,
then copies the catalog span without changing the source archive:

```powershell
python tools/extract_early_bigfile.py `
  --source 'D:\\2 16' `
  --output build/early-mrlock6 `
  --select '*mrlock6.drm' `
  --pcm
```

For area scenes, `--pcm` changes only the output extension (`.drm` → `.pcm`);
the relocatable bytes and their padding are preserved.  This is enough for the
viewer to parse the early scene as a `pcm-level` and is the first compatibility
test.  It is not yet proof that the remaster runtime accepts every early
asset: the prototype scene has a different relocation-table size and marker
layout from the remaster's corresponding PCM, and its companion `.crm` files
may need a separate mapping.  Test the staged output first, then make a backup
before replacing a loose file in the Steam `AREA` tree.
