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


def cap_geometry(
    early_arrays: dict[str, bytes],
    early_counts: dict[str, int],
    template: bytes,
    template_module: int,
    template_terrain: int,
) -> tuple[dict[str, bytes], dict[str, int]]:
    """Keep early geometry coherent while respecting native array capacities."""
    native_counts = {
        "vertices": i32(template, template_terrain + 0x10),
        "faces": i32(template, template_terrain + 0x14),
        "normals": i32(template, template_terrain + 0x18),
    }
    native_vertex_rel = u32(template, template_terrain + 0x1C)
    native_normal_rel = u32(template, template_terrain + 0x24)
    native_vertices = checked_span(
        template, template_module + native_vertex_rel, native_counts["vertices"], 0x0C, "template vertex"
    )
    native_normals = checked_span(
        template, template_module + native_normal_rel, native_counts["normals"], 0x08, "template normal"
    )

    # Preserve the prototype's triangle-to-vertex relationship.  Discard only
    # triangles that reference vertices above the native capacity, then repeat
    # valid triangles if the native face budget is larger than the survivors.
    valid_faces: list[bytes] = []
    face_blob = early_arrays["faces"]
    for index in range(early_counts["faces"]):
        record = face_blob[index * 0x0C : (index + 1) * 0x0C]
        v0, v1, v2 = struct.unpack_from("<HHH", record)
        if v0 < native_counts["vertices"] and v1 < native_counts["vertices"] and v2 < native_counts["vertices"]:
            valid_faces.append(record)
    if not valid_faces:
        raise ValueError("early scene has no faces within native vertex capacity")
    selected_faces = (valid_faces * ((native_counts["faces"] + len(valid_faces) - 1) // len(valid_faces)))[: native_counts["faces"]]

    vertex_bytes = bytearray(native_vertices)
    vertex_bytes[: min(len(vertex_bytes), len(early_arrays["vertices"]))] = early_arrays["vertices"][: len(vertex_bytes)]
    normal_bytes = bytearray(native_normals)
    normal_bytes[: min(len(normal_bytes), len(early_arrays["normals"]))] = early_arrays["normals"][: len(normal_bytes)]
    return {
        "vertices": bytes(vertex_bytes),
        "faces": b"".join(selected_faces),
        "normals": bytes(normal_bytes),
    }, native_counts


def remap_face_material_offsets(
    face_blob: bytes,
    template_texture_bytes: int,
) -> tuple[bytes, int]:
    """Keep converted TFace texture offsets inside the wrapper texture list.

    TFace.textoff is a byte offset from Terrain.StartTextureList.  Prototype
    scenes can contain a much larger texture table than the native wrapper;
    retaining those offsets makes the remaster dereference past the wrapper's
    serialized texture records during material setup.  Map by texture index so
    offsets stay aligned to the 12-byte TextureFT3 record size.
    """
    if len(face_blob) % 0x0C:
        raise ValueError("face blob is not aligned to TFace records")
    texture_count = template_texture_bytes // 0x0C
    if texture_count <= 0:
        raise ValueError("template has no serialized texture records")
    result = bytearray(face_blob)
    changed = 0
    for offset in range(0, len(result), 0x0C):
        textoff = struct.unpack_from("<H", result, offset + 0x0A)[0]
        if textoff == 0xFFFF:
            continue
        texture_index = textoff // 0x0C
        replacement = (texture_index % texture_count) * 0x0C
        if replacement != textoff:
            struct.pack_into("<H", result, offset + 0x0A, replacement)
            changed += 1
    return bytes(result), changed


def build_morph_normal_indices(face_blob: bytes) -> bytes:
    """Create a no-op morph-normal backup array for the converted faces."""
    if len(face_blob) % 0x0C:
        raise ValueError("face blob is not aligned to TFace records")
    result = bytearray((len(face_blob) // 0x0C) * 2)
    for index, offset in enumerate(range(0, len(face_blob), 0x0C)):
        struct.pack_into("<h", result, index * 2, struct.unpack_from("<h", face_blob, offset + 0x08)[0])
    return bytes(result)


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


def copy_early_bsp(
    template: bytearray,
    early: bytes,
    early_module: int,
    early_terrain: int,
    template_module: int,
    early_face_rel: int,
    early_face_count: int,
    new_face_rel: int,
) -> tuple[int, int, int, list[int]]:
    """Append the prototype BSP graph and retarget its serialized pointers.

    Prototype Terrain has two extra fields before ``numBSPTrees`` (the count
    is at +0x4c, array at +0x50), while the remaster has those fields at
    +0x44/+0x48.  The node/leaf graph itself uses the native 0x2c/0x30/0x24
    record sizes, so it can be copied as one contiguous block once all graph
    pointers and face-list pointers are translated.
    """
    count = i32(early, early_terrain + 0x4C)
    array_rel = u32(early, early_terrain + 0x50)
    if count <= 0 or count > 0x1000 or not array_rel:
        raise ValueError("early scene has no recognizable BSP tree array")

    trees: list[tuple[int, int, int]] = []
    for index in range(count):
        tree = early_module + array_rel + index * 0x24
        if tree + 0x24 > len(early):
            raise ValueError("early BSP tree array extends outside scene")
        root, start_leaves, end_leaves = struct.unpack_from("<III", early, tree)
        if not (root <= start_leaves <= end_leaves):
            raise ValueError("early BSP tree ranges are invalid")
        trees.append((root, start_leaves, end_leaves))

    old_start = min(array_rel, *(root for root, _, _ in trees))
    old_end = max(array_rel + count * 0x24, *(end for _, _, end in trees))
    if old_start >= old_end or early_module + old_end > len(early):
        raise ValueError("early BSP graph extends outside scene")

    new_rel = align(len(template) - template_module, 4)
    absolute = template_module + new_rel
    if len(template) < absolute:
        template.extend(b"\0" * (absolute - len(template)))
    template.extend(early[early_module + old_start : early_module + old_end])

    def translate(value: int) -> int:
        if old_start <= value < old_end:
            return new_rel + (value - old_start)
        return value

    extra_relocs: list[int] = []
    # Tree graph pointers: root, leaf ranges, and optional instance spline.
    tree_copy_base = new_rel + (array_rel - old_start)
    for index in range(count):
        tree = template_module + tree_copy_base + index * 0x24
        for field in (0x00, 0x04, 0x08, 0x20):
            value = u32(template, tree + field)
            if value:
                struct.pack_into("<I", template, tree + field, translate(value))
                extra_relocs.append(tree_copy_base + index * 0x24 + field)

    patched_faces = 0
    face_end = early_face_rel + early_face_count * 0x0C
    for root, start_leaves, end_leaves in trees:
        node_rel = root
        while node_rel < start_leaves:
            node = template_module + new_rel + (node_rel - old_start)
            for field in (0x14, 0x18):
                value = u32(template, node + field)
                if value:
                    struct.pack_into("<I", template, node + field, translate(value))
                    extra_relocs.append(new_rel + (node_rel - old_start) + field)
            node_rel += 0x2C
        leaf_rel = start_leaves
        while leaf_rel < end_leaves:
            leaf = template_module + new_rel + (leaf_rel - old_start)
            face_rel = u32(template, leaf + 0x08)
            if early_face_rel <= face_rel < face_end:
                struct.pack_into("<I", template, leaf + 0x08, new_face_rel + (face_rel - early_face_rel))
                patched_faces += 1
                extra_relocs.append(new_rel + (leaf_rel - old_start) + 0x08)
            leaf_rel += 0x30

    return new_rel, count, patched_faces, extra_relocs


def rebuild_relocation_table(data: bytearray, module_offset: int, extra: list[int]) -> bytearray:
    """Prepend an expanded relocation table while preserving module bytes."""
    if not extra:
        return data
    old_count = u32(data, 0)
    existing = [u32(data, 4 + index * 4) for index in range(old_count)]
    entries = existing + sorted(set(extra))
    table_words = ((len(entries) + 512) // 512) * 512
    new_module_offset = table_words * 4
    module = bytes(data[module_offset:])
    header = bytearray(new_module_offset)
    struct.pack_into("<I", header, 0, len(entries))
    for index, value in enumerate(entries):
        struct.pack_into("<I", header, 4 + index * 4, value)
    return header + module


def rebalance_native_bsp(
    template: bytearray,
    template_module: int,
    template_terrain: int,
    face_relative: int,
    face_count: int,
    vertices: bytes,
) -> int:
    """Keep the PC BSP graph but distribute the replacement faces across it."""
    count = i32(template, template_terrain + 0x44)
    array_rel = u32(template, template_terrain + 0x48)
    if count <= 0 or count > 0x1000 or not array_rel:
        raise ValueError("template has no recognizable BSP tree array")
    points = [struct.unpack_from("<hhh", vertices, index) for index in range(0, len(vertices), 0x0C)]
    mins = tuple(min(point[axis] for point in points) for axis in range(3))
    maxs = tuple(max(point[axis] for point in points) for axis in range(3))
    center = tuple((mins[axis] + maxs[axis]) // 2 for axis in range(3))
    box = (*mins, *maxs)
    leaves: list[int] = []
    nodes: list[int] = []
    for tree_index in range(count):
        tree = template_module + array_rel + tree_index * 0x24
        root, start_leaves, end_leaves = struct.unpack_from("<III", template, tree)
        node_rel = root
        while node_rel < start_leaves:
            nodes.append(node_rel)
            node_rel += 0x2C
        leaf_rel = start_leaves
        while leaf_rel < end_leaves:
            leaves.append(leaf_rel)
            leaf_rel += 0x30
    if not leaves:
        raise ValueError("template BSP contains no leaves")

    base, remainder = divmod(face_count, len(leaves))
    cursor = 0
    for index, leaf_rel in enumerate(leaves):
        leaf = template_module + leaf_rel
        amount = base + (1 if index < remainder else 0)
        struct.pack_into("<I", template, leaf + 0x08, face_relative + cursor * 0x0C)
        struct.pack_into("<h", template, leaf + 0x0C, amount)
        struct.pack_into("<hhhhhh", template, leaf + 0x10, *box)
        struct.pack_into("<hhhhhh", template, leaf + 0x1C, *box)
        struct.pack_into("<hhhH", template, leaf, *center, 0xFFFF)
        struct.pack_into("<hhhH", template, leaf + 0x28, *center, 0xFFFF)
        cursor += amount
    for node_rel in nodes:
        node = template_module + node_rel
        struct.pack_into("<hhhH", template, node, *center, 0xFFFF)
        struct.pack_into("<hhhH", template, node + 0x1C, *center, 0xFFFF)
    return len(leaves)


def convert(
    early_path: Path,
    template_path: Path,
    output_path: Path,
    preserve_template_topology: bool = False,
    cap_to_native: bool = False,
    in_place_native: bool = False,
    world_name: str | None = None,
    stream_unit_id: int | None = None,
    copy_early_bsp_graph: bool = False,
    rebalance_bsp: bool = False,
    sanitize_materials: bool = False,
) -> dict[str, object]:
    early = early_path.read_bytes()
    template = bytearray(template_path.read_bytes())
    early_module, early_relocs = module_offset(early)
    template_module, template_relocs = module_offset(template)

    early_terrain_rel = u32(early, early_module)
    template_terrain_rel = u32(template, template_module)
    early_terrain = early_module + early_terrain_rel
    template_terrain = template_module + template_terrain_rel

    if world_name is not None:
        if not world_name or "\0" in world_name or len(world_name) > 15:
            raise ValueError("--world-name must be 1-15 characters without NUL")
        world_relative = u32(template, template_module + 0x98)
        world_start = template_module + world_relative
        old_end = template.find(b"\0", world_start)
        if world_relative == 0 or old_end < world_start:
            raise ValueError("template has no writable Level.worldName string")
        capacity = old_end - world_start
        if len(world_name) > capacity:
            raise ValueError("replacement world name is longer than template storage")
        template[world_start : old_end + 1] = world_name.encode("ascii") + b"\0" * (capacity - len(world_name) + 1)
    if stream_unit_id is not None:
        struct.pack_into("<i", template, template_module + 0xF8, int(stream_unit_id))

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
    remapped_materials = 0
    if sanitize_materials:
        native_texture_start = u32(template, template_terrain + 0x34)
        native_texture_end = u32(template, template_terrain + 0x38)
        if native_texture_end <= native_texture_start:
            raise ValueError("template has no valid texture range")
        arrays["faces"], remapped_materials = remap_face_material_offsets(
            arrays["faces"], native_texture_end - native_texture_start
        )

    output_counts = dict(early_counts)
    if cap_to_native:
        arrays, output_counts = cap_geometry(
            arrays, early_counts, template, template_module, template_terrain
        )
    if preserve_template_topology:
        # Keep the remaster's fixed face/normal topology and BSP partitioning.
        # Only replace the first native vertex-count positions with prototype
        # positions; this isolates count/face-layout incompatibilities.
        native_counts = {
            "vertices": i32(template, template_terrain + 0x10),
            "faces": i32(template, template_terrain + 0x14),
            "normals": i32(template, template_terrain + 0x18),
        }
        native_vertex_rel = u32(template, template_terrain + 0x1C)
        native_face_rel = u32(template, template_terrain + 0x20)
        native_normal_rel = u32(template, template_terrain + 0x24)
        native_vertices = checked_span(
            template, template_module + native_vertex_rel, native_counts["vertices"], 0x0C, "template vertex"
        )
        native_faces = checked_span(
            template, template_module + native_face_rel, native_counts["faces"], 0x0C, "template face"
        )
        native_normals = checked_span(
            template, template_module + native_normal_rel, native_counts["normals"], 0x08, "template normal"
        )
        vertex_bytes = bytearray(native_vertices)
        copy_size = min(len(vertex_bytes), len(arrays["vertices"]))
        vertex_bytes[:copy_size] = arrays["vertices"][:copy_size]
        arrays = {
            "vertices": bytes(vertex_bytes),
            "faces": native_faces,
            "normals": native_normals,
        }
        output_counts = native_counts

    if in_place_native:
        if not cap_to_native:
            raise ValueError("--in-place-native requires --cap-to-native")
        # Keep the native relocation table, pointers, BSP topology, and file
        # length completely unchanged.  This matters for the console loader:
        # its relocation pass compacts the module in-place, so a replacement
        # array is safer than adding new pointer targets and redirecting the
        # serialized Terrain fields.
        native_vertex_rel = u32(template, template_terrain + 0x1C)
        native_face_rel = u32(template, template_terrain + 0x20)
        native_normal_rel = u32(template, template_terrain + 0x24)
        for name, relative in (
            ("vertices", native_vertex_rel),
            ("faces", native_face_rel),
            ("normals", native_normal_rel),
        ):
            blob = arrays[name]
            start = template_module + relative
            end = start + len(blob)
            if start < template_module or end > len(template):
                raise ValueError(f"native {name} array is outside template")
            template[start:end] = blob
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
            "counts": output_counts,
            "mode": "in-place-native",
            "output_size": len(template),
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

    native_counts = {
        "vertices": i32(template, template_terrain + 0x10),
        "faces": i32(template, template_terrain + 0x14),
        "normals": i32(template, template_terrain + 0x18),
    }
    resized_morph_data = output_counts != native_counts
    if resized_morph_data:
        # The native morph-color and morph-delta lists are sized for the
        # wrapper's original arrays.  Leaving them attached makes spectral
        # updates walk past the converted geometry.  A face-normal backup is
        # still required by MORPH_UpdateNormals, so provide a same-value,
        # no-op list sized to the replacement face array.
        struct.pack_into("<I", template, template_terrain + 0x3C, 0)
        struct.pack_into("<I", template, template_terrain + 0x40, 0)
        morph_normals = build_morph_normal_indices(arrays["faces"])
        rel = align(len(template) - template_module, 4)
        absolute = template_module + rel
        if len(template) < absolute:
            template.extend(b"\0" * (absolute - len(template)))
        appended["morph_normals"] = rel
        template.extend(morph_normals)
        struct.pack_into("<I", template, template_terrain + 0x4C, rel)

    early_bsp: tuple[int, int, int, list[int]] | None = None
    if copy_early_bsp_graph:
        early_bsp = copy_early_bsp(
            template,
            early,
            early_module,
            early_terrain,
            template_module,
            early_ptrs["faces"],
            early_counts["faces"],
            appended["faces"],
        )

    # The native BSP leaves contain their own face-list pointers.  Redirect
    # those pointers when they point into the wrapper's original face array;
    # otherwise collision/render traversal would still index the discarded
    # native faces while Terrain.faceList points at the prototype faces.
    old_face_rel = u32(template, template_terrain + 0x20)
    old_face_count = i32(template, template_terrain + 0x14)
    bsp_count = i32(template, template_terrain + 0x44)
    bsp_array_rel = u32(template, template_terrain + 0x48)
    patched_leaf_faces = 0
    if not copy_early_bsp_graph and bsp_count >= 0 and bsp_count < 0x10000 and bsp_array_rel:
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

    if early_bsp is not None:
        bsp_rel, bsp_count, patched_early_faces, extra_relocs = early_bsp
        struct.pack_into("<i", template, template_terrain + 0x44, bsp_count)
        struct.pack_into("<I", template, template_terrain + 0x48, bsp_rel)
    if rebalance_bsp:
        if copy_early_bsp_graph:
            raise ValueError("--rebalance-native-bsp cannot be combined with --copy-early-bsp")
        rebalance_native_bsp(
            template,
            template_module,
            template_terrain,
            appended["faces"],
            output_counts["faces"],
            arrays["vertices"],
        )

    # Native remaster Terrain fields: counts at 0x10/0x14/0x18 and pointers at
    # 0x1c/0x20/0x24.  Keep its BSP/texture/event structures coherent with the
    # wrapper; the geometry arrays are the only transplanted data in this pass.
    for field, offset in (("vertices", 0x10), ("faces", 0x14), ("normals", 0x18)):
        struct.pack_into("<i", template, template_terrain + offset, output_counts[field])
    for field, offset in (("vertices", 0x1C), ("faces", 0x20), ("normals", 0x24)):
        struct.pack_into("<I", template, template_terrain + offset, appended[field])

    if early_bsp is not None:
        template = rebuild_relocation_table(template, template_module, extra_relocs)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(template)
    return {
        "input_early": str(early_path),
        "input_template": str(template_path),
        "output": str(output_path),
        "early_module_offset": early_module,
        "template_module_offset": module_offset(template)[0],
        "early_relocation_count": early_relocs,
        "template_relocation_count": template_relocs,
        "early_terrain_relative": early_terrain_rel,
        "template_terrain_relative": template_terrain_rel,
        "counts": output_counts,
        "appended_relative": appended,
        "output_size": len(template),
        "patched_bsp_leaf_face_pointers": patched_leaf_faces,
        "mode": "topology-preserving" if preserve_template_topology else "geometry-wrapper",
        "copied_early_bsp_graph": copy_early_bsp_graph,
        "patched_early_bsp_leaf_face_pointers": patched_early_faces if early_bsp is not None else 0,
        "world_name": world_name,
        "stream_unit_id": stream_unit_id,
        "remapped_material_offsets": remapped_materials,
        "resized_morph_data": resized_morph_data,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--early", type=Path, required=True, help="early prototype .pcm/.drm scene")
    parser.add_argument("--template", type=Path, required=True, help="matching remaster-native .drm scene")
    parser.add_argument("--output", type=Path, required=True, help="new converted scene")
    parser.add_argument(
        "--preserve-template-topology",
        action="store_true",
        help="keep native face/normal counts and BSP topology; replace vertex positions only",
    )
    parser.add_argument(
        "--cap-to-native",
        action="store_true",
        help="keep prototype triangle relationships while capping arrays to native remaster counts",
    )
    parser.add_argument(
        "--in-place-native",
        action="store_true",
        help="overwrite native arrays without changing serialized pointers or file size (requires --cap-to-native)",
    )
    parser.add_argument("--world-name", help="replace the template Level.worldName string")
    parser.add_argument("--stream-unit-id", type=int, help="replace the template Level.streamUnitID")
    parser.add_argument(
        "--copy-early-bsp",
        action="store_true",
        help="copy the prototype BSP node/leaf graph and retarget its face lists",
    )
    parser.add_argument(
        "--rebalance-native-bsp",
        action="store_true",
        help="keep the native BSP graph but distribute converted faces and widen its bounds",
    )
    parser.add_argument(
        "--sanitize-materials",
        action="store_true",
        help="remap prototype TFace texture offsets into the wrapper texture table",
    )
    args = parser.parse_args()
    for path in (args.early, args.template):
        if not path.is_file():
            parser.error(f"file not found: {path}")
    result = convert(
        args.early,
        args.template,
        args.output,
        preserve_template_topology=args.preserve_template_topology,
        cap_to_native=args.cap_to_native,
        in_place_native=args.in_place_native,
        world_name=args.world_name,
        stream_unit_id=args.stream_unit_id,
        copy_early_bsp_graph=args.copy_early_bsp,
        rebalance_bsp=args.rebalance_native_bsp,
        sanitize_materials=args.sanitize_materials,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
