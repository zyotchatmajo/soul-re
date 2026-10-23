#!/usr/bin/env python3
"""Trim obsolete trailing stream MultiSignals from a serialized Soul Reaver scene.

The level header stores SignalListStart/SignalListEnd as module-relative offsets.
When a portal is removed, its MultiSignal must also be excluded from this range;
otherwise collision code can still dispatch the old transition.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from level_editor import _module_offset, _parse_drm


def trim(source: Path, destination: Path, keep_count: int) -> dict[str, object]:
    data = bytearray(source.read_bytes())
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(relative: int) -> int:
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    def i32(relative: int) -> int:
        return struct.unpack_from("<i", data, module_offset + relative)[0]

    start = u32(0xD0)
    end = u32(0xD4)
    if not (0 < start < end <= module_size):
        raise ValueError("scene has no valid serialized signal list")

    cursor = start
    visited: set[int] = set()
    records: list[tuple[int, int, int]] = []
    while cursor < end:
        if cursor in visited or cursor + 8 > module_size:
            raise ValueError("signal list contains a loop or out-of-range MultiSignal")
        visited.add(cursor)
        count = i32(cursor)
        number = struct.unpack_from("<h", data, module_offset + cursor + 4)[0]
        if not 0 <= count <= 32:
            raise ValueError(f"invalid MultiSignal count {count} at 0x{cursor:X}")
        # Stream MultiSignals in these scenes consist of one type-18 record;
        # the terminal type-15 record leads to the next MultiSignal.
        signal_cursor = cursor + 8
        for _ in range(count):
            signal_id = i32(signal_cursor) & 0x7FFFFFFF
            if signal_id == 18:
                length = 6
            elif signal_id == 15:
                length = 0
            else:
                # The stream table should not contain other records, but keep
                # the parser conservative for future files.
                raise ValueError(f"unsupported signal id {signal_id} at 0x{signal_cursor:X}")
            signal_cursor += (length + 1) * 4
        next_cursor = signal_cursor + 4
        records.append((cursor, number, next_cursor))
        cursor = next_cursor

    if not records:
        raise ValueError("scene has an empty signal list")
    if not 0 < keep_count <= len(records):
        raise ValueError(f"keep-count must be between 1 and {len(records)}")
    keep = records[:keep_count]
    new_end = keep[-1][2]
    if new_end > end:
        raise ValueError("computed signal-list end is invalid")
    struct.pack_into("<I", data, module_offset + 0xD4, new_end)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    _parse_drm(destination)
    return {
        "input_end": hex(end),
        "output_end": hex(new_end),
        "removed_signal_numbers": [n for _, n, _ in records[keep_count:]],
        "output": str(destination),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep-count", type=int, required=True)
    args = parser.parse_args()
    print(trim(args.source, args.output, args.keep_count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
