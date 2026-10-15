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
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional


SUPPORTED_EXTENSIONS = {".drm", ".pcm", ".crm", ".smf", ".snf", ".tim", ".smp", ".snd"}
DUPLICATE_RE = re.compile(r"_duplicate\d+$", re.IGNORECASE)


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
    table_end = 4 + (relocation_count + 1) * 4
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


def _export_scene_json(path: Path, destination: Path) -> None:
    summary = _parse_drm(path)
    markers = _parse_scene_markers(path)
    payload = {
        "format": "soul-re-scene-v1",
        "source": str(path),
        "summary": asdict(summary),
        "markers": [asdict(marker) for marker in markers],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _patch_scene(source: Path, scene_path: Path, destination: Path) -> int:
    """Apply marker positions from a scene JSON to a new DRM copy."""
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
        struct.pack_into("<hhh", data, module_offset + relative, x, y, z)
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
    candidate = str(raw_asset or raw_name or f"custom_object_{index}").replace("\\", "/")
    stem = Path(candidate).stem.strip().lower()
    # Object SRMs are commonly named ``RAZIEL_1.SRM`` while the runtime intro
    # table references the object family (``raziel``), not a numbered mesh.
    stem = re.sub(r"_\d+$", "", stem)
    if not stem:
        raise ValueError(f"placement {index} has an empty runtime object name")
    try:
        encoded = stem.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"placement {index} runtime object name must be ASCII") from exc
    if len(encoded) > 16:
        raise ValueError(f"placement {index} runtime object name exceeds 16 bytes: {stem!r}")
    return stem


def _append_scene_placements(
    source: Path, placements: list[dict[str, object]], destination: Path
) -> list[dict[str, object]]:
    """Append object intro records and return the assigned runtime identifiers.

    This deliberately appends a new intro array instead of shifting any existing
    serialized data.  The Terrain intro count/pointer are updated in place, and
    the new records contain no pointers of their own.  It is therefore safe to
    produce a new file and atomically replace a writable AREA copy after making a
    backup.  Only ``kind=object`` placements map to runtime intro records.
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
    template = bytearray(0x4C)
    for index in range(intro_count):
        relative = intro_relative + index * 0x4C
        record = data[module_offset + relative : module_offset + relative + 0x4C]
        if len(record) != 0x4C:
            raise ValueError("scene intro record is truncated")
        existing_ids.append(struct.unpack_from("<i", record, 0x14)[0])
        # A normal object intro has no camera/path flag.  Reusing its defaults
        # preserves fields the game may expect beyond the documented transform.
        if struct.unpack_from("<i", record, 0x2C)[0] == 0 and template == bytearray(0x4C):
            template = bytearray(record)

    next_identifier = max(existing_ids, default=0) + 1
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
        record = bytearray(template)
        record[0:16] = name.encode("ascii").ljust(16, b"\0")
        struct.pack_into("<i", record, 0x14, next_identifier)
        struct.pack_into("<hhh", record, 0x18, rotation_units, 0, 0)
        struct.pack_into("<hhh", record, 0x20, x, y, z)
        records.extend(record)
        assigned.append({"index": index, "name": name, "identifier": next_identifier, "x": x, "y": y, "z": z})
        next_identifier += 1
        if next_identifier > 0x7FFFFFFF and index + 1 < len(placements):
            raise ValueError("no signed 32-bit runtime identifiers remain")

    # Keep all existing offsets stable and put the replacement array at EOF.
    while len(data) % 4:
        data.append(0)
    new_intro_relative = len(data) - module_offset
    data.extend(records)
    struct.pack_into("<i", data, module_offset + terrain_relative + 0x08, intro_count + len(placements))
    struct.pack_into("<I", data, module_offset + terrain_relative + 0x0C, new_intro_relative)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    _parse_drm(destination)
    parsed = _parse_scene_markers(destination)
    if len([marker for marker in parsed if marker.kind == "intro"]) != intro_count + len(placements):
        raise ValueError("written scene intro count did not round-trip")
    return assigned


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
