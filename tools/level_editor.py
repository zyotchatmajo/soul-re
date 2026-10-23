#!/usr/bin/env python3
"""Read-only Soul Reaver level asset browser.

This is the first level-editor milestone.  It deliberately does not write
game assets: the extracted BIGFILE data contains pointer-rich PSX structures,
so inspection comes before mutation.  The same inventory model will be used
by the geometry/editor work that follows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from itertools import permutations
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional


SUPPORTED_EXTENSIONS = {".drm", ".pcm", ".crm", ".smf", ".snf", ".tim", ".smp", ".snd"}
DUPLICATE_RE = re.compile(r"_duplicate\d+$", re.IGNORECASE)
PLACEMENT_ID_MIN = 0x70000000


def ensure_distinct_paths(source: Path, destination: Path) -> None:
    """Reject an in-place binary edit unless a caller explicitly owns it."""
    if source.resolve(strict=False) == destination.resolve(strict=False):
        raise ValueError("source and output paths must be different; write a staged copy instead")


@dataclass(frozen=True)
class Asset:
    area: str
    name: str
    stem: str
    extension: str
    size: int
    relative_path: str
    header: str
    sha1: str


@dataclass(frozen=True)
class DrmSummary:
    path: str
    module_offset: int
    module_size: int
    relocation_count: int
    world_name: str
    stream_unit_id: int
    vertex_count: int
    face_count: int
    normal_count: int
    intro_count: int
    bsp_tree_count: int
    plan_marker_count: int
    sfx_marker_count: int
    bounds_min: tuple[int, int, int]
    bounds_max: tuple[int, int, int]


@dataclass(frozen=True)
class SceneMarker:
    kind: str
    index: int
    name: str
    identifier: int
    x: int
    y: int
    z: int
    flags: int


def _strip_duplicate(stem: str) -> str:
    return DUPLICATE_RE.sub("", stem)


def _header(path: Path, length: int = 16) -> str:
    with path.open("rb") as stream:
        return stream.read(length).hex(" ").upper()


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def discover(root: Path, area: Optional[str] = None) -> list[Asset]:
    """Discover extracted area assets without interpreting their payloads."""
    if not root.is_dir():
        raise FileNotFoundError(f"asset root does not exist: {root}")

    assets: list[Asset] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        # Expected layout is <area>/bin/<asset>.<ext>.  Keep a fallback for
        # future extracted folders so the browser remains useful.
        discovered_area = parts[0] if parts else "<root>"
        if area and discovered_area.lower() != area.lower():
            continue
        stem = _strip_duplicate(path.stem)
        assets.append(
            Asset(
                area=discovered_area,
                name=path.name,
                stem=stem,
                extension=path.suffix.lower(),
                size=path.stat().st_size,
                relative_path=relative.as_posix(),
                header=_header(path),
                sha1=_sha1(path),
            )
        )
    return assets


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{size} B"
        value /= 1024
    return f"{size} B"


def print_report(assets: Iterable[Asset], root: Path) -> None:
    assets = list(assets)
    by_area: dict[str, list[Asset]] = defaultdict(list)
    for asset in assets:
        by_area[asset.area].append(asset)

    print(f"Asset root: {root}")
    print(f"Assets: {len(assets)}")
    print()
    for area in sorted(by_area):
        area_assets = by_area[area]
        by_extension: dict[str, int] = defaultdict(int)
        for asset in area_assets:
            by_extension[asset.extension] += 1
        ext_summary = ", ".join(
            f"{extension}={count}" for extension, count in sorted(by_extension.items())
        )
        bundles = len({asset.stem for asset in area_assets})
        total_size = sum(asset.size for asset in area_assets)
        print(
            f"{area:12} bundles={bundles:4} files={len(area_assets):5} "
            f"size={_human_size(total_size):>10}  {ext_summary}"
        )


def write_manifest(assets: Iterable[Asset], destination: Path) -> None:
    payload = {
        "format": "soul-re-level-inventory-v1",
        "assets": [asdict(asset) for asset in assets],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _read_preview(path: Path, length: int = 64) -> str:
    with path.open("rb") as stream:
        data = stream.read(length)
    return " ".join(f"{byte:02X}" for byte in data)


def _module_offset(data: bytes) -> tuple[int, int]:
    """Return the serialized module offset and relocation-table entry count."""
    if len(data) < 4:
        raise ValueError("DRM file is too small")
    relocation_count = struct.unpack_from("<I", data, 0)[0]
    # The first word is the relocation-entry count; the relocation table has
    # exactly ``relocation_count`` entries following it.  Adding one here
    # shifts files whose table ends exactly on an 0x800 boundary into the
    # next block (for example the glyph-reward area STONE11, which has 0x3FF
    # entries and a module at 0x1000 instead of 0x1800).
    table_end = 4 + relocation_count * 4
    module_offset = (table_end + 0x7FF) & ~0x7FF
    if module_offset >= len(data):
        raise ValueError("DRM relocation table points past end of file")
    return module_offset, relocation_count


def _parse_drm(path: Path) -> DrmSummary:
    """Parse the stable Level/Terrain header and geometry arrays from a DRM."""
    data = path.read_bytes()
    module_offset, relocation_count = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(offset: int) -> int:
        return struct.unpack_from("<I", data, module_offset + offset)[0]

    def i32(offset: int) -> int:
        return struct.unpack_from("<i", data, module_offset + offset)[0]

    def ptr(offset: int) -> int:
        value = u32(offset)
        if value >= module_size:
            raise ValueError(f"pointer 0x{value:X} at Level+0x{offset:X} is outside DRM module")
        return value

    def read_c_string(relative: int) -> str:
        if relative == 0:
            return ""
        end = data.find(b"\0", module_offset + relative)
        if end < 0:
            end = len(data)
        return data[module_offset + relative:end].decode("ascii", errors="replace")

    terrain_relative = ptr(0x00)
    terrain = module_offset + terrain_relative
    num_intros = i32(terrain_relative + 0x08)
    num_vertices = i32(terrain_relative + 0x10)
    num_faces = i32(terrain_relative + 0x14)
    num_normals = i32(terrain_relative + 0x18)
    vertex_relative = struct.unpack_from("<I", data, terrain + 0x1C)[0]
    face_relative = struct.unpack_from("<I", data, terrain + 0x20)[0]
    bsp_tree_count = i32(terrain_relative + 0x44)

    if num_vertices < 0 or num_faces < 0 or num_normals < 0:
        raise ValueError("DRM contains a negative geometry count")
    if num_vertices and vertex_relative + num_vertices * 12 > module_size:
        raise ValueError("vertex array extends beyond DRM module")
    if num_faces and face_relative + num_faces * 12 > module_size:
        raise ValueError("face array extends beyond DRM module")

    bounds_min = [32767, 32767, 32767]
    bounds_max = [-32768, -32768, -32768]
    for index in range(num_vertices):
        x, y, z = struct.unpack_from("<hhh", data, module_offset + vertex_relative + index * 12)
        for axis, value in enumerate((x, y, z)):
            bounds_min[axis] = min(bounds_min[axis], value)
            bounds_max[axis] = max(bounds_max[axis], value)
    if not num_vertices:
        bounds_min = [0, 0, 0]
        bounds_max = [0, 0, 0]

    return DrmSummary(
        path=str(path),
        module_offset=module_offset,
        module_size=module_size,
        relocation_count=relocation_count,
        world_name=read_c_string(u32(0x98)),
        stream_unit_id=i32(0xF8),
        vertex_count=num_vertices,
        face_count=num_faces,
        normal_count=num_normals,
        intro_count=num_intros,
        bsp_tree_count=bsp_tree_count,
        plan_marker_count=i32(0xE0),
        sfx_marker_count=i32(0xE8),
        bounds_min=tuple(bounds_min),
        bounds_max=tuple(bounds_max),
    )


def _parse_scene_markers(path: Path) -> list[SceneMarker]:
    """Read the relocatable Intro, plan-marker, and SFX-marker arrays."""
    data = path.read_bytes()
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(relative: int) -> int:
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    def i32(relative: int) -> int:
        return struct.unpack_from("<i", data, module_offset + relative)[0]

    def i16(relative: int) -> int:
        return struct.unpack_from("<h", data, module_offset + relative)[0]

    def checked_array(relative: int, count: int, item_size: int, label: str) -> int:
        if count < 0:
            raise ValueError(f"{label} count is negative")
        if not count:
            return 0
        if relative == 0 or relative + count * item_size > module_size:
            raise ValueError(f"{label} array extends beyond DRM module")
        return relative

    terrain_relative = u32(0)
    if terrain_relative >= module_size:
        raise ValueError("terrain pointer is outside DRM module")
    terrain_file = module_offset + terrain_relative
    intro_count = i32(terrain_relative + 0x08)
    intro_relative = u32(terrain_relative + 0x0C)
    plan_count = i32(0xE0)
    plan_relative = u32(0xE4)
    sfx_count = i32(0xE8)
    sfx_relative = u32(0xEC)
    checked_array(intro_relative, intro_count, 0x4C, "intro")
    checked_array(plan_relative, plan_count, 0x08, "plan marker")
    checked_array(sfx_relative, sfx_count, 0x24, "SFX marker")

    markers: list[SceneMarker] = []
    for index in range(intro_count):
        relative = intro_relative + index * 0x4C
        raw_name = data[module_offset + relative : module_offset + relative + 16]
        name = raw_name.split(b"\0", 1)[0].decode("ascii", errors="replace")
        markers.append(
            SceneMarker(
                kind="intro",
                index=index,
                name=name or f"intro_{index}",
                identifier=i32(relative + 0x14),
                x=i16(relative + 0x20),
                y=i16(relative + 0x22),
                z=i16(relative + 0x24),
                flags=i32(relative + 0x2C),
            )
        )
    for index in range(plan_count):
        relative = plan_relative + index * 0x08
        markers.append(
            SceneMarker(
                kind="plan",
                index=index,
                name=f"plan_{index}",
                identifier=i16(relative + 0x06),
                x=i16(relative),
                y=i16(relative + 0x02),
                z=i16(relative + 0x04),
                flags=0,
            )
        )
    for index in range(sfx_count):
        relative = sfx_relative + index * 0x24
        markers.append(
            SceneMarker(
                kind="sfx",
                index=index,
                name=f"sfx_{index}",
                identifier=i32(relative + 0x04),
                x=i16(relative + 0x14),
                y=i16(relative + 0x16),
                z=i16(relative + 0x18),
                flags=i32(relative + 0x1C),
            )
        )
    return markers


def _parse_scene_objects(path: Path) -> list[dict[str, object]]:
    """Return editable object views for every serialized intro record."""
    data = path.read_bytes()
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("scene field is outside the module")
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    def i32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("scene field is outside the module")
        return struct.unpack_from("<i", data, module_offset + relative)[0]

    terrain_relative = u32(0)
    if terrain_relative >= module_size or terrain_relative + 0x10 > module_size:
        raise ValueError("terrain pointer is outside the scene module")
    count = i32(terrain_relative + 0x08)
    intro_relative = u32(terrain_relative + 0x0C)
    if count < 0 or (count and (intro_relative == 0 or intro_relative + count * 0x4C > module_size)):
        raise ValueError("scene intro array is invalid")
    objects: list[dict[str, object]] = []
    for index in range(count):
        relative = intro_relative + index * 0x4C
        raw_name = data[module_offset + relative : module_offset + relative + 16]
        name = raw_name.split(b"\0", 1)[0].decode("ascii", errors="replace") or f"intro_{index}"
        rotation = struct.unpack_from("<hhh", data, module_offset + relative + 0x18)
        position = struct.unpack_from("<hhh", data, module_offset + relative + 0x20)
        identifier = i32(relative + 0x14)
        objects.append(
            {
                "kind": "object",
                "asset": name,
                "animation": "",
                "name": name,
                # Leave the override blank so changing the asset selector
                # changes the runtime object family automatically. The
                # original name is still retained in ``asset`` and ``name``.
                "runtime_name": "",
                "runtime_id": identifier,
                "identifier": identifier,
                "rotation_xyz": list(rotation),
                "rotation": float(rotation[2]) * 360.0 / 4096.0,
                "x": position[0],
                "y": position[1],
                "z": position[2],
                "flags": i32(relative + 0x2C),
                "native": True,
            }
        )
    return objects


def _parse_scene_lights(path: Path) -> list[dict[str, object]]:
    """Read native point/spot lights from the serialized Level header."""
    data = path.read_bytes()
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("light field is outside the module")
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    def i32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("light field is outside the module")
        return struct.unpack_from("<i", data, module_offset + relative)[0]

    def read_table(table_kind: str, count_relative: int, pointer_relative: int, stride: int, position_offset: int) -> list[dict[str, object]]:
        count = i32(count_relative)
        pointer = u32(pointer_relative)
        if count < 0 or count > 0x1000 or (count and (pointer == 0 or pointer + count * stride > module_size)):
            raise ValueError(f"{table_kind} light table is invalid")
        result: list[dict[str, object]] = []
        for index in range(count):
            relative = pointer + index * stride
            position = struct.unpack_from("<hhh", data, module_offset + relative + position_offset)
            color = list(data[module_offset + relative + 0x08 : module_offset + relative + 0x0B])
            result.append(
                {
                    "kind": "light",
                    "light_kind": table_kind,
                    "table_kind": table_kind,
                    "table_index": index,
                    "asset": "",
                    "animation": "",
                    "name": f"{table_kind}_light_{index}",
                    "runtime_name": "",
                    "x": position[0],
                    "y": position[1],
                    "z": position[2],
                    "rotation": 0,
                    "scale": 1,
                    "color": color,
                    "radius": struct.unpack_from("<h", data, module_offset + relative + (0x12 if table_kind == "point" else 0x12))[0],
                    "native": True,
                }
            )
        return result

    return read_table("point", 0x18, 0x1C, 0x18, 0x0C) + read_table("spot", 0x10, 0x14, 0x28, 0x18)


def _apply_scene_lights(source: Path, lights: list[dict[str, object]], destination: Path) -> int:
    """Update native light positions without changing table sizes or links."""
    ensure_distinct_paths(source, destination)
    data = bytearray(source.read_bytes())
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset
    changed = 0
    tables = {
        "point": (0x18, 0x1C, 0x18, 0x0C),
        "spot": (0x10, 0x14, 0x28, 0x18),
    }
    for index, light in enumerate(lights):
        if not isinstance(light, dict) or light.get("kind") != "light":
            raise ValueError("only kind=light placements can be written to light tables")
        table_kind = str(light.get("table_kind", light.get("light_kind", ""))).lower()
        if table_kind not in tables:
            raise ValueError(f"light[{index}] must specify table_kind=point or spot")
        table_index = light.get("table_index")
        if isinstance(table_index, bool) or not isinstance(table_index, int) or table_index < 0:
            raise ValueError(f"light[{index}].table_index must be a non-negative integer")
        count_relative, pointer_relative, stride, position_offset = tables[table_kind]
        count = struct.unpack_from("<i", data, module_offset + count_relative)[0]
        pointer = struct.unpack_from("<I", data, module_offset + pointer_relative)[0]
        if table_index >= count or pointer == 0 or pointer + (table_index + 1) * stride > module_size:
            raise ValueError(f"light[{index}] table index is outside the {table_kind} light table")
        values = tuple(light.get(axis) for axis in ("x", "y", "z"))
        if any(isinstance(value, bool) or not isinstance(value, int) or not -32768 <= value <= 32767 for value in values):
            raise ValueError(f"light[{index}] coordinates must be signed 16-bit integers")
        relative = pointer + table_index * stride + position_offset
        old = struct.unpack_from("<hhh", data, module_offset + relative)
        if old != values:
            struct.pack_into("<hhh", data, module_offset + relative, *values)
            changed += 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    _parse_drm(destination)
    _parse_scene_lights(destination)
    return changed


def _parse_stream_portals(path: Path) -> list[dict[str, object]]:
    """Read the serialized StreamUnitPortal records from a scene.

    Portal geometry is stored in the terrain's stream-unit table.  The eight
    coordinate blocks are six-byte SVectors followed by two-byte padding; the
    parser deliberately returns the padding-free values so callers can compare
    reciprocal portals without accidentally treating padding as coordinates.
    """
    data = path.read_bytes()
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("portal field is outside the DRM module")
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    def i32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("portal field is outside the DRM module")
        return struct.unpack_from("<i", data, module_offset + relative)[0]

    def i16(relative: int) -> int:
        if relative < 0 or relative + 2 > module_size:
            raise ValueError("portal field is outside the DRM module")
        return struct.unpack_from("<h", data, module_offset + relative)[0]

    terrain_relative = u32(0x00)
    if terrain_relative >= module_size:
        raise ValueError("terrain pointer is outside DRM module")
    stream_relative = u32(terrain_relative + 0x30)
    if stream_relative == 0 or stream_relative + 4 > module_size:
        raise ValueError("stream portal table pointer is outside DRM module")
    count = i32(stream_relative)
    portal_size = 0x5C
    if count < 0 or count > 0x1000 or stream_relative + 4 + count * portal_size > module_size:
        raise ValueError("stream portal table has an invalid count or span")

    def svec(record: int, offset: int) -> list[int]:
        return [i16(record + offset), i16(record + offset + 2), i16(record + offset + 4)]

    portals: list[dict[str, object]] = []
    for index in range(count):
        relative = stream_relative + 4 + index * portal_size
        name = data[module_offset + relative : module_offset + relative + 16].split(b"\0", 1)[0].decode(
            "ascii", "replace"
        )
        portals.append(
            {
                "index": index,
                "name": name,
                "signal_id": i32(relative + 0x10),
                "stream_id": i32(relative + 0x14),
                "flags": i16(relative + 0x1E),
                "bounds_min": svec(relative, 0x18),
                "bounds_max": svec(relative, 0x20),
                "t1": [svec(relative, 0x2C + point * 8) for point in range(3)],
                "t2": [svec(relative, 0x44 + point * 8) for point in range(3)],
            }
        )
    return portals


def _compare_stream_portals(left: Path, right: Path) -> dict[str, object]:
    """Compare reciprocal portal frames between two scene assets.

    A reciprocal pair is identified by the serialized stream IDs and each
    scene's stream-unit ID. The result reports right-minus-left coordinate
    deltas for the bounds and both triangle windings; it does not modify either
    file and is useful for diagnosing imported-room transforms.
    """
    left_summary = _parse_drm(left)
    right_summary = _parse_drm(right)
    left_portals = _parse_stream_portals(left)
    right_portals = _parse_stream_portals(right)
    matches: list[dict[str, object]] = []

    def delta(a: list[int], b: list[int]) -> list[int]:
        return [int(b[index]) - int(a[index]) for index in range(3)]

    def frame_delta(left_frame: list[list[int]], right_frame: list[list[int]]) -> list[list[int]]:
        # Reciprocal portal triangles may use a different winding/order. Pick
        # the point permutation with the smallest total absolute delta so a
        # pure winding change is not misreported as a geometric offset.
        best: tuple[int, tuple[int, ...]] | None = None
        for order in permutations(range(3)):
            score = sum(
                abs(value)
                for index, right_index in enumerate(order)
                for value in delta(left_frame[index], right_frame[right_index])
            )
            if best is None or score < best[0]:
                best = (score, order)
        assert best is not None
        return [delta(left_frame[index], right_frame[best[1][index]]) for index in range(3)]

    def polygon_delta(left_portal: dict[str, object], right_portal: dict[str, object]) -> list[list[int]]:
        left_points = sorted({tuple(point) for frame in (left_portal["t1"], left_portal["t2"]) for point in frame})
        right_points = sorted({tuple(point) for frame in (right_portal["t1"], right_portal["t2"]) for point in frame})
        if len(left_points) != len(right_points) or len(left_points) > 8:
            return []
        best: tuple[int, tuple[int, ...]] | None = None
        for order in permutations(range(len(right_points))):
            score = sum(
                abs(value)
                for index, right_index in enumerate(order)
                for value in delta(list(left_points[index]), list(right_points[right_index]))
            )
            if best is None or score < best[0]:
                best = (score, order)
        assert best is not None
        return [
            delta(list(left_points[index]), list(right_points[best[1][index]]))
            for index in range(len(left_points))
        ]

    for left_portal in left_portals:
        candidates = [
            portal
            for portal in right_portals
            if int(left_portal["stream_id"]) == right_summary.stream_unit_id
            and int(portal["stream_id"]) == left_summary.stream_unit_id
        ]
        if not candidates:
            matches.append(
                {
                    "left": left_portal,
                    "right": None,
                    "status": "unmatched",
                }
            )
            continue
        right_portal = candidates[0]
        alignment_translation = delta(right_portal["bounds_min"], left_portal["bounds_min"])
        polygon_deltas = polygon_delta(left_portal, right_portal)
        deltas = [
            *delta(left_portal["bounds_min"], right_portal["bounds_min"]),
            *delta(left_portal["bounds_max"], right_portal["bounds_max"]),
            *[value for row in polygon_deltas for value in row],
        ]
        matches.append(
            {
                "left": left_portal,
                "right": right_portal,
                "status": "exact" if not any(deltas) else "offset",
                "bounds_min_delta": delta(left_portal["bounds_min"], right_portal["bounds_min"]),
                "bounds_max_delta": delta(left_portal["bounds_max"], right_portal["bounds_max"]),
                "alignment_translation": alignment_translation,
                "t1_delta": frame_delta(left_portal["t1"], right_portal["t1"]),
                "t2_delta": frame_delta(left_portal["t2"], right_portal["t2"]),
                "max_abs_delta": max(abs(value) for value in deltas),
            }
        )
    return {
        "format": "soul-re-portal-compare-v1",
        "left": str(left),
        "right": str(right),
        "left_stream_unit_id": left_summary.stream_unit_id,
        "right_stream_unit_id": right_summary.stream_unit_id,
        "matches": matches,
    }


def _parse_bsp_roots(path: Path) -> list[dict[str, object]]:
    """Read the serialized BSP tree roots and their bounding volumes."""
    data = path.read_bytes()
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("BSP field is outside the scene module")
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    def i32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("BSP field is outside the scene module")
        return struct.unpack_from("<i", data, module_offset + relative)[0]

    def shorts(relative: int, count: int) -> list[int]:
        if relative < 0 or relative + count * 2 > module_size:
            raise ValueError("BSP bounding volume is outside the scene module")
        return list(struct.unpack_from("<" + "h" * count, data, module_offset + relative))

    terrain = u32(0x00)
    tree_count = i32(terrain + 0x44)
    tree_array = u32(terrain + 0x48)
    if tree_count < 0 or tree_count > 0x1000 or tree_array + tree_count * 0x24 > module_size:
        raise ValueError("BSP tree array has an invalid count or span")
    roots: list[dict[str, object]] = []
    for index in range(tree_count):
        tree = tree_array + index * 0x24
        root = u32(tree)
        if root == 0 or root + 0x34 > module_size:
            raise ValueError(f"BSP tree {index} has an invalid root pointer")
        roots.append(
            {
                "index": index,
                "root_relative": root,
                "sphere": shorts(root + 0x00, 4),
                "box": shorts(root + 0x10, 6),
                "spectral_box": shorts(root + 0x1C, 6),
                "spectral_sphere": shorts(root + 0x28, 4),
            }
        )
    return roots


def _scene_alignment_report(left: Path, right: Path) -> dict[str, object]:
    """Report room bounds and BSP-root positions after portal relocation."""
    comparison = _compare_stream_portals(left, right)
    left_summary = _parse_drm(left)
    right_summary = _parse_drm(right)
    left_roots = _parse_bsp_roots(left)
    right_roots = _parse_bsp_roots(right)

    def shifted(values: tuple[int, int, int] | list[int], translation: list[int]) -> list[int]:
        return [int(values[index]) + int(translation[index]) for index in range(3)]

    pair_reports: list[dict[str, object]] = []
    for match in comparison["matches"]:
        right_portal = match.get("right")
        if not isinstance(right_portal, dict):
            continue
        translation = [int(value) for value in match["alignment_translation"]]
        translated_min = shifted(right_summary.bounds_min, translation)
        translated_max = shifted(right_summary.bounds_max, translation)
        root_pairs = []
        for index, (left_root, right_root) in enumerate(zip(left_roots, right_roots)):
            left_sphere = left_root["sphere"]
            right_sphere = right_root["sphere"]
            translated_right = shifted(right_sphere[:3], translation)
            root_pairs.append(
                {
                    "index": index,
                    "left_sphere": left_sphere,
                    "right_sphere_after_translation": [*translated_right, right_sphere[3]],
                    "center_delta": [translated_right[axis] - left_sphere[axis] for axis in range(3)],
                }
            )
        pair_reports.append(
            {
                "left_portal": match["left"]["name"],
                "right_portal": right_portal["name"],
                "translation": translation,
                "right_scene_bounds_after_translation": {
                    "min": translated_min,
                    "max": translated_max,
                },
                "bsp_root_pairs": root_pairs,
            }
        )
    return {
        "format": "soul-re-scene-alignment-v1",
        "left": str(left),
        "right": str(right),
        "left_bounds": {"min": list(left_summary.bounds_min), "max": list(left_summary.bounds_max)},
        "right_bounds": {"min": list(right_summary.bounds_min), "max": list(right_summary.bounds_max)},
        "portal_comparison": comparison,
        "pairs": pair_reports,
    }


def _export_scene_json(path: Path, destination: Path) -> None:
    summary = _parse_drm(path)
    markers = _parse_scene_markers(path)
    try:
        portals = _parse_stream_portals(path)
        portal_warning = None
    except (ValueError, struct.error) as exc:
        portals = []
        portal_warning = str(exc)
    payload = {
        "format": "soul-re-scene-v1",
        "source": str(path),
        "summary": asdict(summary),
        "markers": [asdict(marker) for marker in markers],
        "portals": portals,
    }
    if portal_warning:
        payload["portal_warning"] = portal_warning
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _patch_scene(source: Path, scene_path: Path, destination: Path) -> int:
    """Apply marker positions from a scene JSON to a new DRM copy."""
    ensure_distinct_paths(source, destination)
    payload = json.loads(scene_path.read_text(encoding="utf-8"))
    if payload.get("format") != "soul-re-scene-v1":
        raise ValueError("scene JSON format is not soul-re-scene-v1")
    requested = payload.get("markers")
    if not isinstance(requested, list):
        raise ValueError("scene JSON markers must be an array")

    data = bytearray(source.read_bytes())
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(relative: int) -> int:
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    def i32(relative: int) -> int:
        return struct.unpack_from("<i", data, module_offset + relative)[0]

    terrain_relative = u32(0)
    terrain_file = module_offset + terrain_relative
    intro_count = i32(terrain_relative + 0x08)
    intro_relative = u32(terrain_relative + 0x0C)
    plan_count = i32(0xE0)
    plan_relative = u32(0xE4)
    sfx_count = i32(0xE8)
    sfx_relative = u32(0xEC)
    counts = {"intro": intro_count, "plan": plan_count, "sfx": sfx_count}
    bases = {"intro": intro_relative, "plan": plan_relative, "sfx": sfx_relative}
    strides = {"intro": 0x4C, "plan": 0x08, "sfx": 0x24}
    position_offsets = {"intro": 0x20, "plan": 0x00, "sfx": 0x14}
    position_sizes = {"intro": 0x4C, "plan": 0x08, "sfx": 0x24}

    def validate_coordinate(value: object, label: str) -> int:
        if not isinstance(value, int) or not -32768 <= value <= 32767:
            raise ValueError(f"{label} must be a signed 16-bit integer")
        return value

    changed = 0
    for marker in requested:
        if not isinstance(marker, dict):
            raise ValueError("each marker must be an object")
        kind = marker.get("kind")
        index = marker.get("index")
        if kind not in counts or not isinstance(index, int) or not 0 <= index < counts[kind]:
            raise ValueError(f"invalid marker slot: {kind!r} index={index!r}")
        coords = marker.get("x"), marker.get("y"), marker.get("z")
        x, y, z = (validate_coordinate(value, f"{kind}[{index}].{axis}") for value, axis in zip(coords, "xyz"))
        relative = bases[kind] + index * strides[kind] + position_offsets[kind]
        if relative + position_sizes[kind] > module_size:
            raise ValueError(f"{kind}[{index}] position is outside DRM module")
        old_coords = struct.unpack_from("<hhh", data, module_offset + relative)
        if kind == "intro" and tuple((x, y, z)) != old_coords:
            # Intro records with a non-null multi-spline pointer are animated
            # paths, not free-standing markers. Moving only their base
            # position would desynchronise the path and can crash the loader.
            spline_relative = struct.unpack_from("<I", data, module_offset + bases[kind] + index * strides[kind] + 0x38)[0]
            if spline_relative:
                raise ValueError(
                    f"intro[{index}] is attached to a multi-spline; edit its path data instead of moving the marker"
                )
        struct.pack_into("<hhh", data, module_offset + relative, x, y, z)
        if tuple((x, y, z)) != old_coords:
            changed += 1

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    # Re-parse both the source copy and the result to ensure the output still
    # has a valid serialized marker layout.
    _parse_drm(destination)
    _parse_scene_markers(destination)
    return changed


def _placement_asset_name(placement: dict[str, object], index: int) -> str:
    """Convert a viewer placement into the 16-byte runtime object name."""
    raw_asset = placement.get("asset")
    raw_name = placement.get("name")
    raw_runtime_name = placement.get("runtime_name")
    candidate = str(raw_runtime_name or raw_asset or raw_name or f"custom_object_{index}").replace("\\", "/")
    stem = Path(candidate).stem.strip().lower()
    # Object SRMs are commonly named ``RAZIEL_1.SRM`` while the runtime intro
    # table references the object family (``raziel``), not a numbered mesh.
    # Existing object assets commonly use a numbered mesh suffix (e.g.
    # RAZIEL_1.SRM), while a genuinely authored runtime name may end in
    # digits. Callers can now provide runtime_name explicitly to avoid the
    # legacy family-name normalization in that case.
    if raw_runtime_name is None:
        stem = re.sub(r"_\d+$", "", stem)
    if not stem:
        raise ValueError(f"placement {index} has an empty runtime object name")
    try:
        encoded = stem.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"placement {index} runtime object name must be ASCII") from exc
    if len(encoded) > 15:
        raise ValueError(f"placement {index} runtime object name exceeds 15 bytes: {stem!r}")
    return stem


def _append_object_names(
    source: Path, names: Iterable[str], destination: Path
) -> list[str]:
    """Append runtime object names to a native Level object-name table.

    The table is a sequence of 16-byte, NUL-padded names terminated by a
    record whose first byte is ``0xFF``.  The Level already has a relocatable
    pointer to this table, so replacing that pointer with an appended table
    keeps the loader's relocation bookkeeping intact.
    """
    ensure_distinct_paths(source, destination)
    data = bytearray(source.read_bytes())
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset
    if module_size < 0x90:
        raise ValueError("scene module is too small for a Level object list")

    object_list_relative = struct.unpack_from("<I", data, module_offset + 0x8C)[0]
    if object_list_relative == 0 or object_list_relative >= module_size:
        raise ValueError("Level object-name pointer is outside the scene module")

    existing: list[bytes] = []
    existing_names: set[str] = set()
    cursor = object_list_relative
    for _ in range(0x1000):
        absolute = module_offset + cursor
        if absolute + 16 > len(data):
            raise ValueError("Level object-name table extends beyond the scene")
        record = bytes(data[absolute : absolute + 16])
        if record[0] == 0xFF:
            break
        name_bytes = record.split(b"\0", 1)[0]
        if not name_bytes:
            raise ValueError("Level object-name table contains an empty entry")
        existing.append(record)
        existing_names.add(name_bytes.decode("ascii", errors="strict").lower())
        cursor += 16
    else:
        raise ValueError("Level object-name table has no terminator")

    added: list[str] = []
    for raw_name in names:
        name = str(raw_name).strip().lower()
        if not name:
            raise ValueError("object names cannot be empty")
        try:
            encoded = name.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError(f"object name must be ASCII: {name!r}") from exc
        if len(encoded) > 15:
            raise ValueError(f"object name exceeds 15 bytes: {name!r}")
        if name in existing_names:
            continue
        existing_names.add(name)
        added.append(name)

    if not added:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        _parse_drm(destination)
        return []

    records = bytearray().join(existing)
    for name in added:
        records.extend(name.encode("ascii").ljust(16, b"\0"))
    records.extend(b"\xFF" + b"\0" * 15)

    while len(data) % 4:
        data.append(0)
    new_relative = len(data) - module_offset
    data.extend(records)
    # This field is already part of the native relocation table.
    struct.pack_into("<I", data, module_offset + 0x8C, new_relative)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    _parse_drm(destination)
    return added


def _append_scene_placements(
    source: Path,
    placements: list[dict[str, object]],
    destination: Path,
    reserved_ids: set[int] | None = None,
) -> list[dict[str, object]]:
    ensure_distinct_paths(source, destination)
    """Append new object intros and reuse exact existing placements.

    This deliberately appends a new intro array instead of shifting any existing
    serialized data. The Terrain intro count/pointer are updated in place, and
    the new records contain no pointers of their own. Exact placement signatures
    are reused so applying the same sidecar twice does not create duplicates.
    Only ``kind=object`` placements map to runtime intro records.
    """
    if not placements:
        raise ValueError("placements must contain at least one object")

    data = bytearray(source.read_bytes())
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("scene field is outside the module")
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    def i32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("scene field is outside the module")
        return struct.unpack_from("<i", data, module_offset + relative)[0]

    terrain_relative = u32(0x00)
    if terrain_relative >= module_size or terrain_relative + 0x10 > module_size:
        raise ValueError("terrain pointer is outside the scene module")
    intro_count = i32(terrain_relative + 0x08)
    intro_relative = u32(terrain_relative + 0x0C)
    if intro_count < 0:
        raise ValueError("scene intro count is negative")
    if intro_count and (intro_relative == 0 or intro_relative + intro_count * 0x4C > module_size):
        raise ValueError("scene intro array extends beyond the module")

    existing_ids: list[int] = []
    existing_signatures: dict[tuple[object, ...], int] = {}
    for index in range(intro_count):
        relative = intro_relative + index * 0x4C
        record = data[module_offset + relative : module_offset + relative + 0x4C]
        if len(record) != 0x4C:
            raise ValueError("scene intro record is truncated")
        existing_ids.append(struct.unpack_from("<i", record, 0x14)[0])
        name_bytes = bytes(record[0:16]).split(b"\0", 1)[0].decode("ascii", "replace").lower()
        rotation = struct.unpack_from("<hhhh", record, 0x18)
        position = struct.unpack_from("<hhh", record, 0x20)
        flags = struct.unpack_from("<i", record, 0x2C)[0]
        existing_signatures[(name_bytes, rotation[:3], position, flags)] = existing_ids[-1]

    used_ids = set(existing_ids)
    if reserved_ids:
        used_ids.update(int(value) for value in reserved_ids)
    next_identifier = max(PLACEMENT_ID_MIN, max(used_ids, default=PLACEMENT_ID_MIN - 1) + 1)
    if next_identifier > 0x7FFFFFFF:
        raise ValueError("no signed 32-bit runtime identifiers remain")

    def coordinate(value: object, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not -32768 <= value <= 32767:
            raise ValueError(f"{label} must be a signed 16-bit integer")
        return value

    # The pointer is replaced with a new contiguous array, so preserve every
    # existing intro record before appending the newly authored objects.
    records = bytearray(
        data[module_offset + intro_relative : module_offset + intro_relative + intro_count * 0x4C]
        if intro_count
        else b""
    )
    assigned: list[dict[str, object]] = []
    appended_count = 0
    for index, placement in enumerate(placements):
        if not isinstance(placement, dict):
            raise ValueError("each placement must be an object")
        if placement.get("kind", "object") != "object":
            raise ValueError("only kind=object placements can be written to the runtime intro table")
        name = _placement_asset_name(placement, index)
        x = coordinate(placement.get("x"), f"placement[{index}].x")
        y = coordinate(placement.get("y"), f"placement[{index}].y")
        z = coordinate(placement.get("z"), f"placement[{index}].z")
        rotation = placement.get("rotation", 0)
        if isinstance(rotation, bool) or not isinstance(rotation, (int, float)):
            raise ValueError(f"placement[{index}].rotation must be numeric")
        rotation_units = int(round(float(rotation) * 4096.0 / 360.0))
        if not -32768 <= rotation_units <= 32767:
            raise ValueError(f"placement[{index}].rotation is outside the supported range")
        raw_rotation = placement.get("rotation_xyz")
        if isinstance(raw_rotation, (list, tuple)) and len(raw_rotation) == 3:
            rotation_xyz = tuple(coordinate(value, f"placement[{index}].rotation_xyz") for value in raw_rotation)
        else:
            # The viewer's rotation field is a yaw around the engine Z axis.
            rotation_xyz = (0, 0, rotation_units)
        raw_flags = placement.get("flags", 0)
        if isinstance(raw_flags, bool) or not isinstance(raw_flags, int) or not -0x80000000 <= raw_flags <= 0x7FFFFFFF:
            raise ValueError(f"placement[{index}].flags must be a signed 32-bit integer")
        signature = (name, rotation_xyz, (x, y, z), raw_flags)
        existing_identifier = existing_signatures.get(signature)
        if existing_identifier is not None:
            assigned.append(
                {
                    "index": index,
                    "name": name,
                    "identifier": existing_identifier,
                    "x": x,
                    "y": y,
                    "z": z,
                    "rotation_xyz": list(rotation_xyz),
                    "flags": raw_flags,
                    "existing": True,
                }
            )
            continue

        # Build a fresh, pointer-free intro.  Copying a neighbour carries over
        # spectralPosition, lighting group, mesh color, radius and hidden event
        # pointers, which makes a new object depend on unrelated state.
        record = bytearray(0x4C)
        record[0:16] = name.encode("ascii").ljust(16, b"\0")
        struct.pack_into("<i", record, 0x14, next_identifier)
        struct.pack_into("<hhh", record, 0x18, *rotation_xyz)
        struct.pack_into("<hhh", record, 0x20, x, y, z)
        raw_intronum = placement.get("intronum")
        if raw_intronum is not None:
            if isinstance(raw_intronum, bool) or not isinstance(raw_intronum, int) or not -0x80000000 <= raw_intronum <= 0x7FFFFFFF:
                raise ValueError(f"placement[{index}].intronum must be a signed 32-bit integer")
            struct.pack_into("<i", record, 0x10, raw_intronum)
        raw_max_rad = placement.get("max_rad")
        if raw_max_rad is not None:
            if isinstance(raw_max_rad, bool) or not isinstance(raw_max_rad, int) or not -32768 <= raw_max_rad <= 32767:
                raise ValueError(f"placement[{index}].max_rad must be a signed 16-bit integer")
            struct.pack_into("<h", record, 0x26, raw_max_rad)
        raw_max_rad_sq = placement.get("max_rad_sq")
        if raw_max_rad_sq is not None:
            if isinstance(raw_max_rad_sq, bool) or not isinstance(raw_max_rad_sq, int) or not -0x80000000 <= raw_max_rad_sq <= 0x7FFFFFFF:
                raise ValueError(f"placement[{index}].max_rad_sq must be a signed 32-bit integer")
            struct.pack_into("<i", record, 0x28, raw_max_rad_sq)
        struct.pack_into("<i", record, 0x2C, raw_flags)
        # The zeroed tail intentionally clears data/instance/multiSpline,
        # dsignal, spectral lighting, mesh color, spectral position and pads.
        records.extend(record)
        appended_count += 1
        existing_signatures[signature] = next_identifier
        used_ids.add(next_identifier)
        assigned.append(
            {
                "index": index,
                "name": name,
                "identifier": next_identifier,
                "x": x,
                "y": y,
                "z": z,
                "rotation_xyz": list(rotation_xyz),
                "flags": raw_flags,
                "existing": False,
            }
        )
        next_identifier += 1
        if next_identifier > 0x7FFFFFFF and index + 1 < len(placements):
            raise ValueError("no signed 32-bit runtime identifiers remain")

    # If every requested placement already exists, preserve the source byte
    # for byte. This avoids growing the file with another orphaned copy of the
    # intro array on a repeated Apply.
    if appended_count == 0:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        _parse_drm(destination)
        return assigned

    # Keep all existing offsets stable and put the replacement array at EOF.
    while len(data) % 4:
        data.append(0)
    new_intro_relative = len(data) - module_offset
    data.extend(records)
    struct.pack_into("<i", data, module_offset + terrain_relative + 0x08, intro_count + appended_count)
    struct.pack_into("<I", data, module_offset + terrain_relative + 0x0C, new_intro_relative)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    _parse_drm(destination)
    parsed = _parse_scene_markers(destination)
    if len([marker for marker in parsed if marker.kind == "intro"]) != intro_count + appended_count:
        raise ValueError("written scene intro count did not round-trip")
    return assigned


def _apply_managed_scene_placements(
    source: Path,
    placements: list[dict[str, object]],
    remove_runtime_ids: list[object] | None,
    destination: Path,
    reserved_ids: set[int] | None = None,
) -> dict[str, object]:
    """Synchronize editor-owned object records in a staged scene.

    IDs in the reserved high range are owned by the scene editor.  Native
    intro records are never removed or rewritten unless the sidecar explicitly
    carries one of those editor IDs.  Existing editor records are updated in
    place in a replacement intro array; missing records are appended with a
    stable ID; requested removals are omitted from the replacement array.
    Reapplying an unchanged sidecar therefore produces byte-identical output.
    """
    ensure_distinct_paths(source, destination)
    if not isinstance(placements, list):
        raise ValueError("placements must be an array")
    remove_runtime_ids = remove_runtime_ids or []
    data = bytearray(source.read_bytes())
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("scene field is outside the module")
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    def i32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("scene field is outside the module")
        return struct.unpack_from("<i", data, module_offset + relative)[0]

    def coordinate(value: object, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not -32768 <= value <= 32767:
            raise ValueError(f"{label} must be a signed 16-bit integer")
        return value

    def runtime_id(value: object, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not -0x80000000 <= value <= 0x7FFFFFFF:
            raise ValueError(f"{label} must be a signed 32-bit runtime ID")
        return value

    terrain_relative = u32(0x00)
    if terrain_relative >= module_size or terrain_relative + 0x10 > module_size:
        raise ValueError("terrain pointer is outside the scene module")
    intro_count = i32(terrain_relative + 0x08)
    intro_relative = u32(terrain_relative + 0x0C)
    if intro_count < 0:
        raise ValueError("scene intro count is negative")
    if intro_count and (intro_relative == 0 or intro_relative + intro_count * 0x4C > module_size):
        raise ValueError("scene intro array extends beyond the module")

    records: list[bytearray] = []
    by_id: dict[int, bytearray] = {}
    signatures: dict[tuple[object, ...], int] = {}
    existing_ids: set[int] = set()
    for index in range(intro_count):
        relative = intro_relative + index * 0x4C
        record = bytearray(data[module_offset + relative : module_offset + relative + 0x4C])
        if len(record) != 0x4C:
            raise ValueError("scene intro record is truncated")
        identifier = struct.unpack_from("<i", record, 0x14)[0]
        existing_ids.add(identifier)
        records.append(record)
        by_id[identifier] = record
        if identifier >= PLACEMENT_ID_MIN:
            name = bytes(record[:16]).split(b"\0", 1)[0].decode("ascii", "replace").lower()
            rotation = struct.unpack_from("<hhh", record, 0x18)
            position = struct.unpack_from("<hhh", record, 0x20)
            flags = struct.unpack_from("<i", record, 0x2C)[0]
            signatures[(name, rotation, position, flags)] = identifier

    used_ids = set(existing_ids)
    if reserved_ids:
        used_ids.update(int(value) for value in reserved_ids)
    next_identifier = max(PLACEMENT_ID_MIN, max(used_ids, default=PLACEMENT_ID_MIN - 1) + 1)

    def build_values(placement: dict[str, object], index: int) -> tuple[str, tuple[int, int, int], tuple[int, int, int], int]:
        if not isinstance(placement, dict) or placement.get("kind", "object") != "object":
            raise ValueError("only kind=object placements can be written to the runtime intro table")
        name = _placement_asset_name(placement, index)
        coords = tuple(coordinate(placement.get(axis), f"placement[{index}].{axis}") for axis in ("x", "y", "z"))
        rotation = placement.get("rotation", 0)
        if isinstance(rotation, bool) or not isinstance(rotation, (int, float)):
            raise ValueError(f"placement[{index}].rotation must be numeric")
        rotation_units = int(round(float(rotation) * 4096.0 / 360.0))
        if not -32768 <= rotation_units <= 32767:
            raise ValueError(f"placement[{index}].rotation is outside the supported range")
        raw_rotation = placement.get("rotation_xyz")
        if isinstance(raw_rotation, (list, tuple)) and len(raw_rotation) == 3:
            rotation_xyz = tuple(coordinate(value, f"placement[{index}].rotation_xyz") for value in raw_rotation)
        else:
            rotation_xyz = (0, 0, rotation_units)
        raw_flags = placement.get("flags", 0)
        if isinstance(raw_flags, bool) or not isinstance(raw_flags, int) or not -0x80000000 <= raw_flags <= 0x7FFFFFFF:
            raise ValueError(f"placement[{index}].flags must be a signed 32-bit integer")
        return name, rotation_xyz, coords, raw_flags

    requested_ids: set[int] = set()
    assigned: list[dict[str, object]] = []
    changed = False
    change_count = 0
    for index, placement in enumerate(placements):
        name, rotation_xyz, coords, flags = build_values(placement, index)
        raw_id = placement.get("runtime_id", placement.get("identifier"))
        identifier = runtime_id(raw_id, f"placement[{index}].runtime_id") if raw_id is not None else None
        if identifier is None:
            identifier = signatures.get((name, rotation_xyz, coords, flags))
        if identifier is None:
            while next_identifier in used_ids:
                next_identifier += 1
            if next_identifier > 0x7FFFFFFF:
                raise ValueError("no signed 32-bit runtime identifiers remain")
            identifier = next_identifier
            next_identifier += 1
            record = bytearray(0x4C)
            record[0:16] = name.encode("ascii").ljust(16, b"\0")
            struct.pack_into("<i", record, 0x14, identifier)
            records.append(record)
            by_id[identifier] = record
            used_ids.add(identifier)
            changed = True
            change_count += 1
            state = "added"
        else:
            record = by_id.get(identifier)
            if record is None:
                if identifier < PLACEMENT_ID_MIN:
                    raise ValueError(f"placement[{index}] references a missing native runtime ID: {identifier}")
                if reserved_ids and identifier in {int(value) for value in reserved_ids}:
                    raise ValueError(f"placement[{index}] runtime ID is already reserved by another scene: {identifier}")
                record = bytearray(0x4C)
                struct.pack_into("<i", record, 0x14, identifier)
                records.append(record)
                by_id[identifier] = record
                used_ids.add(identifier)
                changed = True
                state = "added"
            else:
                state = "updated"
        requested_ids.add(identifier)
        before = bytes(record)
        record[0:16] = name.encode("ascii").ljust(16, b"\0")
        struct.pack_into("<hhh", record, 0x18, *rotation_xyz)
        struct.pack_into("<hhh", record, 0x20, *coords)
        struct.pack_into("<i", record, 0x2C, flags)
        if state != "added" and bytes(record) != before:
            changed = True
            change_count += 1
            if state == "updated":
                state = "updated"
        assigned.append({"index": index, "name": name, "identifier": identifier, "runtime_id": identifier,
                         "x": coords[0], "y": coords[1], "z": coords[2], "rotation_xyz": list(rotation_xyz),
                         "flags": flags, "existing": state == "updated", "state": state})

    removed: list[int] = []
    for raw_id in remove_runtime_ids:
        identifier = runtime_id(raw_id, "remove_runtime_ids entry")
        if identifier in requested_ids:
            raise ValueError(f"cannot remove a placement that is still present: {identifier}")
        if identifier in by_id:
            removed.append(identifier)
            changed = True
            change_count += 1
    if removed:
        records = [record for record in records if struct.unpack_from("<i", record, 0x14)[0] not in set(removed)]

    if not changed:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        _parse_drm(destination)
        return {"assigned": assigned, "removed": removed, "changed": 0}

    while len(data) % 4:
        data.append(0)
    new_intro_relative = len(data) - module_offset
    data.extend(b"".join(records))
    struct.pack_into("<i", data, module_offset + terrain_relative + 0x08, len(records))
    struct.pack_into("<I", data, module_offset + terrain_relative + 0x0C, new_intro_relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    _parse_drm(destination)
    _parse_scene_markers(destination)
    return {"assigned": assigned, "removed": removed, "changed": change_count}


def _collect_scene_identifiers(root: Path) -> set[int]:
    """Collect intro UniqueIDs across a staged AREA tree.

    Intro IDs are resolved globally by the game, not just within one unit.
    Parse every DRM/PCM scene that can be read and fail closed if a candidate
    scene is malformed instead of allocating an ID from an incomplete view.
    """
    identifiers: set[int] = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".drm", ".pcm"}:
            continue
        try:
            markers = _parse_scene_markers(path)
        except (ValueError, struct.error):
            # Standalone object PCMs can share the extension but do not have a
            # Level intro table. They are not part of the world ID namespace.
            continue
        identifiers.update(marker.identifier for marker in markers if marker.kind == "intro")
    return identifiers


def _export_svg(path: Path, destination: Path) -> DrmSummary:
    """Export a dependency-free top-down terrain and marker preview as SVG."""
    data = path.read_bytes()
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(relative: int) -> int:
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    def i32(relative: int) -> int:
        return struct.unpack_from("<i", data, module_offset + relative)[0]

    terrain_relative = u32(0)
    terrain_file = module_offset + terrain_relative
    num_vertices = i32(terrain_relative + 0x10)
    num_faces = i32(terrain_relative + 0x14)
    vertex_relative = struct.unpack_from("<I", data, terrain_file + 0x1C)[0]
    face_relative = struct.unpack_from("<I", data, terrain_file + 0x20)[0]
    if num_vertices < 0 or num_faces < 0:
        raise ValueError("DRM contains a negative geometry count")
    if vertex_relative + num_vertices * 12 > module_size or face_relative + num_faces * 12 > module_size:
        raise ValueError("geometry arrays extend beyond DRM module")

    vertices = [
        struct.unpack_from("<hhh", data, module_offset + vertex_relative + index * 12)
        for index in range(num_vertices)
    ]
    faces = [
        struct.unpack_from("<HHH", data, module_offset + face_relative + index * 12)
        for index in range(num_faces)
    ]
    summary = _parse_drm(path)
    markers = _parse_scene_markers(path)
    width, height, margin = 1400, 900, 28
    min_x, min_y, _ = summary.bounds_min
    max_x, max_y, _ = summary.bounds_max
    span_x = max(max_x - min_x, 1)
    span_y = max(max_y - min_y, 1)

    def project(x: int, y: int) -> tuple[float, float]:
        px = margin + (x - min_x) * (width - 2 * margin) / span_x
        py = height - margin - (y - min_y) * (height - 2 * margin) / span_y
        return px, py

    def marker_color(kind: str) -> str:
        return {"intro": "#E45756", "plan": "#4C78A8", "sfx": "#F2CF5B"}.get(kind, "#FFFFFF")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as stream:
        stream.write(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}">\n'
        )
        stream.write('<rect width="100%" height="100%" fill="#111827"/>\n')
        stream.write('<g fill="#2D6A4F" fill-opacity="0.23" stroke="#84A98C" stroke-opacity="0.35" stroke-width="0.45">\n')
        for v0, v1, v2 in faces:
            if v0 >= num_vertices or v1 >= num_vertices or v2 >= num_vertices:
                continue
            points = " ".join(f"{x:.2f},{y:.2f}" for x, y in (project(*vertices[v0][:2]), project(*vertices[v1][:2]), project(*vertices[v2][:2])))
            stream.write(f'<polygon points="{points}"/>\n')
        stream.write("</g>\n")
        stream.write('<g stroke="#F8FAFC" stroke-width="1.4" fill="none">\n')
        stream.write(f'<rect x="{margin}" y="{margin}" width="{width - margin * 2}" height="{height - margin * 2}"/>\n</g>\n')
        stream.write('<g font-family="Segoe UI, sans-serif" font-size="12">\n')
        for marker in markers:
            x, y = project(marker.x, marker.y)
            color = marker_color(marker.kind)
            stream.write(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{color}" stroke="#111827"/>\n')
            stream.write(f'<text x="{x + 7:.2f}" y="{y - 6:.2f}" fill="{color}">{marker.kind}: {marker.name}</text>\n')
        stream.write("</g>\n")
        stream.write(
            '<g font-family="Segoe UI, sans-serif" font-size="16" fill="#F8FAFC">'
            f'<text x="{margin}" y="20">{path.name} — top-down terrain preview</text></g>\n'
        )
        stream.write("</svg>\n")
    return summary


def _export_obj(path: Path, destination: Path) -> DrmSummary:
    """Export the terrain vertex/face arrays as a coordinate-only OBJ."""
    data = path.read_bytes()
    module_offset, relocation_count = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(offset: int) -> int:
        return struct.unpack_from("<I", data, module_offset + offset)[0]

    def i32(offset: int) -> int:
        return struct.unpack_from("<i", data, module_offset + offset)[0]

    terrain_relative = u32(0)
    terrain = module_offset + terrain_relative
    num_vertices = i32(terrain_relative + 0x10)
    num_faces = i32(terrain_relative + 0x14)
    vertex_relative = struct.unpack_from("<I", data, terrain + 0x1C)[0]
    face_relative = struct.unpack_from("<I", data, terrain + 0x20)[0]
    if vertex_relative + num_vertices * 12 > module_size or face_relative + num_faces * 12 > module_size:
        raise ValueError("geometry arrays extend beyond DRM module")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as stream:
        stream.write(f"# Soul Reaver terrain export: {path.name}\n")
        for index in range(num_vertices):
            x, y, z = struct.unpack_from("<hhh", data, module_offset + vertex_relative + index * 12)
            stream.write(f"v {x} {y} {z}\n")
        for index in range(num_faces):
            v0, v1, v2 = struct.unpack_from("<HHH", data, module_offset + face_relative + index * 12)
            # OBJ indices are 1-based. Invalid faces are retained as comments
            # rather than silently producing a misleading mesh.
            if v0 >= num_vertices or v1 >= num_vertices or v2 >= num_vertices:
                stream.write(f"# invalid face {v0} {v1} {v2}\n")
            else:
                stream.write(f"f {v0 + 1} {v1 + 1} {v2 + 1}\n")
    return _parse_drm(path)


def print_drm_summary(summary: DrmSummary) -> None:
    print(f"DRM: {summary.path}")
    print(f"Module: offset=0x{summary.module_offset:X}, size={summary.module_size:,} bytes")
    print(f"Relocations: {summary.relocation_count:,}")
    print(f"World: {summary.world_name or '<unnamed>'}  stream unit={summary.stream_unit_id}")
    print(
        "Geometry: "
        f"vertices={summary.vertex_count:,}, faces={summary.face_count:,}, "
        f"normals={summary.normal_count:,}, BSP trees={summary.bsp_tree_count:,}"
    )
    print(
        "Gameplay markers: "
        f"intros={summary.intro_count:,}, plan={summary.plan_marker_count:,}, "
        f"SFX={summary.sfx_marker_count:,}"
    )
    print(f"Bounds: min={summary.bounds_min}, max={summary.bounds_max}")


def print_marker_summary(markers: Iterable[SceneMarker]) -> None:
    markers = list(markers)
    by_kind: dict[str, int] = defaultdict(int)
    for marker in markers:
        by_kind[marker.kind] += 1
    counts = ", ".join(f"{kind}={count}" for kind, count in sorted(by_kind.items())) or "none"
    print(f"Markers: {counts}")
    for marker in markers[:12]:
        print(
            f"  {marker.kind:5}[{marker.index:3}] {marker.name:16} id={marker.identifier:6} "
            f"pos=({marker.x:6}, {marker.y:6}, {marker.z:6}) flags=0x{marker.flags:X}"
        )
    if len(markers) > 12:
        print(f"  ... {len(markers) - 12} more (use --scene for all markers)")


def launch_gui(assets: list[Asset], root: Path) -> int:
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        print("Tkinter is not available; use the text report or install Python Tk.", file=sys.stderr)
        return 2

    by_area: dict[str, list[Asset]] = defaultdict(list)
    for asset in assets:
        by_area[asset.area].append(asset)

    window = tk.Tk()
    window.title("Soul Reaver Level Browser")
    window.geometry("1100x650")

    outer = ttk.PanedWindow(window, orient=tk.HORIZONTAL)
    outer.pack(fill=tk.BOTH, expand=True)
    left = ttk.Frame(outer, padding=8)
    right = ttk.Frame(outer, padding=8)
    outer.add(left, weight=1)
    outer.add(right, weight=2)

    tree = ttk.Treeview(left, columns=("kind", "size"), show="tree headings")
    tree.heading("kind", text="Type")
    tree.heading("size", text="Size")
    tree.column("kind", width=75, anchor=tk.CENTER)
    tree.column("size", width=95, anchor=tk.E)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=tree.yview)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    tree.configure(yscrollcommand=scroll.set)

    details = tk.Text(right, wrap=tk.WORD, state=tk.DISABLED)
    details.pack(fill=tk.BOTH, expand=True)
    asset_by_id: dict[str, Asset] = {}

    for area in sorted(by_area):
        area_id = tree.insert("", tk.END, text=area, values=("area", ""), open=False)
        for index, asset in enumerate(sorted(by_area[area], key=lambda item: (item.stem, item.extension, item.name))):
            item_id = f"{area}:{index}"
            asset_by_id[item_id] = asset
            tree.insert(
                area_id,
                tk.END,
                iid=item_id,
                text=asset.name,
                values=(asset.extension, _human_size(asset.size)),
            )

    def show_details(_event: object = None) -> None:
        selected = tree.selection()
        if not selected or selected[0] not in asset_by_id:
            return
        asset = asset_by_id[selected[0]]
        path = root / Path(asset.relative_path)
        text = (
            f"Area: {asset.area}\n"
            f"File: {asset.relative_path}\n"
            f"Type: {asset.extension}\n"
            f"Size: {_human_size(asset.size)} ({asset.size:,} bytes)\n"
            f"SHA-1: {asset.sha1}\n\n"
            f"First 64 bytes:\n{_read_preview(path)}\n\n"
            "This browser is read-only. Geometry and pointer relocation are the next milestone."
        )
        details.configure(state=tk.NORMAL)
        details.delete("1.0", tk.END)
        details.insert("1.0", text)
        details.configure(state=tk.DISABLED)

    tree.bind("<<TreeviewSelect>>", show_details)
    window.mainloop()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("game/bigfile/kain2/area"),
        help="extracted area directory (default: game/bigfile/kain2/area)",
    )
    parser.add_argument("--area", help="only inspect one area, such as city or aluka")
    parser.add_argument("--manifest", type=Path, help="write a JSON inventory to this path")
    parser.add_argument(
        "--inspect",
        type=Path,
        help="inspect one DRM, relative to --root (for example city/bin/city2.drm)",
    )
    parser.add_argument(
        "--obj",
        type=Path,
        help="export the inspected DRM terrain to an OBJ file",
    )
    parser.add_argument(
        "--scene",
        type=Path,
        help="export inspected DRM marker metadata to JSON",
    )
    parser.add_argument(
        "--svg",
        type=Path,
        help="export an inspected DRM top-down terrain/marker preview as SVG",
    )
    parser.add_argument(
        "--patch-scene",
        type=Path,
        help="apply marker coordinates from a scene JSON to a new DRM copy",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="destination for --patch-scene (must not be the source DRM)",
    )
    parser.add_argument("--gui", action="store_true", help="open the desktop browser after scanning")
    args = parser.parse_args(argv)

    if args.inspect:
        inspect_path = (args.root / args.inspect).resolve()
        root_resolved = args.root.resolve()
        if root_resolved not in inspect_path.parents:
            parser.error("--inspect must point inside --root")
        if inspect_path.suffix.lower() != ".drm":
            parser.error("--inspect currently supports .drm files only")
        try:
            summary = _parse_drm(inspect_path)
            print_drm_summary(summary)
            markers = _parse_scene_markers(inspect_path)
            print_marker_summary(markers)
            if args.obj:
                _export_obj(inspect_path, args.obj)
                print(f"OBJ: {args.obj} ({args.obj.stat().st_size:,} bytes)")
            if args.scene:
                _export_scene_json(inspect_path, args.scene)
                print(f"Scene: {args.scene} ({args.scene.stat().st_size:,} bytes)")
            if args.svg:
                _export_svg(inspect_path, args.svg)
                print(f"SVG: {args.svg} ({args.svg.stat().st_size:,} bytes)")
            if args.patch_scene:
                if not args.output:
                    parser.error("--patch-scene requires --output")
                output_path = args.output.resolve()
                if output_path == inspect_path:
                    parser.error("--output must be a new DRM path; the source is never overwritten")
                changed = _patch_scene(inspect_path, args.patch_scene, output_path)
                print(f"Patched markers: {changed}")
                print(f"DRM copy: {output_path} ({output_path.stat().st_size:,} bytes)")
        except (OSError, ValueError, struct.error) as error:
            parser.error(str(error))
        return 0

    try:
        assets = discover(args.root, args.area)
    except (FileNotFoundError, OSError) as error:
        parser.error(str(error))
    if not assets:
        parser.error("no supported assets found")

    print_report(assets, args.root)
    if args.manifest:
        write_manifest(assets, args.manifest)
        print(f"\nWrote manifest: {args.manifest}")
    if args.gui:
        return launch_gui(assets, args.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
