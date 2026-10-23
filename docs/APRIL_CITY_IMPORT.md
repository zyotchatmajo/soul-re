# April City 8–11 import notes

The April 14, 1999 prototype stores its area files in the embedded PSX
`BIGFILE.DAT` subdirectory table. The relevant stream units are:

| stream unit | area | April scene hash | bytes |
|---:|---|---:|---:|
| 3412 | City 8 | `A9E78550` | 67,924 |
| 3413 | City 9 | `A9E80550` | 154,176 |
| 3414 | City 10 | `B1EF3600` | 18,920 |
| 3415 | City 11 | `B1EFB600` | 65,608 |

The April portal records are coherent across the four areas:

- City 8 ↔ City 9, signal 2/1
- City 9 → City 10, signals 2 and 3
- City 10 → City 9, signals 1 and 2
- City 10 ↔ City 11, signal 3/1

The City 10 and City 11 portal coordinates in the April build are identical
to the native PC scene records. City 8 is also identical. The only portal
topology change needed for the retail PC set is City 9: April replaces the
unused City 19 link with the two City 10 links.

The retail PC archive contains native City 10 and City 11 scene files, but no
matching `CITY10.PMF`/`CITY10.PNF` or `CITY11.PMF`/`CITY11.PNF` companions.
Those companions are separate `FMPa`/`FNPa` files, not the PSX April scene
records. Copying City 9's pair is therefore only a loader diagnostic, not a
format conversion.

## Current staged test

The install uses the stable native PC geometry for City 8, City 10, and City
11. City 9 uses the topology-preserving April-position candidate described
below, with the April City 9 portal table. The previous converted City 8 and
City 11 files are backed up in `AREA - Conversion-Tests` as:

- `CITY8-before-native-uniform-20261015.PCM`
- `CITY11-before-native-uniform-20261015.PCM`

This keeps the portal/link experiment isolated from the unsafe full April BSP
conversion while retaining the April City 9 coordinates that match its portal
opening.

The loader matches the number after the comma in a portal name against the
destination portal's `MSignalID`, then relocates the newly loaded level by the
difference between the two portal minima. City 9's two City 10 links are
therefore intentional alternate entrances; changing either portal table or
loading a converted scene with different minima produces a visible vertical
or lateral shift even when the stream IDs are correct.

## City 9 geometry and introductions

The April City 9 scene is not just a portal-table variant:

| data | April | PC-native |
|---|---:|---:|
| vertices | 1,960 | 1,968 |
| faces | 3,486 | 3,755 |
| normals | 1,495 | 3,065 |
| introductions | 47 | 51 |

The first conversion kept the PC-native topology and only remapped vertex
positions. The current test uses a separate full-geometry wrapper that copies
the April vertex/face/normal arrays and BSP graph while retaining the PC
metadata needed by the remaster loader. Its backup is
`CITY9-before-april-full-geometry-20261015.PCM`.

That first full-BSP attempt produced a black-screen crash because the prototype
material offsets and BSP layout were not safe for the PC loader. The active
test now uses the April arrays with the native BSP rebalanced to the April face
count and remapped material offsets. The immediately previous file is backed
up as `CITY9-before-rebalanced-geometry-20261015.PCM`.

The rebalanced test loaded but rendered incorrect textures and had invalid
collision. April City 9 has 270 texture records; the PC scene has 304 different
records, and the April archive's companion is a PSX asset rather than a PC
`.CRM`. Without converting that companion, the safe active fallback is the
native City 9 mesh plus the April portal table.

The April introduction table also contains different entries (`WRSHP`,
`STAFF`, alternate vampire/urn objects, and different IDs). Those entries are
not directly copied as raw records because their serialized command pointers,
object-name list, and event/signal references belong to the April build.

## Safe object-only test

The new `tools/import_april_objects.py` bridge imports selected April object
placements while keeping the native PC geometry, textures, BSP, and event
tables. It adds the requested names to the PC `Level.objectNameList`, copies
the April transform/radius/flags fields, and clears command/signal pointers so
the new instances cannot accidentally execute another object's event graph.

The current City 9 test imports the four April `WRSHP`/`STAFF` placements. The
active file is backed up as
`CITY9-before-april-object-import-20261015.PCM`. This test can confirm whether
the retail PC object assets are sufficient before attempting the much riskier
PSX texture/BSP conversion. It does not import April event conditionals or
camera paths yet; those require translating the event tables rather than just
copying intro records.

