#!/usr/bin/env python3
"""Retarget one serialized stream signal in a Soul Reaver scene.

The portal table and the event signal list are separate structures.  When a
portal is retargeted, its matching MultiSignal must be retargeted as well or
the collision/event code can walk the wrong signal chain.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from level_editor import _module_offset, _parse_drm, ensure_distinct_paths


# SignalInfo.length values from src/Game/SIGNAL.c.  The serialized signal
# size is (length + 1) * 4 bytes, including the id word.
SIGNAL_LENGTHS = {
    0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 2, 7: 1, 8: 1,
    9: 1, 10: 1, 11: 1, 12: 1, 13: 2, 14: 1, 15: 0, 16: 0,
    17: 0, 18: 6, 19: 2, 20: 1, 21: 1, 22: 1, 23: 0, 24: 1,
    25: 1, 26: 1,
}


def patch_stream_signal(
    source: Path,
    destination: Path,
    from_signal_num: int,
    to_name: str,
    stream_id: int,
    current_num: int | None = None,
) -> dict[str, object]:
    ensure_distinct_paths(source, destination)
    data = bytearray(source.read_bytes())
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(relative: int) -> int:
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    def i32(relative: int) -> int:
        return struct.unpack_from("<i", data, module_offset + relative)[0]

    signal_start = u32(0xD0)
    signal_end = u32(0xD4)
    if not (0 < signal_start < signal_end <= module_size):
        raise ValueError("scene has no valid serialized signal list")
    if len(to_name.encode("ascii")) >= 16:
        raise ValueError("stream signal target must fit in 15 characters")

    cursor = signal_start
    visited: set[int] = set()
    matches: list[dict[str, object]] = []
    while cursor < signal_end:
        if cursor in visited or cursor + 8 > module_size:
            raise ValueError("signal list contains a loop or out-of-range MultiSignal")
        visited.add(cursor)
        num_signals = i32(cursor)
        signal_num = struct.unpack_from("<h", data, module_offset + cursor + 4)[0]
        if not 0 <= num_signals <= 32:
            raise ValueError(f"invalid MultiSignal count {num_signals} at 0x{cursor:X}")

        signal_cursor = cursor + 8
        stream_signal = None
        for _ in range(num_signals):
            if signal_cursor + 4 > module_size:
                raise ValueError("signal record extends beyond the scene module")
            raw_id = i32(signal_cursor)
            signal_id = raw_id & 0x7FFFFFFF
            length = SIGNAL_LENGTHS.get(signal_id)
            if length is None:
                raise ValueError(f"unknown signal id {raw_id} at 0x{signal_cursor:X}")
            if signal_id == 18 and stream_signal is None:
                stream_signal = signal_cursor
            signal_cursor += (length + 1) * 4

        if signal_num == from_signal_num and stream_signal is not None:
            stream_signal_file = module_offset + stream_signal
            old_current = struct.unpack_from("<i", data, stream_signal_file + 4)[0]
            old_stream = struct.unpack_from("<i", data, stream_signal_file + 8)[0]
            old_name = bytes(data[stream_signal_file + 12 : stream_signal_file + 28]).split(b"\0", 1)[0].decode("ascii", "replace")
            new_current = from_signal_num if current_num is None else current_num
            struct.pack_into("<h", data, module_offset + cursor + 4, new_current)
            struct.pack_into("<i", data, stream_signal_file + 4, new_current)
            struct.pack_into("<i", data, stream_signal_file + 8, stream_id)
            encoded = to_name.encode("ascii") + b"\0"
            data[stream_signal_file + 12 : stream_signal_file + 28] = encoded.ljust(16, b"\0")
            matches.append({
                "offset": hex(cursor),
                "old_current": old_current,
                "old_stream": old_stream,
                "old_name": old_name,
                "new_current": new_current,
                "new_stream": stream_id,
                "new_name": to_name,
            })

        # SIGNAL_FindSignal follows the callSignal pointer stored four bytes
        # after the final serialized Signal record.
        cursor = signal_cursor + 4

    if not matches:
        raise ValueError(f"no stream MultiSignal with signal number {from_signal_num}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    _parse_drm(destination)
    return {"matches": matches, "output": str(destination), "size": destination.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--signal-num", type=int, required=True)
    parser.add_argument("--to-name", required=True, help="target name, e.g. city10,1")
    parser.add_argument("--stream-id", type=int, required=True)
    parser.add_argument("--current-num", type=int)
    args = parser.parse_args()
    result = patch_stream_signal(
        args.source,
        args.output,
        args.signal_num,
        args.to_name,
        args.stream_id,
        args.current_num,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
