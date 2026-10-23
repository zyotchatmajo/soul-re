#!/usr/bin/env python3
"""Import selected April-prototype object placements into a native PC scene.

This is deliberately an object-only bridge.  It does not copy prototype
geometry, textures, BSP data, or event command pointers.  Those structures use
different PC/PSX layouts and are handled separately by the conversion tool.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from level_editor import _append_object_names, _append_scene_placements, _module_offset


def _read_april_placements(path: Path, requested: set[str]) -> list[dict[str, object]]:
    data = path.read_bytes()
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("April scene field is outside the module")
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    def i32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("April scene field is outside the module")
        return struct.unpack_from("<i", data, module_offset + relative)[0]

    terrain_relative = u32(0)
    if terrain_relative >= module_size:
        raise ValueError("April scene terrain pointer is outside the module")
    intro_count = i32(terrain_relative + 0x08)
    intro_relative = u32(terrain_relative + 0x0C)
    if intro_count < 0 or intro_count > 0x10000:
        raise ValueError(f"April scene intro count is invalid: {intro_count}")
    if intro_count and (intro_relative == 0 or intro_relative + intro_count * 0x4C > module_size):
        raise ValueError("April scene intro table extends beyond the module")

    placements: list[dict[str, object]] = []
    for index in range(intro_count):
        relative = intro_relative + index * 0x4C
        raw_name = data[module_offset + relative : module_offset + relative + 16]
        name = raw_name.split(b"\0", 1)[0].decode("ascii", errors="strict").lower()
        if name not in requested:
            continue
        rotation_xyz = list(struct.unpack_from("<hhh", data, module_offset + relative + 0x18))
        x, y, z = struct.unpack_from("<hhh", data, module_offset + relative + 0x20)
        intronum = struct.unpack_from("<i", data, module_offset + relative + 0x10)[0]
        max_rad = struct.unpack_from("<h", data, module_offset + relative + 0x26)[0]
        max_rad_sq = struct.unpack_from("<i", data, module_offset + relative + 0x28)[0]
        flags = struct.unpack_from("<i", data, module_offset + relative + 0x2C)[0]
        placements.append(
            {
                "kind": "object",
                "asset": name,
                "name": name,
                "x": x,
                "y": y,
                "z": z,
                "rotation_xyz": rotation_xyz,
                "intronum": intronum,
                "max_rad": max_rad,
                "max_rad_sq": max_rad_sq,
                "flags": flags,
            }
        )
    if not placements:
        available = sorted(
            {
                data[module_offset + intro_relative + i * 0x4C : module_offset + intro_relative + i * 0x4C + 16]
                .split(b"\0", 1)[0]
                .decode("ascii", errors="replace")
                .lower()
                for i in range(intro_count)
            }
        )
        raise ValueError(f"no requested April objects found; available names: {', '.join(available)}")
    return placements


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--early", type=Path, required=True, help="April prototype scene")
    parser.add_argument("--source", type=Path, required=True, help="native PC scene to patch")
    parser.add_argument("--output", type=Path, required=True, help="new patched scene")
    parser.add_argument(
        "--name",
        dest="names",
        action="append",
        required=True,
        help="April runtime object name to import; repeat for multiple names",
    )
    args = parser.parse_args()

    requested = {name.strip().lower() for name in args.names if name.strip()}
    if not requested:
        parser.error("at least one non-empty --name is required")

    placements = _read_april_placements(args.early, requested)
    stage = args.output.with_name(args.output.stem + ".objectlist-stage.pcm")
    try:
        added_names = _append_object_names(args.source, sorted(requested), stage)
        assigned = _append_scene_placements(stage, placements, args.output)
    finally:
        if stage.exists():
            stage.unlink()

    print(f"Imported April placements: {len(assigned)}")
    print(f"Added object names: {', '.join(added_names) if added_names else 'none (already present)'}")
    print(f"Output: {args.output} ({args.output.stat().st_size:,} bytes)")
    for item in assigned:
        print(
            f"  {item['name']} id={item['identifier']} "
            f"pos=({item['x']}, {item['y']}, {item['z']}) "
            f"rot={item['rotation_xyz']} flags=0x{item['flags']:X}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
