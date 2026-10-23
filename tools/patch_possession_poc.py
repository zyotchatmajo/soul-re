#!/usr/bin/env python3
"""Build a debug-only Possession proof-of-concept for the SR1 remaster.

The current remaster still contains MONAPI_PossessNext at RVA 0x98D60.  This
patch redirects the existing controller-message enqueue for the command that
produces 0x80000020 to that selector.  It intentionally does *not* replace
the player instance or monster AI yet; the first test is only to verify that
the dormant target-selection path is live in the PC build.

Input and output are separate files and the input bytes are guarded.  The
script is intended to be applied to the already-tested Water Reaver DLL.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import pefile


# Current PC remaster RVAs.  At 0xE55FA GetControllerMessages enqueues the
# 0x80000020 command (the existing hold/throw action).  Replacing only the
# call instruction preserves the surrounding function prologue/epilogue and
# gives MONAPI_PossessNext its required no-argument ABI.
TRIGGER_RVA = 0xE55FA
TRIGGER_EXPECTED = bytes.fromhex("E8 C1 76 02 00")
POSSESS_NEXT_RVA = 0x98D60


def rel_call(from_rva: int, target_rva: int) -> bytes:
    displacement = target_rva - (from_rva + 5)
    if not -(1 << 31) <= displacement < (1 << 31):
        raise ValueError("relative call is out of range")
    return b"\xE8" + struct.pack("<i", displacement)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    pe = pefile.PE(str(args.input), fast_load=True)
    data = bytearray(args.input.read_bytes())
    offset = pe.get_offset_from_rva(TRIGGER_RVA)
    actual = bytes(data[offset : offset + len(TRIGGER_EXPECTED)])
    if actual != TRIGGER_EXPECTED:
        raise SystemExit(
            f"guard failed at RVA 0x{TRIGGER_RVA:X}: "
            f"expected {TRIGGER_EXPECTED.hex(' ')}, found {actual.hex(' ')}"
        )

    replacement = rel_call(TRIGGER_RVA, POSSESS_NEXT_RVA)
    data[offset : offset + len(replacement)] = replacement
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(f"wrote {args.output}")
    print(
        "patched MONAPI_PossessNext trigger: "
        f"0x{TRIGGER_RVA:X} -> 0x{POSSESS_NEXT_RVA:X}"
    )
    print("test input: tap the existing hold/throw command once near a humanoid host")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
