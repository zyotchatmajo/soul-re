#!/usr/bin/env python3
"""Apply Recombobulator's FixCity10 geometry to a retail PC City 10 scene.

The FixCity11 branch repairs the three City 10 doorway/signal surfaces as a
single migration.  Patching only the portal records leaves the signal BSP and
portal polygons at their old coordinates, which is why a portal can still
appear badly offset in-game.  This tool applies the same serialized values to
an existing PC .DRM/.PCM without rebuilding the rest of the scene.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from level_editor import _module_offset, _parse_drm


PORTAL_SIZE = 0x5C

# Values copied from Recombobulator's FixCity10 migration.  The four vertices
# for each signal are the doorway polygons used by the runtime.
FIXED_VERTICES = {
    196: (-2547, 0, 224),
    197: (-4467, 0, 224),
    198: (-4467, 0, -1921),
    199: (-2547, 0, -1921),
    200: (-4467, -5120, 217),
    201: (-2547, -5120, 217),
    202: (-4467, -5120, -642),
    203: (-2547, -5120, -642),
    204: (-4467, -5120, -642),
    205: (-2547, -5120, -642),
    206: (-2552, -10880, -646),
    207: (-4472, -10880, -646),
}

# Portal records are (min xyz, max xyz, t1[3] xyz, t2[3] xyz).  Names,
# signal IDs, stream IDs, and the remaining serialized fields are preserved.
FIXED_PORTALS = {
    0: {
        "bounds": ((-4472, -10880, -646), (-2547, -5120, -642)),
        "t1": ((-4472, -10880, -646), (-4467, -5120, -642), (-2547, -5120, -642)),
        "t2": ((-2547, -5120, -642), (-2552, -10880, -646), (-4472, -10880, -646)),
    },
    1: {
        "bounds": ((-4467, -5120, -642), (-2547, -5120, 217)),
        "t1": ((-2547, -5120, -642), (-4467, -5120, -642), (-2547, -5120, 217)),
        "t2": ((-4467, -5120, 217), (-2547, -5120, 217), (-4467, -5120, -642)),
    },
    2: {
        "bounds": ((-4467, 0, -1921), (-2547, 0, 224)),
        "t1": ((-4467, 0, -1921), (-2547, 0, -1921), (-2547, 0, 224)),
        "t2": ((-2547, 0, 224), (-4467, 0, 224), (-4467, 0, -1921)),
    },
}

FIXED_SIG_LEAF = {
    "sphere": (-3496, -5359, -1261, 5646),
    "box": (-4472, -10880, -1921, -2547, 0, 224),
    "spectral_sphere": (-3496, -5359, -1261, 5646),
    "spectral_box": (-4472, -10880, -1921, -2547, 0, 224),
}


def _u32(data: bytearray, module: int, relative: int) -> int:
    return struct.unpack_from("<I", data, module + relative)[0]


def _i32(data: bytearray, module: int, relative: int) -> int:
    return struct.unpack_from("<i", data, module + relative)[0]


def apply_fix(source: Path, destination: Path) -> dict[str, object]:
    data = bytearray(source.read_bytes())
    module, _ = _module_offset(data)
    module_size = len(data) - module

    terrain = _u32(data, module, 0x00)
    vertex_count = _i32(data, module, terrain + 0x10)
    vertex_relative = _u32(data, module, terrain + 0x1C)
    if vertex_count < 208 or vertex_relative + 208 * 12 > module_size:
        raise ValueError("City 10 scene does not contain the expected vertex array")

    # Verify that this is the expected City 10 layout before changing it.
    name = data[module + _u32(data, module, 0x98) :].split(b"\0", 1)[0].decode("ascii", "replace").lower()
    if name != "city10":
        raise ValueError(f"expected world name city10, found {name!r}")

    for index, (x, y, z) in FIXED_VERTICES.items():
        struct.pack_into("<hhh", data, module + vertex_relative + index * 12, x, y, z)

    stream_relative = _u32(data, module, terrain + 0x30)
    count = _i32(data, module, stream_relative)
    if count < 3 or stream_relative + 4 + count * PORTAL_SIZE > module_size:
        raise ValueError("City 10 scene does not contain the expected portal table")

    for index, values in FIXED_PORTALS.items():
        record = module + stream_relative + 4 + index * PORTAL_SIZE
        name_bytes = bytes(data[record : record + 16]).split(b"\0", 1)[0].lower()
        expected = ("city9,2", "city9,3", "city11,1")[index].encode("ascii")
        if name_bytes != expected:
            raise ValueError(f"portal {index} is {name_bytes!r}, expected {expected!r}")
        struct.pack_into("<hhh", data, record + 0x18, *values["bounds"][0])
        struct.pack_into("<hhh", data, record + 0x20, *values["bounds"][1])
        for start, corners in ((0x2C, values["t1"]), (0x44, values["t2"])):
            for corner, xyz in enumerate(corners):
                struct.pack_into("<hhh", data, record + start + corner * 8, *xyz)

    # The signal BSP tree is the second BSP tree.  Its root is also the first
    # leaf, as in the retail City 10 layout.  A BSPLeaf is 0x34 bytes in the
    # PC format; sphere, box, and spectral sphere fields are all short-based.
    bsp_count = _i32(data, module, terrain + 0x44)
    bsp_array = _u32(data, module, terrain + 0x48)
    if bsp_count < 2 or bsp_array + 2 * 0x24 > module_size:
        raise ValueError("City 10 scene does not contain the expected BSP trees")
    sig_leaf = _u32(data, module, bsp_array + 1 * 0x24 + 0x00)
    sig_start = _u32(data, module, bsp_array + 1 * 0x24 + 0x04)
    sig_end = _u32(data, module, bsp_array + 1 * 0x24 + 0x08)
    if sig_leaf != sig_start or sig_leaf >= sig_end or sig_leaf + 0x34 > module_size:
        raise ValueError("City 10 signal BSP does not have the expected leaf layout")

    struct.pack_into("<hhhh", data, module + sig_leaf + 0x00, *FIXED_SIG_LEAF["sphere"])
    struct.pack_into("<hhhhhh", data, module + sig_leaf + 0x10, *FIXED_SIG_LEAF["box"])
    struct.pack_into("<hhhhhh", data, module + sig_leaf + 0x1C, *FIXED_SIG_LEAF["spectral_box"])
    struct.pack_into("<hhhh", data, module + sig_leaf + 0x28, *FIXED_SIG_LEAF["spectral_sphere"])

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    summary = _parse_drm(destination)
    return {
        "output": str(destination),
        "size": destination.stat().st_size,
        "vertices": len(FIXED_VERTICES),
        "portals": len(FIXED_PORTALS),
        "signal_leaf": hex(sig_leaf),
        "bounds": (summary.bounds_min, summary.bounds_max),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(apply_fix(args.source, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
