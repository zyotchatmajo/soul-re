#!/usr/bin/env python3
"""Add the first control-transfer test to the possession proof-of-concept.

MONAPI_PossessNext already selects a host and the camera follows it, but the
remaster's RazielProcess wrapper still passes the normal player instance to
mdRazielProcess.  This patch redirects that wrapper's ProcessTimers call to a
20-byte executable padding slot.  The stub preserves the normal call when the
selected-host global is null; when it is non-null it makes the selected host
the instance kept in RDI for the remainder of the frame.

This is intentionally a test build.  It does not overwrite the installed DLL
and all writes are guarded by the exact bytes from the known POC build.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import pefile


# Current Steam-build RVAs.
CALLSITE_RVA = 0xFC17D
CALLSITE_EXPECTED = bytes.fromhex("E8 4E B8 FE FF")
CODE_CAVE_RVA = 0x17D64C
CODE_CAVE_SIZE = 20
CODE_CAVE_EXPECTED = b"\xCC" * CODE_CAVE_SIZE
PROCESS_TIMERS_RVA = 0xE79D0
SELECTED_HOST_GLOBAL_RVA = 0x2E8DE9


def rel32(from_rva: int, target_rva: int) -> bytes:
    value = target_rva - (from_rva + 5)
    return struct.pack("<i", value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    pe = pefile.PE(str(args.input), fast_load=True)
    data = bytearray(args.input.read_bytes())

    call_offset = pe.get_offset_from_rva(CALLSITE_RVA)
    actual_call = bytes(data[call_offset : call_offset + len(CALLSITE_EXPECTED)])
    if actual_call != CALLSITE_EXPECTED:
        raise SystemExit(
            f"guard failed at call site RVA 0x{CALLSITE_RVA:X}: "
            f"expected {CALLSITE_EXPECTED.hex(' ')}, found {actual_call.hex(' ')}"
        )

    cave_offset = pe.get_offset_from_rva(CODE_CAVE_RVA)
    actual_cave = bytes(data[cave_offset : cave_offset + CODE_CAVE_SIZE])
    if actual_cave != CODE_CAVE_EXPECTED:
        raise SystemExit(
            f"guard failed at code cave RVA 0x{CODE_CAVE_RVA:X}: "
            f"expected {CODE_CAVE_EXPECTED.hex(' ')}, found {actual_cave.hex(' ')}"
        )

    # 20 bytes exactly:
    #   mov rax,[rip+selected_host]
    #   test rax,rax
    #   je   normal_call
    #   mov rdi,rax
    # normal_call:
    #   jmp ProcessTimers
    stub = bytearray()
    stub += b"\x48\x8B\x05" + struct.pack(
        "<i", SELECTED_HOST_GLOBAL_RVA - (CODE_CAVE_RVA + 7)
    )
    stub += b"\x48\x85\xC0\x74\x05\x48\x89\xC7"
    stub += b"\xE9" + rel32(CODE_CAVE_RVA + len(stub), PROCESS_TIMERS_RVA)
    if len(stub) != CODE_CAVE_SIZE:
        raise AssertionError(f"unexpected stub size: {len(stub)}")

    data[cave_offset : cave_offset + CODE_CAVE_SIZE] = stub
    data[call_offset : call_offset + 5] = b"\xE8" + rel32(CALLSITE_RVA, CODE_CAVE_RVA)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(f"wrote {args.output}")
    print(
        "patched mdRazielProcess control owner: "
        f"call RVA 0x{CALLSITE_RVA:X} -> stub RVA 0x{CODE_CAVE_RVA:X}"
    )
    print("fallback remains the original ProcessTimers path when no host is selected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