## City 10 transition isolation

The April topology gives City 9 two portals to City 10 (`city10,1` and
`city10,2`). City 10 correspondingly has two portals back to City 9
(`city9,2` and `city9,3`). The retail stream connector applies a relocation
offset for every reciprocal portal it finds. With both pairs enabled, the same
City 10 scene receives two different offsets during connection, which matches
the observed vertical displacement and portal crash.

The current test keeps `city8,2` and `city10,1` in City 9 and removes only
`city10,2`. City 10's second return portal remains in its file but has no
matching City 9 signal, so it cannot trigger a second relocation. The original
three-portal object test is backed up as
`CITY9-before-april-singlelink-20261015.PCM`. `tools/patch_portals.py` performs
this count-preserving portal subset operation without touching geometry or
textures.

The City 9 single-link test still crashed when entering City 10, so the active
diagnostic now also removes City 10's alternate `city9,3` return portal. City
10 keeps only `city9,2` and `city11,1`; its native geometry and companions are
otherwise unchanged. The previous native City 10 file is backed up as
`CITY10-before-single-return-20261015.PCM`. If this still crashes, the failure
is no longer explained by reciprocal portal multiplicity and the next check is
the City 10 PMF/PNF companion pair or the stream loader's scene-extension
handling.

The latest isolation test also removes the imported WRSHP/STAFF intro and
object-list additions from City 9 while preserving the two-portal subset. Its
previous object-enabled City 9 file is backed up as
`CITY9-before-no-object-test-20261015.PCM`. This checks whether the crash is
triggered while unloading the imported object trackers during the stream
transition.

The current City 9 diagnostic now uses the topology-preserving April conversion
(`city9-apr.pcm`) with the same two-portal subset. It keeps the PC face/normal
topology and BSP/material tables, but remaps the native vertex positions from
the April scene. This is intentionally a safe coordinate-frame test, not a
claim that the PSX texture companion or full April BSP has been converted. Its
previous object-free native City 9 file is backed up as
`CITY9-before-april-topology-test-20261015.PCM`.

## Stream-signal alignment

The portal table is not the only place where an area connection is recorded.
Each portal's `MSignalID` also needs a matching `MultiSignal` in the level's
event signal list. The native PC City 9 scene still had signal 10 targeting
`city19,11`, while the converted portal table targeted City 10 with portal
link number 2.
The game was therefore entering the stream-signal parser with an unmatched
portal/event definition and crashing during the transition.

The current test file (`city9-apr-topology-pair2-signal10-city10.pcm`)
retargets that existing stream signal in place while preserving its original
event number:

- MultiSignal number: 10 -> 10
- stream target: `city19,11` -> `city10,2`
- stream ID: 3423 -> 3414
- current signal number: 10 -> 10

The active install has this candidate as `AREA\\CITY\\BIN\\CITY9.PCM`; the
pre-patch file is backed up as
`AREA - Conversion-Tests\\CITY9-before-signal10-test-20261015.PCM`.

## Offset correction test

The first reciprocal pair (`city10,1` <-> `city9,2`) is internally valid but
relocates City 10 by `(2802, 1785, 644)`, making the destination visibly float
upward. The second April pair (`city10,2` <-> `city9,3`) relocates it by only
`(677, -223, 212)` and matches the neighboring geometry much more closely.

The active test now uses that second pair. City 9's portal link number 2 is
paired with City 10's `MSignalID` 2, while the event keeps its original signal
number 10 and now targets `city10,2`. City 10 retains `city9,3` plus its City 11
link. The
previous crash-free first-pair files are backed up as:

- `CITY9-before-pair2-offset-test-20261015.PCM`
- `CITY10-before-pair2-offset-test-20261015.PCM`

The manual portal nudge was reverted after comparing all three April scenes.
The nudge changed only City 10's `city9,3` portal while leaving its
`city11,1` portal unchanged. That made the City 10 geometry appear closer to
City 9 but changed the frame used for the next City 10 -> City 11 transition,
so City 11 became displaced as well.

The April geometry comparison gives a stronger result than visual nudging:

- April and PC City 10 have the same 372 faces and 208 native vertices; only
  four coordinates differ by one unit.
