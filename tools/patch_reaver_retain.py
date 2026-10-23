#!/usr/bin/env python3
"""Create a staged SR1 Remaster DLL that keeps the Reaver below full health.

The patch is deliberately byte-guarded and writes a separate output file.  It
does not modify the Steam installation unless the caller explicitly copies the
validated output into place.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pefile


# RVAs are for the current SR1 PC build.  Each change is guarded by the bytes
# observed in the user's installed DLL so a different build fails safely.
PATCHES = (
    # ProcessHealth: choose razReaverOn instead of razReaverOff when HP is
    # below the material-plane maximum.
    (0xE673E, bytes.fromhex("74"), bytes.fromhex("EB"), "ProcessHealth HP gate"),
    # razReaverOn: accept material-plane HP below GetMaxHealth().
    (0xFDDC3, bytes.fromhex("74"), bytes.fromhex("EB"), "razReaverOn HP gate"),
    # LoseHealth: do not turn the Reaver off when taking damage.
    (0xE60D5, bytes.fromhex("E8 06 7C 01 00"), bytes.fromhex("90 90 90 90 90"), "LoseHealth razReaverOff call"),
    (0xE60EE, bytes.fromhex("E8 1D 3C F9 FF"), bytes.fromhex("90 90 90 90 90"), "LoseHealth Reaver-off message"),
    # LoseHealth: do not replay the inline 0x800103 Reaver imbue/post effect
    # after every hit.  The retention state changes above remain intact.
    (0xE6119, bytes.fromhex("E8 F2 3B F9 FF"), bytes.fromhex("90 90 90 90 90"), "LoseHealth Reaver-imbue message"),
)

# Optional water-Reaver experiment.  This redirects the first water-entry
# trail-effect call through a small pair of code caves.  The first cave keeps
# the original visual effect and checks that a Soul Reaver exists.  The second
# cave checks the *forged ability* bit before calling razReaverImbue(4).
# Reaver type 4 is the original water-Reaver slot in ReaverTuneData.
WATER_REAVER_PATCHES = (
    # Reuse the old Fire-Reaver debug-menu slot as a Water-Reaver toggle.
    # The menu already has the Hold Soul Reaver bit, which causes the game to
    # instantiate the deleted weapon through its normal debug path.
    (
        0x1A09C8,
        bytes.fromhex("46 69 72 65 20 52 65 61 76 65 72 00 00"),
        bytes.fromhex("57 61 74 65 72 20 52 65 61 76 65 72 00"),
        "debug menu Water Reaver label",
    ),
    (
        0x2225E8,
        bytes.fromhex("00 80 00 00 00 00 00 00"),
        bytes.fromhex("00 20 00 00 00 00 00 00"),
        "debug menu Water Reaver mask",
    ),
    (
        0x222308,
        bytes.fromhex("00 80 00 00 00 00 00 00"),
        bytes.fromhex("00 20 00 00 00 00 00 00"),
        "debug menu Water Reaver forged-menu mask",
    ),
    (
        0x222300,
        bytes.fromhex("80 D4 B0 83 01 00 00 00"),
        bytes.fromhex("80 D4 B0 83 01 00 00 00"),
        "debug menu Water Reaver forged-menu target",
    ),
    # Hook both the initial water-crossing effect and the fully-underwater
    # effect.  Different levels/physics paths can report either transition.
    (0xFCE49, bytes.fromhex("E8 32 DF FF FF"), bytes.fromhex("E8 55 47 03 00"), "underwater-state hook"),
    (0xFCF45, bytes.fromhex("E8 36 DE FF FF"), bytes.fromhex("E8 59 46 03 00"), "water-entry hook"),
    (
        0x1315A3,
        bytes.fromhex(
            "CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC "
            "66 66 0F 1F 84 00 00 00 00 00"
        ),
        bytes.fromhex(
            # call TrailWaterFX; if a Soul Reaver exists, call the conditional
            # ability helper in the second cave; otherwise return.
            "E8 D8 97 FC FF 48 8B 05 61 77 95 02 48 85 C0 74 05 "
            # cave1 ends at 0x1315B9; cave2 begins at 0x19E402.
            # The rel32 displacement is 0x6CE49 (not 0x6CE4E).
            "E8 49 CE 06 00 C3"
        ),
        "water-entry helper cave",
    ),
    (
        0x19E402,
        bytes.fromhex(
            "CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC "
            "66 66 0F 1F 84 00 00 00 00 00"
        ),
        bytes.fromhex(
            # test debugRazielFlags1's 0x2000 forged-ability bit and avoid
            # rewriting the selection every underwater frame.  Set only the
            # normal debug imbue selection; ProcessSpecialAbilities performs
            # the INSTANCE_Post at its regular safe point.
            # These RIP-relative targets are the PC port's actual globals:
            # debugRazielFlags1 (0x3B0D480) and Raziel.currentSoulReaver
            # (0x2A88D18).  Keep the offsets byte-guarded because an off-by-one
            # here silently writes to a neighboring global and makes the
            # water selection appear to work for one frame only.
            "F6 05 77 F0 96 03 20 74 13 "
            "83 3D 07 A9 8E 02 04 74 0A "
            "C7 05 5E F0 96 03 00 20 00 00 C3"
        ),
        "water-Reaver forged-ability helper cave",
    ),
    # The remaster already creates a Soul Reaver projectile with
    # `CurrentReaver + 2`.  Its collision dispatcher recognizes the normal
    # and Fire slots, but the deleted Water slot (type 4) falls through.
    # After the normal/material cases have branched away, the remaining
    # values are current type minus two: Water=2, Fire=4, and the preserved
    # amplified/debug slot=7.  Testing the low bit sends both even values to
    # the existing fire-impact path and retaining the compare for 7 keeps the
    # amplified/debug behavior intact.
    (
        0xFF6EC,
        bytes.fromhex("41 83 E8 04"),
        bytes.fromhex("41 F6 C0 01"),
        "Water Reaver projectile dispatch",
    ),
    (
        0xFF6F2,
        bytes.fromhex("41 83 F8 03"),
        bytes.fromhex("41 83 F8 07"),
        "preserve amplified Reaver projectile dispatch",
    ),
    # Water and Fire projectiles share the game's native Reaver burn/kill
    # damage payload (0x20), but the native Water slot was deleted. Preserve
    # the original payload and carry the elemental slot (2 or 4) in the
    # hit's knock-back-distance field.
    (
        0xFF711,
        bytes.fromhex("8B 49 60 BE 20 00 00 00"),
        bytes.fromhex("E9 10 ED 09 00 90 90 90"),
        "capture elemental Reaver slot",
    ),
    (
        0xFF73A,
        bytes.fromhex("45 33 C9"),
        bytes.fromhex("44 8B CE"),
        "carry elemental marker into hit data",
    ),
    (
        0xFF73D,
        bytes.fromhex("C7 44 24 20 00 00 00 00"),
        bytes.fromhex("40 B6 20 90 90 90 90 90"),
        "restore lethal Reaver payload",
    ),
    (
        0x19E426,
        bytes.fromhex("CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC 66"),
        bytes.fromhex("41 8B F0 8B 49 60 33 C0 89 44 24 20 E9 E2 12 F6 FF"),
        "elemental Reaver slot cave",
    ),
    # The monster handler stores the hit's knock-back distance at +0x10 in
    # the 64-bit port. Convert the carried slot (2/4) to the existing
    # Monster damage types (0x10/0x20) for the impact and death state.
    (
        0x9FE7C,
        bytes.fromhex("41 BC 20 00 00 00"),
        bytes.fromhex("E9 C5 E5 0F 00 90"),
        "apply elemental death type",
    ),
    (
        0x19E446,
        bytes.fromhex("CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC"),
        bytes.fromhex("45 0F B7 65 10 41 C1 E4 03 E9 2E 1A F0 FF 90 90"),
        "elemental death type cave",
    ),
    # The native 0x20 projectile path only becomes fatal when the victim is
    # already in a particular hit state. The deleted Water slot can arrive as
    # marker 2 or 3 in MonsterHitData+0x10 depending on the source asset;
    # values below 4 are made fatal so the dissolve cannot return the monster
    # to combat. Marker 4 (the ordinary Fire slot) keeps the existing branch.
    (
        0x9FE2A,
        bytes.fromhex("48 0F BA E0 1C"),
        # Broad diagnostic pass: force the native Reaver impact branch to
        # fatal. If this stops the apparent revival, the remaining refinement
        # is only identifying the exact Water marker value.
        bytes.fromhex("41 80 7D 10 10"),
        "Water Reaver fatal hit marker",
    ),
    # The native Water death path consumes the monster's soulJuice before
    # MON_DeadEntry runs; restore the high byte of the 16-bit soulJuice field
    # (0x25c) so MON_DeadEntry creates the linked soul object.  Keep the
    # generator-managed flag so MON_KillMonster uses SAVE_DeleteInstance,
    # but clear regenTime (0x25e) so it does not re-add this instance to the
    # generator after deletion.
    (
        0x9FE31,
        bytes.fromhex("C7 44 24 40 01 00 00 00"),
        bytes.fromhex("E9 B4 E5 0F 00 90 90 90"),
        "restore Water Reaver soul and route through deletion hook",
    ),
    (
        0x19E3EA,
        bytes.fromhex("CC CC CC CC CC CC CC CC CC CC CC CC 66 66 0F 1F 84 00 00 00 00 00"),
        bytes.fromhex("80 4C 24 40 01 C6 83 5D 02 00 00 10 66 83 A3 5E 02 00 00 00 EB 60"),
        "restore Water Reaver soul and suppress regeneration cave",
    ),
    (
        0x19E460,
        bytes.fromhex("CC CC CC CC CC CC 66 66 0F 1F 84 00 00 00 00 00"),
        bytes.fromhex("E9 D4 19 F0 FF 90 90 90 90 90 90 90 90 90 90 90"),
        "Water Reaver soul cave return trampoline",
    ),
    (
        0xA41F5,
        bytes.fromhex("BA 01 00 00 00"),
        bytes.fromhex("31 D2 90 90 90"),
        "spawn Water Reaver soul as unlinked pickup",
    ),
    # Leave the original post-impact visual-FX call sequence intact.  A
    # trampoline here changes the return address of the preceding
    # INSTANCE_Post call and was the source of repeatable illegal-instruction
    # crashes at RVA 0xFF761 on the PC build.
    # Fire projectiles normally refuse to post damage while below the water
    # plane.  Water-Reaver projectiles use the same burn impact, but should be
    # able to hit submerged enemies, so remove only that early-out branch.
    (
        0xFF72D,
        bytes.fromhex("0F 8F 9D 00 00 00"),
        bytes.fromhex("90 90 90 90 90 90"),
        "allow Water Reaver projectile underwater",
    ),
    # The deleted Water projectile slot still has a valid collision/damage
    # marker, but its visual model entry is empty.  BirthProjectilePhysOb's
    # model lookup (the helper at RVA 0xBFA30) indexes the projectile data
    # table through the type stored in extraData+4.  Remap only slot 6 to the
    # Material slot (4) at that lookup, leaving extraData+4 unchanged so the
    # working Water collision/death path remains intact.
    (
        0xBBCFC,
        bytes.fromhex("48 C1 E1 04 48 03 4A 10"),
        bytes.fromhex("E9 EB 26 0D 00 90 90 90"),
        "Water Reaver orb model lookup hook",
    ),
    (
        0x18E3EC,
        bytes.fromhex("CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC"),
        bytes.fromhex(
            # The original FORCE projectile table uses model ID 2 for the
            # visible Reaver orb; model ID 1 is the small quad effect.
            "83 F9 06 75 05 B9 02 00 00 00 E9 10 00 00 00"
        ) + b"\xCC",
        "Water Reaver orb model lookup selector cave",
    ),
    (
        0x18E40B,
        bytes.fromhex("CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC CC"),
        bytes.fromhex(
            # Finish the original table address calculation and resume at
            # the original model-byte load.
            "48 C1 E1 04 48 03 4A 10 E9 EC D8 F2 FF"
        ) + b"\xCC\xCC\xCC",
        "Water Reaver orb model lookup address cave",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--water-reaver",
        action="store_true",
        help="also imbue the retained Reaver with type 4 on water entry",
    )
    args = parser.parse_args()

    pe = pefile.PE(str(args.input), fast_load=True)
    data = bytearray(args.input.read_bytes())
    applied = []
    patches = PATCHES + (WATER_REAVER_PATCHES if args.water_reaver else ())
    for rva, expected, replacement, label in patches:
        offset = pe.get_offset_from_rva(rva)
        actual = bytes(data[offset : offset + len(expected)])
        if actual != expected:
            raise SystemExit(
                f"guard failed for {label} at RVA 0x{rva:X}: "
                f"expected {expected.hex(' ')}, found {actual.hex(' ')}"
            )
        data[offset : offset + len(replacement)] = replacement
        applied.append(f"{label} @ 0x{rva:X}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(f"wrote {args.output}")
    for item in applied:
        print(f"patched {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
