#!/usr/bin/env python3
"""Convert early prototype terrain geometry into a remaster-native scene.

The early prototype and the remaster use different Terrain layouts.  A direct
extension rename therefore makes the game read the wrong fields and can crash.
This tool uses a matching remaster scene as a native wrapper, then replaces its
vertex/face/normal arrays with the early scene's arrays and updates the native
Terrain pointers.  The original files are never modified.

This is intentionally a geometry-first conversion.  The wrapper keeps the
remaster's metadata, textures, BSP and event tables; those are not yet safely
portable between the two builds.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path


def module_offset(data: bytes) -> tuple[int, int]:
    if len(data) < 4:
        raise ValueError("scene is too small")
    reloc_count = struct.unpack_from("<I", data, 0)[0]
    table_end = 4 + (reloc_count + 1) * 4
    module = (table_end + 0x7FF) & ~0x7FF
    if module >= len(data):
        raise ValueError("relocation table points beyond scene")
    return module, reloc_count


def u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def i32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def align(value: int, boundary: int = 4) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


def checked_span(data: bytes, absolute: int, count: int, item_size: int, label: str) -> bytes:
    if count < 0:
        raise ValueError(f"{label} count is negative: {count}")
    end = absolute + count * item_size
    if absolute < 0 or end > len(data):
        raise ValueError(f"{label} array extends outside scene")
    return data[absolute:end]


def scene_info(path: Path, early: bool) -> dict[str, int | str]:
    data = path.read_bytes()
    module, reloc_count = module_offset(data)
    terrain_rel = u32(data, module)
    terrain = module + terrain_rel
    if terrain_rel >= len(data) - module:
        raise ValueError(f"{path}: terrain pointer is outside module")
    if early:
        count_v, count_f, count_n = 0x14, 0x18, 0x1C
        ptr_v, ptr_f, ptr_n = 0x20, 0x24, 0x28
    else:
        count_v, count_f, count_n = 0x10, 0x14, 0x18
        ptr_v, ptr_f, ptr_n = 0x1C, 0x20, 0x24
    return {
        "path": str(path),
        "size": len(data),
        "module_offset": module,
        "module_size": len(data) - module,
        "relocation_count": reloc_count,
        "terrain_relative": terrain_rel,
        "vertex_count": i32(data, terrain + count_v),
        "face_count": i32(data, terrain + count_f),
        "normal_count": i32(data, terrain + count_n),
        "vertex_relative": u32(data, terrain + ptr_v),
        "face_relative": u32(data, terrain + ptr_f),
        "normal_relative": u32(data, terrain + ptr_n),
    }


def convert(early_path: Path, template_path: Path, output_path: Path) -> dict[str, object]:
    early = early_path.read_bytes()
    template = bytearray(template_path.read_bytes())
    early_module, early_relocs = module_offset(early)
    template_module, template_relocs = module_offset(template)

    early_terrain_rel = u32(early, early_module)
    template_terrain_rel = u32(template, template_module)
    early_terrain = early_module + early_terrain_rel
    template_terrain = template_module + template_terrain_rel

    # The extracted prototype scenes in D:\\2 16 use the serialized Terrain
    # layout recognized by the existing viewer (counts at 0x10/0x14/0x18).
    # KAIN2.SYM from the retail disc describes a different early compiler
    # variant, so the file itself is the source of truth here.
    early_counts = {
        "vertices": i32(early, early_terrain + 0x10),
        "faces": i32(early, early_terrain + 0x14),
        "normals": i32(early, early_terrain + 0x18),
    }
    early_ptrs = {
        "vertices": u32(early, early_terrain + 0x1C),
        "faces": u32(early, early_terrain + 0x20),
        "normals": u32(early, early_terrain + 0x24),
    }
    # These are the stable serialized sizes of TVertex, TFace and Normal.
    arrays = {
        "vertices": checked_span(early, early_module + early_ptrs["vertices"], early_counts["vertices"], 0x0C, "early vertex"),
        "faces": checked_span(early, early_module + early_ptrs["faces"], early_counts["faces"], 0x0C, "early face"),
        "normals": checked_span(early, early_module + early_ptrs["normals"], early_counts["normals"], 0x08, "early normal"),
    }

    # Append aligned arrays to the existing native module.  The wrapper's
    # relocation entries already include the three native Terrain pointer
    # fields that we update below.
    appended: dict[str, int] = {}
    for name in ("vertices", "faces", "normals"):
        rel = align(len(template) - template_module, 4)
        absolute = template_module + rel
        if len(template) < absolute:
            template.extend(b"\0" * (absolute - len(template)))
        appended[name] = rel
        template.extend(arrays[name])

    # The native BSP leaves contain their own face-list pointers.  Redirect
    # those pointers when they point into the wrapper's original face array;
    # otherwise collision/render traversal would still index the discarded
    # native faces while Terrain.faceList points at the prototype faces.
    old_face_rel = u32(template, template_terrain + 0x20)
    old_face_count = i32(template, template_terrain + 0x14)
    bsp_count = i32(template, template_terrain + 0x44)
    bsp_array_rel = u32(template, template_terrain + 0x48)
    patched_leaf_faces = 0
    if bsp_count >= 0 and bsp_count < 0x10000 and bsp_array_rel:
        old_face_end = old_face_rel + old_face_count * 0x0C
        for tree_index in range(bsp_count):
            tree = template_module + bsp_array_rel + tree_index * 0x24
            if tree + 0x0C > len(template):
                break
            start_leaves = u32(template, tree + 0x04)
            end_leaves = u32(template, tree + 0x08)
            if not (start_leaves <= end_leaves and end_leaves - start_leaves <= 0x100000):
                continue
            leaf_rel = start_leaves
            while leaf_rel < end_leaves:
                leaf = template_module + leaf_rel
                if leaf + 0x0E > len(template):
                    break
                face_rel = u32(template, leaf + 0x08)
                if old_face_rel <= face_rel < old_face_end:
                    delta = face_rel - old_face_rel
                    struct.pack_into("<I", template, leaf + 0x08, appended["faces"] + delta)
                    patched_leaf_faces += 1
                leaf_rel += 0x30

    # Native remaster Terrain fields: counts at 0x10/0x14/0x18 and pointers at
    # 0x1c/0x20/0x24.  Keep its BSP/texture/event structures coherent with the
    # wrapper; the geometry arrays are the only transplanted data in this pass.
    for field, offset in (("vertices", 0x10), ("faces", 0x14), ("normals", 0x18)):
        struct.pack_into("<i", template, template_terrain + offset, early_counts[field])
    for field, offset in (("vertices", 0x1C), ("faces", 0x20), ("normals", 0x24)):
        struct.pack_into("<I", template, template_terrain + offset, appended[field])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(template)
    return {
        "input_early": str(early_path),
        "input_template": str(template_path),
        "output": str(output_path),
        "early_module_offset": early_module,
        "template_module_offset": template_module,
        "early_relocation_count": early_relocs,
        "template_relocation_count": template_relocs,
        "early_terrain_relative": early_terrain_rel,
        "template_terrain_relative": template_terrain_rel,
        "counts": early_counts,
        "appended_relative": appended,
        "output_size": len(template),
        "patched_bsp_leaf_face_pointers": patched_leaf_faces,
        "mode": "geometry-wrapper",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--early", type=Path, required=True, help="early prototype .pcm/.drm scene")
    parser.add_argument("--template", type=Path, required=True, help="matching remaster-native .drm scene")
    parser.add_argument("--output", type=Path, required=True, help="new converted scene")
    args = parser.parse_args()
    for path in (args.early, args.template):
        if not path.is_file():
            parser.error(f"file not found: {path}")
    result = convert(args.early, args.template, args.output)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
