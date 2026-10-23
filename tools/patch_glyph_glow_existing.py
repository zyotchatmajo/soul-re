#!/usr/bin/env python3
"""Replace the v33 low-health power ring with a native cyan segment glow.

This patches the existing possession payload in-place so the other remaster
changes already present in the DLL are preserved.  It does not touch the
original game code or the possession hooks.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import pefile


FX_GLOW_WITH_TIME_RVA = 0x53470
OLD_POWER_RING_RVA = 0x534B0


def rel32(target: int, next_rva: int) -> bytes:
    return struct.pack("<i", target - next_rva)


def patch(input_path: Path, output_path: Path) -> None:
    pe = pefile.PE(str(input_path), fast_load=True)
    raw = bytearray(input_path.read_bytes())
    section = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".pce")
    base = section.VirtualAddress
    start = section.PointerToRawData
    end = start + section.SizeOfRawData
    data = raw[start:end]

    # The v33 payload has two player reloads. Select the one that is followed
    # by the old FX_DoInstancePowerRing setup (mov edx, 750).
    reload = bytes.fromhex("48 8b 4c 24 60")
    ring_setup = bytes.fromhex("ba ee 02 00 00")
    candidates = [i for i in range(len(data)) if data.startswith(reload, i)]
    patch_start = next(
        i for i in candidates if 0 < data.find(ring_setup, i, i + 48) - i < 48
    )
    ring_pos = data.find(ring_setup, patch_start, patch_start + 48)
    call_pos = data.find(b"\xe8", ring_pos, ring_pos + 40)
    if ring_pos < 0 or call_pos < 0:
        raise SystemExit("could not locate v33 power-ring call")
    # Confirm this is the expected call target before modifying anything.
    call_target = call_pos + 5 + struct.unpack_from(
        "<i", data, call_pos + 1
    )[0]
    if base + call_target != OLD_POWER_RING_RVA:
        raise SystemExit(
            f"unexpected FX call target 0x{base + call_target:X}; refusing to patch"
        )
    continuation = call_pos + 5
    old_len = continuation - patch_start

    routine_off = 0x700
    routine_rva = base + routine_off
    if routine_off + 128 > len(data):
        raise SystemExit(".pce section is too small for the glow routine")
    if any(data[routine_off : routine_off + 128]):
        raise SystemExit("glow routine area is not unused")

    # Stub: retain the existing null check, then branch to the appended glow
    # routine.  The original continuation is the old glyph_fx_done label.
    stub = bytearray()
    stub += b"\x48\x8b\x4c\x24\x60"  # reload player
    stub += b"\x48\x85\xc9"            # test player
    stub += b"\x0f\x84" + rel32(base + continuation, base + patch_start + 14)
    stub += b"\xe9" + rel32(routine_rva, base + patch_start + 19)
    stub += b"\x90" * (old_len - len(stub))
    if len(stub) > old_len:
        raise SystemExit(f"stub grew to {len(stub)} bytes; old span is {old_len}")
    data[patch_start : patch_start + old_len] = stub

    # Native FX_DoInstanceOneSegmentGlowWithTime(instance, segment 41,
    # &0x4080FF, 1, cycle 0, width 100, height 160, lifetime 600).
    routine = bytearray()
    routine += bytes.fromhex("c7 44 24 40 ff 80 40 00")
    routine += bytes.fromhex("4c 8d 44 24 40")
    routine += bytes.fromhex("ba 29 00 00 00")
    routine += bytes.fromhex("41 b9 01 00 00 00")
    routine += bytes.fromhex("c7 44 24 20 00 00 00 00")
    routine += bytes.fromhex("c7 44 24 28 64 00 00 00")
    routine += bytes.fromhex("c7 44 24 30 a0 00 00 00")
    routine += bytes.fromhex("c7 44 24 38 58 02 00 00")
    call = len(routine)
    routine += b"\xe8" + rel32(FX_GLOW_WITH_TIME_RVA, routine_rva + call + 5)
    routine += bytes.fromhex("48 8b 4c 24 60")  # restore volatile RCX
    jmp = len(routine)
    routine += b"\xe9" + rel32(base + continuation, routine_rva + jmp + 5)
    data[routine_off : routine_off + len(routine)] = routine

    raw[start:end] = data
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(raw)
    print(f"wrote {output_path}; glow routine RVA 0x{routine_rva:X}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    patch(args.input, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
