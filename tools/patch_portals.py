#!/usr/bin/env python3
"""Create a scene copy with a selected stream-portal subset.

Portal records are fixed-size and the scene already contains a relocatable
pointer to the portal block.  Reducing the count in place is therefore safer
than rebuilding the whole scene or changing geometry/BSP data.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from level_editor import _module_offset, _parse_drm, ensure_distinct_paths


PORTAL_SIZE = 0x5C


def patch_portals(source: Path, destination: Path, keep: set[str]) -> list[str]:
    ensure_distinct_paths(source, destination)
    data = bytearray(source.read_bytes())
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset
    terrain_relative = struct.unpack_from("<I", data, module_offset)[0]
    if terrain_relative >= module_size:
        raise ValueError("terrain pointer is outside the scene module")
    stream_relative = struct.unpack_from("<I", data, module_offset + terrain_relative + 0x30)[0]
    if stream_relative < 4 or stream_relative + 4 > module_size:
        raise ValueError("stream portal table pointer is outside the scene module")
    table_file = module_offset + stream_relative
    count = struct.unpack_from("<i", data, table_file)[0]
    if count < 0 or count > 0x1000 or table_file + 4 + count * PORTAL_SIZE > len(data):
        raise ValueError("stream portal table has an invalid count or span")

    records: list[bytes] = []
    names: list[str] = []
    for index in range(count):
        record = bytes(data[table_file + 4 + index * PORTAL_SIZE : table_file + 4 + (index + 1) * PORTAL_SIZE])
        name = record[:16].split(b"\0", 1)[0].decode("ascii", errors="strict").lower()
        if name in keep:
            records.append(record)
            names.append(name)
    if not records:
        raise ValueError(f"none of the requested portal names exist; available: {', '.join(names)}")

    struct.pack_into("<i", data, table_file, len(records))
    for index, record in enumerate(records):
        start = table_file + 4 + index * PORTAL_SIZE
        data[start : start + PORTAL_SIZE] = record

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    _parse_drm(destination)
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="source scene")
    parser.add_argument("--output", type=Path, required=True, help="new scene copy")
    parser.add_argument(
        "--keep",
        dest="keep",
        action="append",
        required=True,
        help="portal name to retain, e.g. city10,1; repeat for multiple portals",
    )
    args = parser.parse_args()
    names = patch_portals(args.source, args.output, {name.strip().lower() for name in args.keep})
    print(f"Retained portals: {', '.join(names)}")
    print(f"Output: {args.output} ({args.output.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
