#!/usr/bin/env python3
"""Retarget a portal name and IDs while preserving its geometric frame."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from level_editor import _module_offset, _parse_drm, ensure_distinct_paths


PORTAL_SIZE = 0x5C


def find_table(data: bytes) -> tuple[int, int]:
    module, _ = _module_offset(data)
    terrain = module + struct.unpack_from("<I", data, module)[0]
    table = module + struct.unpack_from("<I", data, terrain + 0x30)[0]
    count = struct.unpack_from("<i", data, table)[0]
    if count < 0 or count > 0x1000 or table + 4 + count * PORTAL_SIZE > len(data):
        raise ValueError("invalid stream portal table")
    return table, count


def retarget(source: Path, output: Path, old_name: str, new_name: str, signal_id: int, stream_id: int) -> None:
    ensure_distinct_paths(source, output)
    data = bytearray(source.read_bytes())
    table, count = find_table(data)
    wanted = old_name.lower()
    for index in range(count):
        base = table + 4 + index * PORTAL_SIZE
        current = data[base : base + 16].split(b"\0", 1)[0].decode("ascii", "strict").lower()
        if current != wanted:
            continue
        encoded = new_name.encode("ascii")
        if len(encoded) >= 16:
            raise ValueError("portal name must fit in 15 characters")
        data[base : base + 16] = encoded.ljust(16, b"\0")
        struct.pack_into("<ii", data, base + 0x10, signal_id, stream_id)
        struct.pack_into("<I", data, base + 0x28, 0)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        _parse_drm(output)
        print(f"Retargeted {old_name} -> {new_name} while preserving the portal frame")
        print(f"Output: {output} ({output.stat().st_size:,} bytes)")
        return
    raise ValueError(f"portal {old_name!r} not found")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--old-name", required=True)
    parser.add_argument("--new-name", required=True)
    parser.add_argument("--signal-id", type=int, required=True)
    parser.add_argument("--stream-id", type=int, required=True)
    args = parser.parse_args()
    retarget(args.source, args.output, args.old_name, args.new_name, args.signal_id, args.stream_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