- April and PC City 11 have identical 789 vertex positions and 1680 faces.
- The reciprocal City 9/10 portal corners already coincide under the native
  pair-2 frame: City 9 `city10,2` min `(-4467,-5120,-642)` maps to City 10
  `city9,3` min `(-5144,-4897,-854)` with translation `(677,-223,212)`.
- City 10 `city11,1` and City 11 `city10,3` likewise retain their matching
  native/April coordinates.

The connected group also inherits the initial City 9 BSP origin. The April
root sphere is `(3336,-6432,6520)`, while the PC wrapper's first root was
`(3137,-6361,6602)`. The loader negates that root before preloading connected
rooms, so the discrepancy moves City 9 and every room loaded from it by
`(-199,+71,+82)`. The current City 9 candidate uses the April root position
and radius `13075`; the previous file is backed up as
`AREA - Conversion-Tests\\CITY9-before-root-origin-test-20261015.PCM`.

The current full-topology test restores both April City 10 portal entries
(`city10,1` and `city10,2`), City 10's full portal table (`city9,2`,
`city9,3`, and `city11,1`), and the April-equivalent City 11 wrapper. The
pre-test files are backed up as `CITY9-before-full-april-topology-20261015.PCM`,
`CITY10-before-full-april-topology-20261015.PCM`, and
`CITY11-before-full-april-topology-20261015.PCM`.

## Current isolation build (2026-10-15)

The full-topology/root experiment was reverted after it moved the connected
rooms in the wrong direction. The active install now uses City 9's native PC
geometry with only the pair-2 portal and stream-signal correction:

- `CITY9.PCM`: native geometry, portals `city8,2` and `city10,2`, signal 10
  retargeted to `city10,2`/stream 3414
- `CITY10.PCM`: native pair-2 scene (`city9,3` and `city11,1`)
- `CITY11.PCM`: native scene

This isolates malformed April-position geometry from the room-origin problem.
The previous active City 9 file is preserved as
`AREA - Conversion-Tests\\CITY9-before-native-geometry-isolation-20261015.PCM`.

## Friend Bigfile comparison (2026-10-15)

`D:\\Waifu\\BigfileWithCutAreas\\bigfile.dat` was inspected as a packed
remaster/PC asset table. Its City records are the unmodified native PC files:

- City 9: 169,432-byte `city9.drm`, plus the original 32-byte `city9.pnf`
  and 244,820-byte `city9.pmf`
- City 10: 18,644-byte `city10.drm`
- City 11: 65,088-byte `city11.drm`

The extracted City 9 record still contains `city19,11`, not a City 10 portal.
Therefore this Bigfile does not contain a working City 9 -> City 10 portal
solution; it is a useful known-good native geometry baseline, but it cannot by
itself explain the cross-room alignment. The extracted comparison files are
under `build/friend-bigfile-extracted/`.

The active `AREA\\CITY\\BIN` test now uses those exact native scenes as
`CITY9.PCM`, `CITY10.PCM`, and `CITY11.PCM`. The former converted files are
backed up under `AREA - Conversion-Tests\\friend-native-baseline-20261015`.

For the portal test, City 9's native `city19,11` record was replaced in place
with the verified `city10,2` frame and signal 10 was retargeted to stream 3414.
The untouched native City 9 is preserved as
`AREA - Conversion-Tests\\CITY9-friend-native-before-portal-test-20261015.PCM`.

The next diagnostic preserves the native City 9 portal geometry and changes
only its metadata to `city10,2`/signal 3. This produces a City 10 relocation
of `(575,-223,-1068)` instead of the April-frame relocation `(677,-223,212)`;
the resulting 1,280-unit vertical difference directly tests the observed
high-spawn error.

That portal-only test was reverted: the native City 9 doorway and City 10's
`city9,3` doorway have different extents, so no translation can make the two
meshes coincide. The active City 9 is again the clean native scene without a
City 10 link.

## BSP pointer correction (2026-10-15)

The geometry-wrapper converter had one structural bug in its optional early
BSP import: it copied the early BSP graph correctly, but wrote the graph's
start address into `Terrain.BSPTreeArray` instead of the address of the copied
`BSPTree` records inside that graph. The runtime then interpreted the first
BSP node as a tree record, producing invalid roots/global offsets and making
the April geometry candidates appear displaced or crash. The converter now
returns the translated tree-array address. A new City 9 test was generated
from the April geometry with this fix and with only the reciprocal `city10,2`
portal retained, avoiding the duplicate relocation pass.
