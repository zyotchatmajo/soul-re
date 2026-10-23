#!/usr/bin/env python3
"""Remap the February City 12 connection to the remaster stream IDs."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from level_editor import _module_offset, _parse_drm
from patch_stream_signal import SIGNAL_LENGTHS


def patch_city11(source: Path, destination: Path, stream_id: int) -> dict[str, object]:
    data = bytearray(source.read_bytes())
    module, _ = _module_offset(data)
    terrain = module + struct.unpack_from("<I", data, module)[0]
    portals = module + struct.unpack_from("<I", data, terrain + 0x30)[0]
    count = struct.unpack_from("<i", data, portals)[0]
    changed_portal = False
    for index in range(count):
        record = portals + 4 + index * 0x5C
        name = bytes(data[record : record + 16]).split(b"\0", 1)[0]
        if name.lower() == b"city12,1":
            struct.pack_into("<i", data, record + 0x14, stream_id)
            changed_portal = True
    if not changed_portal:
        raise ValueError("City 11 has no city12,1 portal")

    start, end = struct.unpack_from("<II", data, module + 0xD0)
    cursor = start
    changed_signal = False
    while cursor < end:
        count_signals = struct.unpack_from("<i", data, module + cursor)[0]
        signal_num = struct.unpack_from("<h", data, module + cursor + 4)[0]
        signal_cursor = cursor + 8
        for _ in range(count_signals):
            signal_id = struct.unpack_from("<i", data, module + signal_cursor)[0] & 0x7FFFFFFF
            length = SIGNAL_LENGTHS.get(signal_id)
            if length is None:
                raise ValueError(f"unknown signal id {signal_id} at 0x{signal_cursor:X}")
            if signal_id == 18:
                record = module + signal_cursor
                name = bytes(data[record + 12 : record + 28]).split(b"\0", 1)[0]
                if name.lower() == b"city12,1":
                    struct.pack_into("<i", data, record + 8, stream_id)
                    changed_signal = True
            signal_cursor += (length + 1) * 4
        cursor = signal_cursor + 4
    if not changed_signal:
        raise ValueError("City 11 has no city12,1 stream signal")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    _parse_drm(destination)
    return {"output": str(destination), "stream_id": stream_id, "portal": True, "signal": True}


def patch_city12(source: Path, destination: Path, stream_id: int) -> dict[str, object]:
    data = bytearray(source.read_bytes())
    module, _ = _module_offset(data)
    world_rel = struct.unpack_from("<I", data, module + 0x98)[0]
    world = bytes(data[module + world_rel : module + world_rel + 16]).split(b"\0", 1)[0]
    if world.lower() != b"city12":
        raise ValueError(f"expected city12 world name, found {world!r}")
    struct.pack_into("<i", data, module + 0xF8, stream_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    _parse_drm(destination)
    return {"output": str(destination), "stream_id": stream_id}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city11", type=Path, required=True)
    parser.add_argument("--city11-output", type=Path, required=True)
    parser.add_argument("--city12", type=Path, required=True)
    parser.add_argument("--city12-output", type=Path, required=True)
    parser.add_argument("--stream-id", type=int, default=3416)
    args = parser.parse_args()
    print(patch_city11(args.city11, args.city11_output, args.stream_id))
    print(patch_city12(args.city12, args.city12_output, args.stream_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
