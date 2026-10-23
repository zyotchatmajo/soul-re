#!/usr/bin/env python3
"""Nudge one portal frame while preserving its stream/event metadata."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

try:
    from level_editor import _module_offset, _parse_drm, ensure_distinct_paths
except ModuleNotFoundError:
    from tools.level_editor import _module_offset, _parse_drm, ensure_distinct_paths


PORTAL_SIZE = 0x5C
# StreamUnitPortal: min (0x18), max (0x20), then all three points of t1 and
# t2. Each SVector is six bytes; the remaining two bytes in each 8-byte slot
# are padding and must remain untouched.
COORDINATE_OFFSETS = (0x18, 0x20, 0x2C, 0x34, 0x3C, 0x44, 0x4C, 0x54)


def nudge_portal(source: Path, destination: Path, name: str, dx: int, dy: int, dz: int) -> dict[str, object]:
    ensure_distinct_paths(source, destination)
    data = bytearray(source.read_bytes())
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset
    terrain_relative = struct.unpack_from("<I", data, module_offset)[0]
    terrain = module_offset + terrain_relative
    stream_relative = struct.unpack_from("<I", data, terrain + 0x30)[0]
    table_file = module_offset + stream_relative
    count = struct.unpack_from("<i", data, table_file)[0]
    wanted = name.lower()
    matches = []
    for index in range(count):
        record = table_file + 4 + index * PORTAL_SIZE
        current = bytes(data[record : record + 16]).split(b"\0", 1)[0].decode("ascii", "replace")
        if current.lower() != wanted:
            continue
        for base in COORDINATE_OFFSETS:
            x, y, z = struct.unpack_from("<hhh", data, record + base)
            values = (x + dx, y + dy, z + dz)
            if any(value < -32768 or value > 32767 for value in values):
                raise ValueError(f"nudge would overflow a 16-bit portal coordinate at 0x{base:X}")
            struct.pack_into("<hhh", data, record + base, *values)
        matches.append({"index": index, "name": current, "delta": (dx, dy, dz)})
    if not matches:
        raise ValueError(f"portal {name!r} was not found")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    _parse_drm(destination)
    return {"matches": matches, "output": str(destination), "size": destination.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", required=True, help="portal name, e.g. city9,3")
    parser.add_argument("--dx", type=int, default=0)
    parser.add_argument("--dy", type=int, default=0)
    parser.add_argument("--dz", type=int, default=0)
    args = parser.parse_args()
    print(nudge_portal(args.source, args.output, args.name, args.dx, args.dy, args.dz))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
