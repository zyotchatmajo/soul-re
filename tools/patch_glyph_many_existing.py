#!/usr/bin/env python3
"""Upgrade the v37 limb glow to a finite full-body multi-segment glow."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import pefile


FX_GLOW_MANY_RVA = 0x53320


def rel32(target: int, next_rva: int) -> bytes:
    return struct.pack("<i", target - next_rva)


def patch(input_path: Path, output_path: Path) -> None:
    pe = pefile.PE(str(input_path), fast_load=True)
    raw = bytearray(input_path.read_bytes())
    section = next(s for s in pe.sections if s.Name.rstrip(b"\0") == b".pce")
    base = section.VirtualAddress
    start = section.PointerToRawData
    data = raw[start : start + section.SizeOfRawData]

    # Locate the v37 stub and recover its original continuation from the
    # existing conditional branch. This avoids hard-coding payload offsets.
    prefix = bytes.fromhex("48 8b 4c 24 60 48 85 c9 0f 84")
    patch_start = data.find(prefix)
    if patch_start < 0:
        raise SystemExit("v37 glow stub not found")
    jcc_next = patch_start + 14
    continuation = jcc_next + struct.unpack_from("<i", data, patch_start + 10)[0]
    routine_off = 0x700
    routine_rva = base + routine_off

    # Native FX_DoInstanceManySegmentGlow(instance, segment=0, count=50,
    # &0x4080FF, numColors=1, cycle=0, height=180), then set the returned
    # glow's 16-bit lifetime to 19,800 ticks (~20 seconds).
    routine = bytearray()
    routine += bytes.fromhex("c7 44 24 40 ff 80 40 00")
    routine += bytes.fromhex("ba 00 00 00 00")
    routine += bytes.fromhex("41 b8 32 00 00 00")
    routine += bytes.fromhex("4c 8d 4c 24 40")
    routine += bytes.fromhex("c7 44 24 20 01 00 00 00")
    routine += bytes.fromhex("c7 44 24 28 00 00 00 00")
    routine += bytes.fromhex("c7 44 24 30 b4 00 00 00")
    call = len(routine)
    routine += b"\xe8" + rel32(FX_GLOW_MANY_RVA, routine_rva + call + 5)
    routine += bytes.fromhex("48 85 c0")
    routine += b"\x0f\x84" + rel32(base + continuation, routine_rva + len(routine) + 6)
    routine += bytes.fromhex("66 c7 40 1c 58 4d")
    routine += bytes.fromhex("48 8b 4c 24 60")
    jmp = len(routine)
    routine += b"\xe9" + rel32(base + continuation, routine_rva + jmp + 5)
    if len(routine) > 128:
        raise SystemExit(f"routine is unexpectedly large: {len(routine)} bytes")

    data[routine_off : routine_off + 128] = routine + b"\0" * (128 - len(routine))
    raw[start : start + section.SizeOfRawData] = data
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(raw)
    print(f"wrote {output_path}; many-segment glow routine RVA 0x{routine_rva:X}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    patch(args.input, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
