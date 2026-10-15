#!/usr/bin/env python3
"""Serve an interactive, dependency-free WebGL viewer for extracted DRM/PCM assets."""

from __future__ import annotations

import argparse
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

from level_editor import (
    _append_scene_placements,
    _module_offset,
    _parse_drm,
    _parse_scene_markers,
    _patch_scene,
)


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
    return {
        "format": "soul-re-view-v1",
        "asset_type": "drm",
        "path": path.as_posix(),
        "summary": _parse_drm(path).__dict__,
        "vertices": vertices,
        "faces": faces,
        "markers": [marker.__dict__ for marker in _parse_scene_markers(path)],
    }


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
    skip((32 + extra_bone_count) * 48, "bone matrices")
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
    bounds_min = [float("inf"), float("inf"), float("inf")]
    bounds_max = [float("-inf"), float("-inf"), float("-inf")]
    for _ in range(vertex_count):
        x, y, z = struct.unpack_from("<3f", data, offset)
        offset += 32
        if not all(math.isfinite(value) for value in (x, y, z)):
            raise ValueError("SRM contains a non-finite vertex")
        vertices.append((x, y, z))
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
        "extra_bone_count": extra_bone_count,
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
        "markers": [],
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


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ViewerHandler(BaseHTTPRequestHandler):
    root: Path
    object_root: Path | None = None
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
            if parsed.path == "/api/object":
                requested = parse_qs(parsed.query).get("path", [""])[0]
                if not requested:
                    raise ValueError("missing object PCM/SRM path")
                source = _safe_object_asset(self.object_root, requested)
                geometry = _pcm_geometry(source) if source.suffix.lower() == ".pcm" else _srm_geometry(source)
                self._json(HTTPStatus.OK, geometry)
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
            if parsed.path not in {"/api/apply-markers", "/api/apply-placements"}:
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
            source_geometry = _geometry(source)
            if source_geometry.get("asset_type") not in {"drm", "pcm-level"}:
                raise ValueError("direct writes are limited to DRM/PCM scene assets")
            target = _safe_target(self.write_root, requested)
            before_sha1 = _sha1(target)
            with tempfile.TemporaryDirectory(prefix="soul-re-viewer-") as temp_dir:
                temp_dir_path = Path(temp_dir)
                patched_path = temp_dir_path / target.name
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
                os.replace(patched_path, target)
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
    try:
        initial = _safe_asset(root, args.drm)
    except (FileNotFoundError, ValueError):
        initial = None

    html_path = Path(__file__).with_name("level_viewer.html")
    html = html_path.read_bytes()
    handler = type("ConfiguredViewerHandler", (ViewerHandler,), {})
    handler.root = root
    handler.object_root = object_root
    handler.write_root = write_root
    handler.default_asset = initial.relative_to(root).as_posix() if initial else ""
    handler.html = html
    server = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{server.server_port}/?asset={quote(handler.default_asset)}"
    print(f"Soul Reaver 3D viewer: {url}")
    print(f"Asset root: {root}")
    print(f"Object root: {object_root if object_root else '<not configured; use --object-root>'}")
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
