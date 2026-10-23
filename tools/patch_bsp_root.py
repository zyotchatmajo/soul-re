#!/usr/bin/env python3
"""Patch one native BSP root sphere to an April scene's coordinate frame."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from level_editor import _module_offset, _parse_drm, ensure_distinct_paths


def patch_root(
    source: Path,
    destination: Path,
    x: int,
    y: int,
    z: int,
    radius: int | None = None,
    tree_index: int = 0,
) -> dict[str, object]:
    ensure_distinct_paths(source, destination)
    data = bytearray(source.read_bytes())
    module, _ = _module_offset(data)
    terrain = module + struct.unpack_from("<I", data, module)[0]
    count = struct.unpack_from("<i", data, terrain + 0x44)[0]
    array = struct.unpack_from("<I", data, terrain + 0x48)[0]
    if tree_index < 0 or tree_index >= count:
        raise ValueError(f"BSP tree index {tree_index} is outside 0..{count - 1}")
    tree = module + array + tree_index * 0x24
    root = struct.unpack_from("<I", data, tree)[0]
    root_file = module + root
    old = struct.unpack_from("<hhhh", data, root_file)
    if radius is None:
        radius = old[3]
    if not -32768 <= radius <= 32767:
        raise ValueError("root radius is outside the serialized short range")
    struct.pack_into("<hhhh", data, root_file, x, y, z, radius)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    _parse_drm(destination)
    return {
        "tree": tree_index,
        "root_relative": hex(root),
        "old": old,
        "new": (x, y, z, radius),
        "output": str(destination),
        "size": destination.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--x", type=int, required=True)
    parser.add_argument("--y", type=int, required=True)
    parser.add_argument("--z", type=int, required=True)
    parser.add_argument("--radius", type=int)
    parser.add_argument("--tree", type=int, default=0)
    args = parser.parse_args()
    print(patch_root(args.source, args.output, args.x, args.y, args.z, args.radius, args.tree))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
