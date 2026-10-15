#!/usr/bin/env python3
"""Serve an interactive, dependency-free WebGL viewer for extracted DRM levels."""

from __future__ import annotations

import argparse
import json
import struct
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from level_editor import _module_offset, _parse_drm, _parse_scene_markers


def _geometry(path: Path) -> dict[str, object]:
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
        "path": path.as_posix(),
        "summary": _parse_drm(path).__dict__,
        "vertices": vertices,
        "faces": faces,
        "markers": [marker.__dict__ for marker in _parse_scene_markers(path)],
    }


def _safe_asset(root: Path, requested: str) -> Path:
    candidate = (root / unquote(requested)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("asset path escapes the configured area root") from exc
    if candidate.suffix.lower() != ".drm" or not candidate.is_file():
        raise FileNotFoundError(f"DRM asset not found: {requested}")
    return candidate


class ViewerHandler(BaseHTTPRequestHandler):
    root: Path
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

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        try:
            if parsed.path in ("", "/"):
                self._send(HTTPStatus.OK, "text/html; charset=utf-8", self.html)
                return
            if parsed.path == "/api/list":
                assets = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*.drm"))
                self._json(HTTPStatus.OK, assets)
                return
            if parsed.path == "/api/drm":
                requested = parse_qs(parsed.query).get("path", [self.default_asset])[0]
                if not requested:
                    raise ValueError("missing DRM path")
                self._json(HTTPStatus.OK, _geometry(_safe_asset(self.root, requested)))
                return
            self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n")
        except FileNotFoundError as exc:
            self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except (ValueError, struct.error) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - defensive server boundary
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "game" / "bigfile" / "kain2" / "area",
        help="extracted BIGFILE area root (default: game/bigfile/kain2/area)",
    )
    parser.add_argument("--drm", default="city/bin/city2.drm", help="initial DRM path relative to --root")
    parser.add_argument("--host", default="127.0.0.1", help="listen address")
    parser.add_argument("--port", type=int, default=8765, help="listen port (0 chooses a free port)")
    parser.add_argument("--open", action="store_true", help="open the viewer in the default browser")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if not root.is_dir():
        print(f"asset root does not exist: {root}", file=sys.stderr)
        return 2
    try:
        initial = _safe_asset(root, args.drm)
    except (FileNotFoundError, ValueError):
        initial = None

    html_path = Path(__file__).with_name("level_viewer.html")
    html = html_path.read_bytes()
    handler = type("ConfiguredViewerHandler", (ViewerHandler,), {})
    handler.root = root
    handler.default_asset = initial.relative_to(root).as_posix() if initial else ""
    handler.html = html
    server = ThreadingHTTPServer((args.host, args.port), handler)
    url = f"http://{args.host}:{server.server_port}/?drm={quote(handler.default_asset)}"
    print(f"Soul Reaver 3D viewer: {url}")
    print(f"Asset root: {root}")
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
