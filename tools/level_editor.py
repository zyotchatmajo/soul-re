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


SUPPORTED_EXTENSIONS = {".drm", ".crm", ".smf", ".snf", ".tim", ".smp", ".snd"}
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
            if args.obj:
                _export_obj(inspect_path, args.obj)
                print(f"OBJ: {args.obj} ({args.obj.stat().st_size:,} bytes)")
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
