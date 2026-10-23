#!/usr/bin/env python3
"""Patch the possession POC with a state-consistent control handoff.

The first control test changed only mdRazielProcess's local instance argument.
Raziel's global CharacterState still referred to Raziel, which is unsafe when
the state handlers operate on a possessed monster.  This version updates the
global CharacterInstance pointer as well as RDI before entering ProcessTimers.
It uses two verified 20-byte executable padding slots and leaves the original
ProcessTimers path untouched when no host is selected.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import pefile


CALLSITE_RVA = 0xFC17D
CALLSITE_EXPECTED = bytes.fromhex("E8 4E B8 FE FF")
CAVE1_RVA = 0x17D64C
CAVE2_RVA = 0x19F132
CAVE_SIZE = 20
CAVE_EXPECTED = b"\xCC" * CAVE_SIZE
PROCESS_TIMERS_RVA = 0xE79D0
SELECTED_HOST_GLOBAL_RVA = 0x2E8DE9
# Resolved from the installed build's InitStates: &Raziel.State.CharacterInstance.
CHARACTER_INSTANCE_GLOBAL_RVA = 0x2A88588


def rel32(from_rva: int, target_rva: int) -> bytes:
    return struct.pack("<i", target_rva - (from_rva + 5))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    pe = pefile.PE(str(args.input), fast_load=True)
    data = bytearray(args.input.read_bytes())

    call_offset = pe.get_offset_from_rva(CALLSITE_RVA)
    actual_call = bytes(data[call_offset : call_offset + 5])
    if actual_call != CALLSITE_EXPECTED:
        raise SystemExit(
            f"guard failed at call site RVA 0x{CALLSITE_RVA:X}: "
            f"expected {CALLSITE_EXPECTED.hex(' ')}, found {actual_call.hex(' ')}"
        )

    for rva in (CAVE1_RVA, CAVE2_RVA):
        offset = pe.get_offset_from_rva(rva)
        actual = bytes(data[offset : offset + CAVE_SIZE])
        if actual != CAVE_EXPECTED:
            raise SystemExit(
                f"guard failed at code cave RVA 0x{rva:X}: "
                f"expected {CAVE_EXPECTED.hex(' ')}, found {actual.hex(' ')}"
            )

    # Cave 1 loads the dormant selected-host global and chains to cave 2.
    stub1 = bytearray()
    stub1 += b"\x48\x8B\x05" + struct.pack(
        "<i", SELECTED_HOST_GLOBAL_RVA - (CAVE1_RVA + 7)
    )
    stub1 += b"\xE9" + rel32(CAVE1_RVA + len(stub1), CAVE2_RVA)
    stub1 += b"\xCC" * (CAVE_SIZE - len(stub1))

    # Cave 2: null -> original ProcessTimers; non-null -> update global state,
    # make the selected host the active RDI instance, then call ProcessTimers.
    stub2 = bytearray(b"\x48\x85\xC0\x74\x0A")
    stub2 += b"\x48\x89\x05" + struct.pack(
        "<i", CHARACTER_INSTANCE_GLOBAL_RVA - (CAVE2_RVA + len(stub2) + 7)
    )
    stub2 += b"\x48\x89\xC7"
    stub2 += b"\xE9" + rel32(CAVE2_RVA + len(stub2), PROCESS_TIMERS_RVA)
    if len(stub1) != CAVE_SIZE or len(stub2) != CAVE_SIZE:
        raise AssertionError(f"unexpected stub sizes: {len(stub1)}, {len(stub2)}")

    data[pe.get_offset_from_rva(CAVE1_RVA) : pe.get_offset_from_rva(CAVE1_RVA) + CAVE_SIZE] = stub1
    data[pe.get_offset_from_rva(CAVE2_RVA) : pe.get_offset_from_rva(CAVE2_RVA) + CAVE_SIZE] = stub2
    data[call_offset : call_offset + 5] = b"\xE8" + rel32(CALLSITE_RVA, CAVE1_RVA)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(f"wrote {args.output}")
    print("patched local and Raziel.State.CharacterInstance control pointers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
