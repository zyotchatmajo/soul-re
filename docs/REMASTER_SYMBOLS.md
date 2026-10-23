# Remaster symbol inventory

The PDB bundle supplied by the user was inspected on 2026-10-16. It contains
symbols for `SRX.exe`, `1/sr1.dll`, and `2/sr2.dll`.

## Initial results

| PDB | Named symbols | Notes |
| --- | ---: | --- |
| `1/sr1.pdb` | 10,196 | 397 compilation modules; includes SR1 source/header paths |
| `SRX.pdb` | 3,881 | launcher/platform symbols |
| `2/sr2.pdb` | 20,781 | SR2 game symbols; retained for later cross-game comparison |

The `sr1.pdb` symbols expose the original subsystem names needed for gameplay
modding, including:

- `EVENT_*` — event VM, camera/game/instance actions, sound and voice control
- `SIGNAL_*` — portals, stream loading, camera, music, sound, voice and player control
- `STREAM_*` — level loading, adjacent-unit rendering, object relocation and fog
- `COLLIDE_*` / `PHYSICS_*` — terrain, portal, object and movement collision
- `Raziel*` / `RAZIEL_*` — player processing, interaction and debug functions
- `MON_*`, `Monster*`, `MONTABLE_*` — enemy state, combat, damage and AI
- `DEBUG_*` — level selection, teleport, reset, cheats and debug rendering

The inventory is generated at:

- `build/remaster-symbols/sr1-symbols.csv`
- `build/remaster-symbols/SRX-symbols.csv`

The reusable extractor is `tools/dump_remaster_pdb.py`.

An initial byte-signature pass compared the supplied SR1 reference DLL against
the installed `1/sr1.dll`. Of 6,328 public-code symbols tested with a 16-byte
leading signature, 1,483 matched a unique location in the installed binary.
Examples include `EVENT_ProcessEvents`, `EVENT_DoSignalAction`,
`COLLIDE_LineWithSignals`, `PhysicsMove`, `RazielProcess`,
`SIGNAL_FindSignal`, and `STREAM_LoadLevelReturn`. The generated match table is
`build/remaster-symbols/sr1-installed-matches.csv`; the matcher is
`tools/match_remaster_symbols.py`.

## Binary/PDB compatibility

The supplied `sr1.dll` and `sr1.pdb` share the same CodeView GUID, but the
installed Steam `sr1.dll` has a newer PDB age. Therefore the supplied PDB is a
very useful symbol map, but its RVAs must not be copied blindly to the current
installed DLL. The next step is to match functions by name and byte pattern
against the installed build before installing any hook or patch.

No game files were modified by this analysis.
