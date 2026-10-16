#!/usr/bin/env python3
"""Serve an interactive, dependency-free WebGL viewer for extracted DRM/PCM assets."""

from __future__ import annotations

import argparse
import io
import datetime as dt
import hashlib
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

try:  # direct ``python tools/level_viewer.py`` invocation
    from level_editor import (
        _append_scene_placements,
        _module_offset,
        _parse_drm,
        _parse_scene_markers,
        _patch_scene,
    )
    from event_extractor import EventReader
except ModuleNotFoundError:  # imported as ``tools.level_viewer``
    from tools.level_editor import (
        _append_scene_placements,
        _module_offset,
        _parse_drm,
        _parse_scene_markers,
        _patch_scene,
    )
    from tools.event_extractor import EventReader


def _event_report(path: Path) -> dict[str, object]:
    """Return the structured event graph used by the event editor."""
    return EventReader(path).read()


def _patch_events(source: Path, payload: dict[str, object], destination: Path) -> int:
    """Patch fixed-size event fields and existing script words.

    This first writer intentionally does not resize the relocatable module. It
    can therefore edit existing event numbers, variables, puppet state, and
    16-bit p-code operands while preserving every pointer and relocation entry.
    Adding new event lines will use a separate append-and-relocate writer.
    """
    report = _event_report(source)
    events = report.get("events")
    if not isinstance(events, list):
        raise ValueError("event report is malformed")
    event_by_index = {int(event["index"]): event for event in events if isinstance(event, dict)}
    patches = payload.get("patches")
    if not isinstance(patches, list):
        raise ValueError("patches must be an array")
    data = bytearray(source.read_bytes())
    module_offset = int(report["module_offset"])
    changed = 0

    def short_value(value: object, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not -32768 <= value <= 32767:
            raise ValueError(f"{label} must be a signed 16-bit integer")
        return value

    def byte_value(value: object, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
            raise ValueError(f"{label} must be an unsigned byte")
        return value

    def write_i16(relative: int, value: int) -> None:
        nonlocal changed
        absolute = module_offset + relative
        if absolute < module_offset or absolute + 2 > len(data):
            raise ValueError("event patch points outside the module")
        old = struct.unpack_from("<h", data, absolute)[0]
        if old != value:
            struct.pack_into("<h", data, absolute, value)
            changed += 1

    def write_u8(relative: int, value: int) -> None:
        nonlocal changed
        absolute = module_offset + relative
        if absolute < module_offset or absolute >= len(data):
            raise ValueError("event patch points outside the module")
        if data[absolute] != value:
            data[absolute] = value
            changed += 1

    for patch in patches:
        if not isinstance(patch, dict):
            raise ValueError("each event patch must be an object")
        try:
            event_index = int(patch["event_index"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("event_index must be an integer") from exc
        event = event_by_index.get(event_index)
        if event is None:
            raise ValueError(f"unknown event index {event_index}")
        kind = patch.get("kind")
        event_relative = int(event["relative_offset"])
        if kind == "event_number":
            write_i16(event_relative, short_value(patch.get("value"), "event number"))
        elif kind == "event_variable":
            variable_index = patch.get("variable_index")
            if isinstance(variable_index, bool) or not isinstance(variable_index, int) or not 0 <= variable_index < 5:
                raise ValueError("variable_index must be between 0 and 4")
            write_i16(event_relative + 6 + variable_index * 2, short_value(patch.get("value"), "event variable"))
        elif kind == "puppet_state":
            write_u8(event_relative + 5, byte_value(patch.get("value"), "puppet state"))
        elif kind == "script_word":
            action_index = patch.get("action_index")
            if isinstance(action_index, bool) or not isinstance(action_index, int) or action_index < 0:
                raise ValueError("action_index must be a non-negative integer")
            script_name = patch.get("script")
            if script_name not in {"condition", "action"}:
                raise ValueError("script must be 'condition' or 'action'")
            actions = event.get("actions")
            if not isinstance(actions, list) or action_index >= len(actions):
                raise ValueError(f"unknown action index {action_index} in event {event_index}")
            action = actions[action_index]
            script = action.get(script_name) if isinstance(action, dict) else None
            if not isinstance(script, dict):
                raise ValueError("selected event line has no requested script")
            word_index = patch.get("word_index")
            if isinstance(word_index, bool) or not isinstance(word_index, int) or not 0 <= word_index < int(script["size_words"]):
                raise ValueError("word_index is outside the selected script")
            write_i16(int(script["data_relative_offset"]) + word_index * 2, short_value(patch.get("value"), "script word"))
        else:
            raise ValueError(f"unsupported event patch kind: {kind}")
    destination.write_bytes(data)
    return changed


def _terrain_geometry(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    module_offset, _ = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(relative: int) -> int:
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    def i32(relative: int) -> int:
        return struct.unpack_from("<i", data, module_offset + relative)[0]

    terrain_relative = u32(0)
    if terrain_relative >= module_size:
        raise ValueError("terrain pointer is outside DRM module")
    terrain_file = module_offset + terrain_relative
    vertex_count = i32(terrain_relative + 0x10)
    face_count = i32(terrain_relative + 0x14)
    vertex_relative = u32(terrain_relative + 0x1C)
    face_relative = u32(terrain_relative + 0x20)
    if vertex_count < 0 or face_count < 0:
        raise ValueError("DRM contains a negative geometry count")
    if vertex_relative + vertex_count * 12 > module_size:
        raise ValueError("vertex array extends beyond DRM module")
    if face_relative + face_count * 12 > module_size:
        raise ValueError("face array extends beyond DRM module")

    vertices = [
        struct.unpack_from("<hhh", data, module_offset + vertex_relative + index * 12)
        for index in range(vertex_count)
    ]
    faces = [
        struct.unpack_from("<HHH", data, module_offset + face_relative + index * 12)
        for index in range(face_count)
    ]
    marker_warning = None
    try:
        markers = [marker.__dict__ for marker in _parse_scene_markers(path)]
    except (ValueError, struct.error) as exc:
        # Early prototype scenes can use a different marker-table layout while
        # retaining the same terrain/geometry header.  Keep those scenes
        # visible in the viewer and expose the limitation to callers instead
        # of misclassifying them as standalone object PCMs.
        markers = []
        marker_warning = str(exc)

    result = {
        "format": "soul-re-view-v1",
        "asset_type": "drm",
        "path": path.as_posix(),
        "summary": _parse_drm(path).__dict__,
        "vertices": vertices,
        "faces": faces,
        "markers": markers,
    }
    if marker_warning:
        result["marker_warning"] = marker_warning
    return result


def _geometry(path: Path) -> dict[str, object]:
    if path.suffix.lower() != ".pcm":
        return _terrain_geometry(path)
    # AREA/*.pcm files are room/level containers in the remaster, while
    # OBJECT/*.pcm files are standalone model containers.  They share the
    # relocatable header, so try the level layout first.
    try:
        result = _terrain_geometry(path)
    except (ValueError, struct.error):
        return _pcm_geometry(path)
    result["asset_type"] = "pcm-level"
    return result


def _pcm_geometry(path: Path) -> dict[str, object]:
    """Decode the relocatable SR1 object/model layout used by PCMs.

    PCM files are object assets, not room terrain.  Their root contains a
    model-pointer array at +0x0C; each model uses the classic Model header and
    MVertex/MFace arrays from ``include/Game/TYPES.h``.
    """
    data = path.read_bytes()
    module_offset, relocation_count = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("PCM field is outside the module")
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    def u16(relative: int) -> int:
        if relative < 0 or relative + 2 > module_size:
            raise ValueError("PCM field is outside the module")
        return struct.unpack_from("<H", data, module_offset + relative)[0]

    def checked_array(relative: int, count: int, stride: int, label: str) -> None:
        if count < 0 or relative < 0 or relative + count * stride > module_size:
            raise ValueError(f"{label} array extends beyond PCM module")
        if count and relative == 0:
            raise ValueError(f"{label} array has a null pointer")

    model_count = u16(0x08)
    model_list = u32(0x0C)
    if model_count == 0:
        raise ValueError("PCM contains no models")
    checked_array(model_list, model_count, 4, "model pointer")

    vertices: list[tuple[int, int, int]] = []
    faces: list[tuple[int, int, int]] = []
    model_summaries: list[dict[str, int]] = []
    bounds_min = [32767, 32767, 32767]
    bounds_max = [-32768, -32768, -32768]

    for model_index in range(model_count):
        model_relative = u32(model_list + model_index * 4)
        if model_relative == 0:
            continue
        if model_relative + 0x38 > module_size:
            raise ValueError(f"PCM model {model_index} header is outside the module")
        vertex_count = u32(model_relative + 0x00)
        vertex_relative = u32(model_relative + 0x04)
        normal_count = u32(model_relative + 0x08)
        normal_relative = u32(model_relative + 0x0C)
        face_count = u32(model_relative + 0x10)
        face_relative = u32(model_relative + 0x14)
        checked_array(vertex_relative, vertex_count, 8, f"model {model_index} vertex")
        checked_array(normal_relative, normal_count, 6, f"model {model_index} normal")
        checked_array(face_relative, face_count, 12, f"model {model_index} face")
        if vertex_count > 0x100000 or face_count > 0x200000:
            raise ValueError(f"PCM model {model_index} geometry count is unreasonable")

        base_vertex = len(vertices)
        for index in range(vertex_count):
            x, y, z = struct.unpack_from(
                "<hhh", data, module_offset + vertex_relative + index * 8
            )
            vertices.append((x, y, z))
            bounds_min[0] = min(bounds_min[0], x)
            bounds_min[1] = min(bounds_min[1], y)
            bounds_min[2] = min(bounds_min[2], z)
            bounds_max[0] = max(bounds_max[0], x)
            bounds_max[1] = max(bounds_max[1], y)
            bounds_max[2] = max(bounds_max[2], z)

        for index in range(face_count):
            v0, v1, v2 = struct.unpack_from(
                "<HHH", data, module_offset + face_relative + index * 12
            )
            if v0 >= vertex_count or v1 >= vertex_count or v2 >= vertex_count:
                raise ValueError(f"PCM model {model_index} face index is outside its vertex array")
            faces.append((base_vertex + v0, base_vertex + v1, base_vertex + v2))
        model_summaries.append(
            {
                "index": model_index,
                "vertex_count": vertex_count,
                "normal_count": normal_count,
                "face_count": face_count,
                "relative_offset": model_relative,
            }
        )

    if not vertices or not faces:
        raise ValueError(
            "PCM has no decodable model geometry; this may be a stream/area PCM variant"
        )
    summary = {
        "path": str(path),
        "module_offset": module_offset,
        "module_size": module_size,
        "relocation_count": relocation_count,
        "world_name": "",
        "stream_unit_id": 0,
        "vertex_count": len(vertices),
        "face_count": len(faces),
        "normal_count": sum(model["normal_count"] for model in model_summaries),
        "intro_count": 0,
        "bsp_tree_count": 0,
        "plan_marker_count": 0,
        "sfx_marker_count": 0,
        "bounds_min": tuple(bounds_min),
        "bounds_max": tuple(bounds_max),
        "model_count": model_count,
        "decoded_model_count": len(model_summaries),
        "models": model_summaries,
    }
    return {
        "format": "soul-re-view-v1",
        "asset_type": "pcm",
        "path": path.as_posix(),
        "summary": summary,
        "vertices": vertices,
        "faces": faces,
        "markers": [],
    }


def _srm_geometry(path: Path) -> dict[str, object]:
    """Decode the SR1 remaster's standalone SRM model container.

    Soul Reaver I's SRM files contain one mesh chunk followed by interleaved
    float positions, packed normals/weights, and 16-bit triangle indices. The
    companion SRA files are runtime object metadata; they do not contain the
    mesh itself, so the viewer exposes SRM files as the selectable geometry.
    """
    data = path.read_bytes()
    if len(data) < 8 or data[:4] != b"SRM\x01":
        raise ValueError("SRM has an unsupported magic/version")
    offset = 4

    def read_u32(label: str) -> int:
        nonlocal offset
        if offset + 4 > len(data):
            raise ValueError(f"SRM {label} field is outside the file")
        value = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        return value

    def skip(size: int, label: str) -> None:
        nonlocal offset
        if size < 0 or offset + size > len(data):
            raise ValueError(f"SRM {label} section is outside the file")
        offset += size

    shader_count = read_u32("shader count")
    if not 1 <= shader_count <= 8:
        raise ValueError(f"SRM shader count is unreasonable: {shader_count}")
    for _ in range(shader_count):
        read_u32("shader type")
        skip(16, "shader parameters")
        skip(24, "shader stream ranges")

    texture_count = read_u32("texture count")
    if texture_count > 512:
        raise ValueError(f"SRM texture count is unreasonable: {texture_count}")
    texture_names: list[str] = []
    for _ in range(texture_count):
        skip_start = offset
        skip(31, "texture name")
        texture_names.append(
            data[skip_start : skip_start + 31].split(b"\0", 1)[0].decode("ascii", "replace")
        )
        skip(1, "texture flag")

    extra_bone_count = read_u32("extra bone count")
    if extra_bone_count > 128:
        raise ValueError(f"SRM extra bone count is unreasonable: {extra_bone_count}")
    bone_count = 32 + extra_bone_count
    # The remaster calls these 48-byte records "bones", but each record is
    # actually four packed segment-origin vectors (4 x float3), not a 3x4
    # matrix.  Keeping them as positions is important: SRM vertex influences
    # use the original segment numbers, which can be greater than the number
    # of 48-byte records.
    segment_positions: list[tuple[float, float, float]] = []
    bone_matrices = []
    for index in range(bone_count):
        values = struct.unpack_from("<12f", data, offset + index * 48)
        bone_matrices.append(values)
        segment_positions.extend(
            (tuple(values[start : start + 3]) for start in range(0, 12, 3))
        )
    skip(bone_count * 48, "bone matrices")
    if offset + 4 > len(data):
        raise ValueError("SRM bone transform check is outside the file")
    if struct.unpack_from("<I", data, offset)[0] != 0x80000000:
        # The optional transform marker is absent in some models.
        pass
    else:
        offset += 4
    skip(128, "bone flags")

    vertex_count = read_u32("vertex count")
    face_count = read_u32("face count")
    if (
        vertex_count == 0
        or vertex_count > 200_000
        or face_count == 0
        or face_count > 600_000
        or face_count % 3
    ):
        raise ValueError(f"SRM geometry counts are unreasonable: {vertex_count}/{face_count}")
    if offset + vertex_count * 32 + (face_count // 3) * 6 > len(data):
        raise ValueError("SRM geometry extends beyond the file")

    vertices: list[tuple[float, float, float]] = []
    bone_indices: list[tuple[int, int, int]] = []
    bone_weights: list[tuple[int, int, int]] = []
    material_indices: list[int] = []
    uvs: list[tuple[float, float]] = []
    bounds_min = [float("inf"), float("inf"), float("inf")]
    bounds_max = [float("-inf"), float("-inf"), float("-inf")]
    for _ in range(vertex_count):
        x, y, z = struct.unpack_from("<3f", data, offset)
        material_indices.append(data[offset + 19])
        # SRM vertices use three bone indices, one packed U coordinate, three
        # normalized bone weights, and one packed V coordinate.  The UV bytes
        # sit between the index and weight triplets; treating V as a weight
        # makes animated meshes shear and stretch noticeably.
        vertex_bone_indices = tuple(data[offset + 20 : offset + 23])
        vertex_bone_weights = tuple(data[offset + 24 : offset + 27])
        uvs.append((data[offset + 23] / 255.0, 1.0 - data[offset + 27] / 255.0))
        offset += 32
        if not all(math.isfinite(value) for value in (x, y, z)):
            raise ValueError("SRM contains a non-finite vertex")
        vertices.append((x, y, z))
        bone_indices.append(vertex_bone_indices)
        bone_weights.append(vertex_bone_weights)
        bounds_min[0] = min(bounds_min[0], x)
        bounds_min[1] = min(bounds_min[1], y)
        bounds_min[2] = min(bounds_min[2], z)
        bounds_max[0] = max(bounds_max[0], x)
        bounds_max[1] = max(bounds_max[1], y)
        bounds_max[2] = max(bounds_max[2], z)

    faces: list[tuple[int, int, int]] = []
    for _ in range(face_count // 3):
        v0, v1, v2 = struct.unpack_from("<3H", data, offset)
        offset += 6
        if max(v0, v1, v2) >= vertex_count:
            raise ValueError("SRM face index is outside its vertex array")
        faces.append((v2, v1, v0))

    summary = {
        "path": str(path),
        "file_size": len(data),
        "world_name": path.stem,
        "shader_count": shader_count,
        "texture_count": texture_count,
        "textures": texture_names,
        "texture_files": [f"{name}_D.DDS" for name in texture_names],
        "extra_bone_count": extra_bone_count,
        "bone_count": bone_count,
        "segment_position_count": len(segment_positions),
        "vertex_count": vertex_count,
        "face_count": face_count // 3,
        "bounds_min": tuple(bounds_min),
        "bounds_max": tuple(bounds_max),
    }
    return {
        "format": "soul-re-view-v1",
        "asset_type": "srm",
        "path": path.as_posix(),
        "summary": summary,
        "vertices": vertices,
        "faces": faces,
        "bone_matrices": bone_matrices,
        "segment_positions": segment_positions,
        "bone_indices": bone_indices,
        "bone_weights": bone_weights,
        "material_indices": material_indices,
        "uvs": uvs,
        "texture_files": [f"{name}_D.DDS" for name in texture_names],
        "markers": [],
    }


def _srl_animation(path: Path) -> dict[str, object]:
    """Decode the remaster SRL matrix stream used by scripted animations.

    SRL stores a contiguous frame-major array of 3x4 affine matrices.  The
    first header value is the number of animated channels, the third is the
    frame count, and each channel/frame record is 48 bytes (12 little-endian
    floats).  The fourth value is the source skeleton channel limit and is
    retained as metadata for diagnostics.
    """
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != b"SRL\x02":
        raise ValueError("SRL has an unsupported magic/version")
    channel_count, flags, frame_count, skeleton_limit = struct.unpack_from("<4I", data, 4)
    payload = len(data) - 20
    if not 1 <= channel_count <= 128 or not 1 <= frame_count <= 10000:
        raise ValueError("SRL animation dimensions are unreasonable")
    expected = channel_count * frame_count * 48
    if payload != expected:
        raise ValueError(
            f"SRL payload size mismatch: expected {expected} bytes, found {payload}"
        )
    frames: list[list[tuple[float, ...]]] = []
    offset = 20
    for _frame in range(frame_count):
        frame: list[tuple[float, ...]] = []
        for _channel in range(channel_count):
            matrix = struct.unpack_from("<12f", data, offset)
            if not all(math.isfinite(value) for value in matrix):
                raise ValueError("SRL contains a non-finite transform")
            frame.append(matrix)
            offset += 48
        frames.append(frame)
    return {
        "format": "soul-re-view-animation-v1",
        "asset_type": "srl",
        "path": path.as_posix(),
        "summary": {
            "path": str(path),
            "file_size": len(data),
            "channel_count": channel_count,
            "frame_count": frame_count,
            "skeleton_limit": skeleton_limit,
            "flags": flags,
            "duration_frames": frame_count,
        },
        "frames": frames,
    }


def _sra_animation_catalog(path: Path) -> dict[str, object]:
    """Read the embedded G2 animation list from an object SRA archive.

    SRA files are relocatable PS1 object modules.  Their module root is a
    pointer array terminated by ``0xFFFFFFFF``; each entry points at a
    ``G2AnimKeylist``.  The keylists contain the clip timing and pointers to
    the compressed channel streams.  This deliberately stops at the catalog
    layer: the channel streams use the original G2 adaptive/linear codecs and
    need the source model's segment hierarchy to turn them into SRM skin
    matrices.
    """
    data = path.read_bytes()
    module_offset, relocation_count = _module_offset(data)
    module_size = len(data) - module_offset

    def u32(relative: int) -> int:
        if relative < 0 or relative + 4 > module_size:
            raise ValueError("SRA field is outside the module")
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    def checked_pointer(value: int, label: str) -> int:
        if value == 0 or value >= module_size:
            raise ValueError(f"SRA {label} pointer is outside the module")
        return value

    clips: list[dict[str, object]] = []
    root_offset = 0
    while root_offset + 4 <= module_size:
        keylist_offset = u32(root_offset)
        root_offset += 4
        if keylist_offset == 0xFFFFFFFF:
            break
        if len(clips) >= 256:
            raise ValueError("SRA animation list is unreasonably long")
        keylist_offset = checked_pointer(keylist_offset, "keylist")
        if keylist_offset + 0x14 > module_size:
            raise ValueError("SRA keylist header is outside the module")
        section_count, s0_tail, s1_tail, s2_tail, key_count, time_per_key = struct.unpack_from(
            "<4B H h", data, module_offset + keylist_offset
        )
        if section_count > 3 or key_count > 10000 or time_per_key < 0:
            raise ValueError("SRA keylist metadata is unreasonable")
        section_pointers: list[int] = []
        for section in range(section_count):
            section_pointer = u32(keylist_offset + 0x14 + section * 4)
            section_pointers.append(checked_pointer(section_pointer, f"section {section}"))
        flag_offset = keylist_offset + 0x14 + section_count * 4
        if flag_offset >= module_size:
            raise ValueError("SRA keylist flags are outside the module")
        clips.append(
            {
                "index": len(clips),
                "keylist_offset": keylist_offset,
                "section_count": section_count,
                "key_count": key_count,
                "time_per_key": time_per_key,
                "duration_ms": key_count * time_per_key,
                "tail_times": [s0_tail, s1_tail, s2_tail],
                "section_data_offsets": section_pointers,
                "flags_offset": flag_offset,
                "flags": data[module_offset + flag_offset],
            }
        )
    else:
        raise ValueError("SRA animation list has no terminator")

    return {
        "format": "soul-re-view-sra-v1",
        "asset_type": "sra",
        "path": path.as_posix(),
        "summary": {
            "path": str(path),
            "file_size": len(data),
            "module_offset": module_offset,
            "module_size": module_size,
            "relocation_count": relocation_count,
            "animation_count": len(clips),
        },
        "clips": clips,
    }


def _sra_flag_channels(data: bytes, module_offset: int, flags_offset: int,
                       active_bits: int, segment_count: int) -> list[tuple[str, int, int]]:
    """Expand the packed G2 per-segment channel flags.

    The first byte is the active channel-group mask (rotation, scale,
    translation).  Each active group then contributes three bits per segment;
    inactive groups do not consume space in the flag stream.  This is the
    detail that is easy to miss when looking at an SRA in a hex editor.
    """
    flag_base = module_offset + flags_offset
    bytes_per_group = (segment_count * 3 + 7) // 8
    bit_offset = 8
    channels: list[tuple[str, int, int]] = []
    for group_bit, group_name in ((0x1, "rotation"), (0x2, "scale"), (0x4, "translation")):
        if not (active_bits & group_bit):
            continue
        for segment in range(segment_count):
            for component in range(3):
                absolute_bit = bit_offset + segment * 3 + component
                byte_offset = absolute_bit // 8
                if (data[flag_base + byte_offset] >> (absolute_bit & 7)) & 1:
                    channels.append((group_name, segment, component))
        bit_offset += bytes_per_group * 8
    return channels


def _sra_stream_blocks(data: bytes, module_offset: int, start: int, end: int,
                       key_count: int) -> list[list[float]]:
    """Decode the remaster's float channel blocks.

    In the remaster SRA files examined so far a channel is either mode 0
    (``uint32 mode`` followed by one float per key) or mode 1 (mode plus one
    constant float).  Keeping the parser strict about bounds makes malformed
    archives report a useful error instead of producing corrupt animation.
    """
    if key_count <= 0:
        return []
    blocks: list[list[float]] = []
    position = start
    while position < end:
        if position + 8 > end:
            break
        mode = struct.unpack_from("<I", data, module_offset + position)[0]
        if mode == 0:
            size = 4 + key_count * 4
            if position + size > end:
                raise ValueError("SRA mode-0 channel extends beyond its clip")
            values = list(struct.unpack_from(f"<{key_count}f", data, module_offset + position + 4))
        elif mode == 1:
            size = 8
            value = struct.unpack_from("<f", data, module_offset + position + 4)[0]
            values = [value] * key_count
        else:
            # Last clips may be followed by alignment/padding.  Only accept
            # padding when it is all zero; any other value is a real format
            # mismatch and should be visible to the caller.
            tail = data[module_offset + position:end]
            if tail and not all(byte == 0 for byte in tail):
                raise ValueError(f"unsupported SRA channel block mode {mode}")
            break
        if not all(math.isfinite(value) for value in values):
            raise ValueError("SRA channel contains a non-finite value")
        blocks.append(values)
        position += size
    return blocks


def _sra_rotation_matrix(rotation: tuple[float, float, float]) -> tuple[float, ...]:
    """Build a row-major ZYX Euler matrix from G2's 4096-unit angles."""
    x, y, z = (value * (2.0 * math.pi / 4096.0) for value in rotation)
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    # RotMatrixZYX is equivalent to Rz * Ry * Rx.
    return (
        cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx,
        sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx,
        -sy, cy * sx, cy * cx,
    )


def _pcm_skeleton(path: Path) -> dict[str, object] | None:
    """Read the original PCM model's segment hierarchy when it is available.

    Remaster SRM vertex influences retain the original segment numbers (they
    can be larger than the compact SRM matrix array), while SRA channels are
    indexed by that same G2 segment list.  The PCM is therefore the reliable
    source for parent links and default segment translations.
    """
    try:
        data = path.read_bytes()
        module_offset, _ = _module_offset(data)
        module_size = len(data) - module_offset
        if module_size < 0x10:
            return None

        def u32(relative: int) -> int:
            if relative < 0 or relative + 4 > module_size:
                raise ValueError("PCM skeleton field is outside the module")
            return struct.unpack_from("<I", data, module_offset + relative)[0]

        def checked(relative: int, size: int) -> None:
            if relative <= 0 or relative + size > module_size:
                raise ValueError("PCM skeleton array is outside the module")

        model_count = struct.unpack_from("<H", data, module_offset + 0x08)[0]
        model_list = u32(0x0C)
        if model_count < 1 or model_count > 64:
            return None
        checked(model_list, model_count * 4)
        model_relative = u32(model_list)
        if model_relative + 0x38 > module_size:
            return None
        segment_count = u32(model_relative + 0x18)
        segment_list = u32(model_relative + 0x1C)
        if not 1 <= segment_count <= 256:
            return None
        checked(segment_list, segment_count * 0x18)
        parents: list[int] = []
        positions: list[tuple[float, float, float]] = []
        for index in range(segment_count):
            relative = module_offset + segment_list + index * 0x18
            px, py, pz, parent = struct.unpack_from("<hhhH", data, relative + 0x0C)
            parent = parent if parent != 0xFFFF else -1
            if parent >= segment_count:
                parent = -1
            parents.append(parent)
            positions.append((float(px), float(py), float(pz)))
        return {"segment_count": segment_count, "parents": parents, "positions": positions}
    except (OSError, ValueError, struct.error):
        return None


def _sra_companion_skeleton(path: Path) -> dict[str, object] | None:
    stem = path.stem
    candidates = [path.parent / stem / f"{stem}.PCM", path.parent / f"{stem}.PCM"]
    for candidate in candidates:
        if candidate.is_file():
            skeleton = _pcm_skeleton(candidate)
            if skeleton is not None:
                for srm_candidate in (
                    candidate.parent / f"{stem}_1.SRM",
                    candidate.parent / f"{stem}.SRM",
                    candidate.parent.parent / f"{stem}_1.SRM",
                    candidate.parent.parent / f"{stem}.SRM",
                ):
                    if srm_candidate.is_file():
                        try:
                            srm = _srm_geometry(srm_candidate)
                            positions = srm.get("segment_positions")
                            if isinstance(positions, list) and len(positions) >= int(skeleton["segment_count"]):
                                skeleton["bind_positions"] = positions
                                skeleton["bind_space"] = "srm"
                                break
                        except (OSError, ValueError, struct.error):
                            pass
                return skeleton
    # The extracted object tree is case-sensitive on some platforms.  Keep
    # the fallback bounded to the SRA's directory rather than scanning AREA.
    lowered = stem.lower()
    for candidate in path.parent.rglob("*.PCM"):
        if candidate.stem.lower() == lowered:
            skeleton = _pcm_skeleton(candidate)
            if skeleton is not None:
                return skeleton
    return None


def _mul_affine(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    return (
        a[0] * b[0] + a[1] * b[4] + a[2] * b[8],
        a[0] * b[1] + a[1] * b[5] + a[2] * b[9],
        a[0] * b[2] + a[1] * b[6] + a[2] * b[10],
        a[0] * b[3] + a[1] * b[7] + a[2] * b[11] + a[3],
        a[4] * b[0] + a[5] * b[4] + a[6] * b[8],
        a[4] * b[1] + a[5] * b[5] + a[6] * b[9],
        a[4] * b[2] + a[5] * b[6] + a[6] * b[10],
        a[4] * b[3] + a[5] * b[7] + a[6] * b[11] + a[7],
        a[8] * b[0] + a[9] * b[4] + a[10] * b[8],
        a[8] * b[1] + a[9] * b[5] + a[10] * b[9],
        a[8] * b[2] + a[9] * b[6] + a[10] * b[10],
        a[8] * b[3] + a[9] * b[7] + a[10] * b[11] + a[11],
    )


def _sra_animation_frames(path: Path, clip_index: int, segment_count: int | None = None) -> dict[str, object]:
    """Decode one SRA clip into frame-major affine matrices for the viewer.

    The original runtime applies these values to the companion PCM segment
    hierarchy.  We reconstruct those parent-composed world matrices here; the
    browser then uses frame-zero as the bind pose and applies the animated
    delta to weighted SRM vertices.
    """
    catalog = _sra_animation_catalog(path)
    clips = catalog["clips"]
    if not isinstance(clip_index, int) or clip_index < 0 or clip_index >= len(clips):
        raise ValueError(f"SRA clip index is outside the archive: {clip_index}")
    clip = clips[clip_index]
    data = path.read_bytes()
    module_offset = int(catalog["summary"]["module_offset"])
    module_size = int(catalog["summary"]["module_size"])
    next_keylist = int(clips[clip_index + 1]["keylist_offset"]) if clip_index + 1 < len(clips) else module_size
    section_offsets = clip["section_data_offsets"]
    if not section_offsets:
        raise ValueError("SRA clip has no channel section")
    key_count = int(clip["key_count"])
    active_bits = int(clip["flags"])
    skeleton = _sra_companion_skeleton(path)
    if segment_count is None:
        segment_count = int(skeleton["segment_count"]) if skeleton else 32
    segment_count = max(1, min(int(segment_count), 128))
    channels = _sra_flag_channels(data, module_offset, int(clip["flags_offset"]), active_bits, segment_count)
    blocks = _sra_stream_blocks(
        data, module_offset, int(section_offsets[0]), next_keylist, key_count
    )
    if not blocks:
        raise ValueError("SRA clip contains no decodable channel blocks")

    # Defaults match _G2Anim_InitializeSegValue.  Values are kept as floats;
    # only rotations use G2's fixed 4096-unit angle conversion.
    rotations = [[[0.0, 0.0, 0.0] for _ in range(segment_count)] for _ in range(key_count)]
    scales = [[[1.0, 1.0, 1.0] for _ in range(segment_count)] for _ in range(key_count)]
    bind_positions = skeleton.get("bind_positions") if skeleton else None
    bind_space = skeleton.get("bind_space") if skeleton else None
    if not isinstance(bind_positions, list) or len(bind_positions) < segment_count:
        bind_positions = skeleton["positions"] if skeleton else [(0.0, 0.0, 0.0)] * segment_count
        bind_space = "pcm"
    parents = skeleton["parents"] if skeleton else [-1] + [index - 1 for index in range(1, segment_count)]
    default_positions = []
    for index in range(segment_count):
        parent = int(parents[index]) if index < len(parents) else -1
        current = bind_positions[index]
        if 0 <= parent < segment_count:
            ancestor = bind_positions[parent]
            default_positions.append((current[0] - ancestor[0], current[1] - ancestor[1], current[2] - ancestor[2]))
        else:
            default_positions.append(tuple(current))
    translations = [
        [list(default_positions[index]) for index in range(segment_count)] for _ in range(key_count)
    ]
    decoded_channels = min(len(channels), len(blocks))
    for channel, values in zip(channels[:decoded_channels], blocks[:decoded_channels]):
        group, segment, component = channel
        for frame, value in enumerate(values):
            if group == "rotation":
                rotations[frame][segment][component] = value
            elif group == "scale":
                # Remaster scale channels use the same 4096 baseline as G2.
                scales[frame][segment][component] = value / 4096.0
            else:
                # SRM segment origins are stored as the central-inverted
                # coordinates of the original PCM hierarchy.
                translations[frame][segment][component] = -value if bind_space == "srm" else value

    frames: list[list[tuple[float, ...]]] = []
    for frame_index in range(key_count):
        local: list[tuple[float, ...]] = []
        for segment in range(segment_count):
            r = _sra_rotation_matrix(tuple(rotations[frame_index][segment]))
            sx, sy, sz = scales[frame_index][segment]
            tx, ty, tz = translations[frame_index][segment]
            local.append((
                r[0] * sx, r[1] * sy, r[2] * sz, tx,
                r[3] * sx, r[4] * sy, r[5] * sz, ty,
                r[6] * sx, r[7] * sy, r[8] * sz, tz,
            ))
        frame = []
        for segment, local_matrix in enumerate(local):
            parent = int(parents[segment]) if segment < len(parents) else -1
            frame.append(_mul_affine(frame[parent], local_matrix) if 0 <= parent < segment else local_matrix)
        frames.append(frame)
    warning = None
    if len(channels) != len(blocks):
        warning = f"channel flags describe {len(channels)} channels, stream contains {len(blocks)}; decoded the shared prefix"
    return {
        "format": "soul-re-view-animation-v2",
        "asset_type": "sra",
        "path": path.as_posix(),
        "summary": {
            "clip_index": clip_index,
            "frame_count": key_count,
            "segment_count": segment_count,
            "hierarchy_source": "companion PCM + SRM segment origins" if bind_space == "srm" else ("companion PCM" if skeleton else "identity fallback"),
            "time_per_key": int(clip["time_per_key"]),
            "duration_ms": int(clip["duration_ms"]),
            "active_channel_bits": active_bits,
            "flag_channel_count": len(channels),
            "decoded_channel_count": decoded_channels,
            "stream_block_count": len(blocks),
            "warning": warning,
        },
        "frames": frames,
    }


def _safe_asset(root: Path, requested: str) -> Path:
    candidate = (root / unquote(requested)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("asset path escapes the configured area root") from exc
    if candidate.suffix.lower() not in {".drm", ".pcm"} or not candidate.is_file():
        raise FileNotFoundError(f"DRM/PCM asset not found: {requested}")
    return candidate


def _safe_target(root: Path, requested: str) -> Path:
    """Resolve a writable DRM target without requiring it to exist yet."""
    candidate = (root / unquote(requested)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("write path escapes the configured write root") from exc
    if candidate.suffix.lower() not in {".drm", ".pcm"}:
        raise ValueError("direct writes are limited to DRM/PCM scene assets")
    if not candidate.is_file():
        raise FileNotFoundError(f"writable DRM target not found: {requested}")
    return candidate


def _safe_object_asset(root: Path | None, requested: str) -> Path:
    if root is None:
        raise FileNotFoundError("object asset root is not configured")
    candidate = (root / unquote(requested)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("object asset path escapes the configured object root") from exc
    if candidate.suffix.lower() not in {".pcm", ".srm"} or not candidate.is_file():
        raise FileNotFoundError(f"object PCM/SRM not found: {requested}")
    return candidate


def _safe_animation_asset(root: Path | None, requested: str) -> Path:
    if root is None:
        raise FileNotFoundError("object asset root is not configured")
    candidate = (root / unquote(requested)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("animation asset path escapes the configured object root") from exc
    if candidate.suffix.lower() != ".srl" or not candidate.is_file():
        raise FileNotFoundError(f"SRL animation not found: {requested}")
    return candidate


def _safe_sra_asset(root: Path | None, requested: str) -> Path:
    if root is None:
        raise FileNotFoundError("object asset root is not configured")
    candidate = (root / unquote(requested)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("SRA path escapes the configured object root") from exc
    if candidate.suffix.lower() != ".sra" or not candidate.is_file():
        raise FileNotFoundError(f"SRA animation archive not found: {requested}")
    return candidate


def _safe_texture_asset(root: Path | None, requested: str) -> Path:
    if root is None:
        raise FileNotFoundError("texture root is not configured")
    candidate = (root / unquote(requested)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("texture path escapes the configured texture root") from exc
    if candidate.suffix.lower() not in {".dds", ".png", ".jpg", ".jpeg"} or not candidate.is_file():
        raise FileNotFoundError(f"texture not found: {requested}")
    return candidate


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ViewerHandler(BaseHTTPRequestHandler):
    root: Path
    object_root: Path | None = None
    texture_root: Path | None = None
    write_root: Path | None = None
    default_asset: str = ""
    html: bytes

    def log_message(self, format: str, *args: object) -> None:
        # Keep the terminal useful: API errors are still reported, routine
        # render requests are not printed for every browser refresh.
        if self.path.startswith("/api/") and self.path.split("?", 1)[0] != "/api/list":
            super().log_message(format, *args)

    def _send(self, status: HTTPStatus, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: HTTPStatus, value: object) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(value).encode("utf-8"))

    def _request_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 8 * 1024 * 1024:
            raise ValueError("request body must be between 1 byte and 8 MiB")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request JSON must be an object")
        return payload

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        try:
            if parsed.path in ("", "/"):
                self._send(HTTPStatus.OK, "text/html; charset=utf-8", self.html)
                return
            if parsed.path == "/api/list":
                assets = sorted(
                    path.relative_to(self.root).as_posix()
                    for path in self.root.rglob("*")
                    if path.is_file() and path.suffix.lower() in {".drm", ".pcm"}
                )
                self._json(HTTPStatus.OK, assets)
                return
            if parsed.path in {"/api/drm", "/api/asset"}:
                requested = parse_qs(parsed.query).get("path", [self.default_asset])[0]
                if not requested:
                    raise ValueError("missing DRM/PCM path")
                self._json(HTTPStatus.OK, _geometry(_safe_asset(self.root, requested)))
                return
            if parsed.path == "/api/events":
                requested = parse_qs(parsed.query).get("path", [self.default_asset])[0]
                if not requested:
                    raise ValueError("missing DRM/PCM path")
                self._json(HTTPStatus.OK, _event_report(_safe_asset(self.root, requested)))
                return
            if parsed.path == "/api/object-list":
                if self.object_root is None:
                    self._json(HTTPStatus.OK, [])
                    return
                assets = sorted(
                    path.relative_to(self.object_root).as_posix()
                    for path in self.object_root.rglob("*")
                    if path.is_file() and path.suffix.lower() in {".pcm", ".srm"}
                )
                self._json(HTTPStatus.OK, assets)
                return
            if parsed.path == "/api/animation-list":
                if self.object_root is None:
                    self._json(HTTPStatus.OK, [])
                    return
                assets = sorted(
                    path.relative_to(self.object_root).as_posix()
                    for path in self.object_root.rglob("*")
                    if path.is_file() and path.suffix.lower() == ".srl"
                )
                self._json(HTTPStatus.OK, assets)
                return
            if parsed.path == "/api/sra-list":
                if self.object_root is None:
                    self._json(HTTPStatus.OK, [])
                    return
                assets = sorted(
                    path.relative_to(self.object_root).as_posix()
                    for path in self.object_root.rglob("*")
                    if path.is_file() and path.suffix.lower() == ".sra"
                )
                self._json(HTTPStatus.OK, assets)
                return
            if parsed.path == "/api/object":
                requested = parse_qs(parsed.query).get("path", [""])[0]
                if not requested:
                    raise ValueError("missing object PCM/SRM path")
                source = _safe_object_asset(self.object_root, requested)
                geometry = _pcm_geometry(source) if source.suffix.lower() == ".pcm" else _srm_geometry(source)
                self._json(HTTPStatus.OK, geometry)
                return
            if parsed.path == "/api/animation":
                requested = parse_qs(parsed.query).get("path", [""])[0]
                if not requested:
                    raise ValueError("missing SRL animation path")
                self._json(HTTPStatus.OK, _srl_animation(_safe_animation_asset(self.object_root, requested)))
                return
            if parsed.path == "/api/sra":
                requested = parse_qs(parsed.query).get("path", [""])[0]
                if not requested:
                    raise ValueError("missing SRA animation archive path")
                self._json(HTTPStatus.OK, _sra_animation_catalog(_safe_sra_asset(self.object_root, requested)))
                return
            if parsed.path == "/api/sra-frames":
                requested = parse_qs(parsed.query).get("path", [""])[0]
                if not requested:
                    raise ValueError("missing SRA animation archive path")
                try:
                    clip_index = int(parse_qs(parsed.query).get("clip", ["0"])[0])
                    segment_value = parse_qs(parsed.query).get("segments", [""])[0]
                    segment_count = int(segment_value) if segment_value else None
                except ValueError as exc:
                    raise ValueError("SRA clip and segment values must be integers") from exc
                self._json(
                    HTTPStatus.OK,
                    _sra_animation_frames(
                        _safe_sra_asset(self.object_root, requested), clip_index, segment_count
                    ),
                )
                return
            if parsed.path == "/api/texture-image":
                requested = parse_qs(parsed.query).get("path", [""])[0]
                if not requested:
                    raise ValueError("missing texture path")
                source = _safe_texture_asset(self.texture_root, requested)
                try:
                    from PIL import Image
                except ImportError as exc:
                    raise RuntimeError("texture preview requires Pillow (pip install Pillow)") from exc
                with Image.open(source) as image:
                    image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
                    output = io.BytesIO()
                    image.convert("RGBA").save(output, format="PNG", optimize=True)
                self._send(HTTPStatus.OK, "image/png", output.getvalue())
                return
            if parsed.path == "/api/write-status":
                self._json(
                    HTTPStatus.OK,
                    {
                        "enabled": self.write_root is not None,
                        "root": str(self.write_root) if self.write_root else None,
                    },
                )
                return
            self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n")
        except FileNotFoundError as exc:
            self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except (ValueError, struct.error) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive server boundary
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        try:
            if parsed.path not in {"/api/apply-markers", "/api/apply-placements", "/api/apply-events"}:
                self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n")
                return
            if self.write_root is None:
                self._json(HTTPStatus.CONFLICT, {"error": "direct writes are disabled; restart with --write-root"})
                return
            payload = self._request_json()
            requested = payload.get("path")
            markers = payload.get("markers")
            if not isinstance(requested, str) or not requested:
                raise ValueError("path must be a non-empty string")
            source = _safe_asset(self.root, requested)
            target = _safe_target(self.write_root, requested)
            before_sha1 = _sha1(target)
            with tempfile.TemporaryDirectory(prefix="soul-re-viewer-") as temp_dir:
                temp_dir_path = Path(temp_dir)
                patched_path = temp_dir_path / target.name
                if parsed.path == "/api/apply-events":
                    changed = _patch_events(source, payload, patched_path)
                    added = []
                else:
                    source_geometry = _geometry(source)
                    if source_geometry.get("asset_type") not in {"drm", "pcm-level"}:
                        raise ValueError("direct writes are limited to DRM/PCM scene assets")
                    if parsed.path == "/api/apply-markers":
                        markers = payload.get("markers")
                        if not isinstance(markers, list):
                            raise ValueError("markers must be an array")
                        scene_path = temp_dir_path / "scene.json"
                        scene_payload = {"format": "soul-re-scene-v1", "source": requested, "markers": markers}
                        scene_path.write_text(json.dumps(scene_payload), encoding="utf-8")
                        changed = _patch_scene(source, scene_path, patched_path)
                        added = []
                    else:
                        placements = payload.get("placements")
                        if not isinstance(placements, list):
                            raise ValueError("placements must be an array")
                        added = _append_scene_placements(source, placements, patched_path)
                        changed = len(added)
                after_sha1 = _sha1(patched_path)
                if before_sha1 == after_sha1:
                    self._json(HTTPStatus.OK, {"path": requested, "changed": 0, "added": added, "written": False, "sha1": before_sha1})
                    return
                timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
                backup = target.with_name(f"{target.name}.bak-{timestamp}")
                suffix = 1
                while backup.exists():
                    backup = target.with_name(f"{target.name}.bak-{timestamp}-{suffix}")
                    suffix += 1
                shutil.copy2(target, backup)
                # The build temp directory may be on C: while the writable
                # AREA is commonly on another drive.  Stage the final copy
                # beside the target so os.replace remains same-volume and
                # atomic on Windows.
                staged_path: Path | None = None
                try:
                    descriptor, staged_name = tempfile.mkstemp(
                        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
                    )
                    os.close(descriptor)
                    staged_path = Path(staged_name)
                    shutil.copyfile(patched_path, staged_path)
                    os.replace(staged_path, target)
                    staged_path = None
                finally:
                    if staged_path is not None:
                        try:
                            staged_path.unlink()
                        except FileNotFoundError:
                            pass
            self._json(
                HTTPStatus.OK,
                {
                    "path": requested,
                    "changed": changed,
                    "added": added,
                    "written": True,
                    "target": str(target),
                    "backup": str(backup),
                    "before_sha1": before_sha1,
                    "after_sha1": after_sha1,
                },
            )
        except FileNotFoundError as exc:
            self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError, struct.error) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive write boundary
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "game" / "bigfile" / "kain2" / "area",
        help="extracted BIGFILE area root (default: game/bigfile/kain2/area)",
    )
    parser.add_argument(
        "--drm",
        default="city/bin/city2.drm",
        help="initial DRM or PCM path relative to --root (legacy option name)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="listen address")
    parser.add_argument("--port", type=int, default=8765, help="listen port (0 chooses a free port)")
    parser.add_argument("--open", action="store_true", help="open the viewer in the default browser")
    parser.add_argument(
        "--write-root",
        type=Path,
        help="explicit root for Apply-to-AREA scene writes; disabled unless supplied",
    )
    parser.add_argument(
        "--object-root",
        type=Path,
        help="root containing standalone object PCMs (default: sibling OBJECT directory)",
    )
    parser.add_argument(
        "--texture-root",
        type=Path,
        help="root containing remaster DDS textures (default: sibling TEX directory)",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if not root.is_dir():
        print(f"asset root does not exist: {root}", file=sys.stderr)
        return 2
    write_root = args.write_root.resolve() if args.write_root else None
    if write_root and not write_root.is_dir():
        print(f"write root does not exist: {write_root}", file=sys.stderr)
        return 2
    object_root = args.object_root.resolve() if args.object_root else root.parent / "OBJECT"
    if not object_root.is_dir():
        object_root = None
    texture_root = args.texture_root.resolve() if args.texture_root else root.parent / "TEX"
    if not texture_root.is_dir():
        texture_root = None
    try:
        initial = _safe_asset(root, args.drm)
    except (FileNotFoundError, ValueError):
        initial = None

    html_path = Path(__file__).with_name("level_viewer.html")
    html = html_path.read_bytes()
    handler = type("ConfiguredViewerHandler", (ViewerHandler,), {})
    handler.root = root
    handler.object_root = object_root
    handler.texture_root = texture_root
    handler.write_root = write_root
    handler.default_asset = initial.relative_to(root).as_posix() if initial else ""
    handler.html = html
    server = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{server.server_port}/?asset={quote(handler.default_asset)}"
    print(f"Soul Reaver 3D viewer: {url}")
    print(f"Asset root: {root}")
    print(f"Object root: {object_root if object_root else '<not configured; use --object-root>'}")
    print(f"Texture root: {texture_root if texture_root else '<not configured; use --texture-root>'}")
    print(f"Write root: {write_root if write_root else '<disabled; use --write-root to enable>'}")
    print("Press Ctrl+C to stop.")
    if args.open:
        threading.Timer(0.15, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping viewer.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
