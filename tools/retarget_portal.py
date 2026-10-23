#!/usr/bin/env python3
"""Replace one serialized portal record with a record from another scene."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from level_editor import _module_offset, _parse_drm, ensure_distinct_paths


PORTAL_SIZE = 0x5C


def portal_table(data: bytes) -> tuple[int, int, int]:
    module, _ = _module_offset(data)
    terrain = module + struct.unpack_from("<I", data, module)[0]
    table = module + struct.unpack_from("<I", data, terrain + 0x30)[0]
    count = struct.unpack_from("<i", data, table)[0]
    if count < 0 or count > 0x1000 or table + 4 + count * PORTAL_SIZE > len(data):
        raise ValueError("invalid stream portal table")
    return module, table, count


def find_record(data: bytes, table: int, count: int, name: str) -> int:
    wanted = name.lower()
    for index in range(count):
        base = table + 4 + index * PORTAL_SIZE
        current = data[base : base + 16].split(b"\0", 1)[0].decode("ascii", "strict").lower()
        if current == wanted:
            return base
    raise ValueError(f"portal {name!r} not found")


def retarget_portal(
    target: Path,
    source: Path,
    old_name: str,
    source_name: str,
    output: Path,
) -> None:
    ensure_distinct_paths(target, output)
    target_data = bytearray(target.read_bytes())
    source_data = source.read_bytes()
    _, target_table, target_count = portal_table(target_data)
    _, source_table, source_count = portal_table(source_data)
    target_record = find_record(target_data, target_table, target_count, old_name)
    source_record = find_record(source_data, source_table, source_count, source_name)
    target_data[target_record : target_record + PORTAL_SIZE] = source_data[
        source_record : source_record + PORTAL_SIZE
    ]
    # Runtime fills this pointer when the destination stream is connected.
    struct.pack_into("<I", target_data, target_record + 0x28, 0)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(target_data)
    _parse_drm(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--old-name", required=True)
    parser.add_argument("--source-name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    retarget_portal(args.target, args.source, args.old_name, args.source_name, args.output)
    print(f"Replaced {args.old_name} with {args.source_name}")
    print(f"Output: {args.output} ({args.output.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
