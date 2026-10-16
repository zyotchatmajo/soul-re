#!/usr/bin/env python3
"""Inspect and extract the fixed-page .BIG bundles used by SR1 Remastered.

The remaster bundles are not the original Soul Reaver BIGFILE format.  They
start with a little-endian version/page-count pair and then contain a 4 KiB
header followed by 128 KiB raw texture pages (256x256, 16-bit RGB565 data).
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


HEADER_SIZE = 0x1000
PAGE_SIZE = 0x20000
PAGE_WIDTH = 256
PAGE_HEIGHT = 256
VERSION = 0x0200


def read_bundle(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    if len(data) < HEADER_SIZE:
        raise ValueError(f"bundle is shorter than the {HEADER_SIZE:#x}-byte header")
    version, page_count = struct.unpack_from("<HH", data, 0)
    if version != VERSION:
        raise ValueError(f"unsupported remaster BIG version: {version:#x}")
    expected_size = HEADER_SIZE + page_count * PAGE_SIZE
    if len(data) != expected_size:
        raise ValueError(
            f"page table is inconsistent: header says {page_count} pages, "
            f"expected {expected_size:,} bytes, got {len(data):,}"
        )
    return version, page_count, data


def write_png(page: bytes, destination: Path) -> None:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("PNG extraction requires Pillow (pip install Pillow)") from exc
    pixels = bytearray(PAGE_WIDTH * PAGE_HEIGHT * 3)
    for index in range(PAGE_WIDTH * PAGE_HEIGHT):
        value = page[index * 2] | (page[index * 2 + 1] << 8)
        pixels[index * 3] = ((value >> 11) & 0x1F) * 255 // 31
        pixels[index * 3 + 1] = ((value >> 5) & 0x3F) * 255 // 63
        pixels[index * 3 + 2] = (value & 0x1F) * 255 // 31
    Image.frombytes("RGB", (PAGE_WIDTH, PAGE_HEIGHT), bytes(pixels)).save(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="remaster .BIG file")
    parser.add_argument("--extract", type=Path, help="directory for raw page files")
    parser.add_argument("--png", action="store_true", help="also write RGB565 pages as PNG")
    args = parser.parse_args()

    version, page_count, data = read_bundle(args.bundle)
    print(f"version: {version:#06x}")
    print(f"pages: {page_count}")
    print(f"page size: {PAGE_SIZE:,} bytes ({PAGE_WIDTH}x{PAGE_HEIGHT}, RGB565)")
    print(f"data offset: {HEADER_SIZE:#x}")
    print(f"file size: {len(data):,} bytes")
    if args.png and not args.extract:
        parser.error("--png requires --extract")
    if args.extract:
        args.extract.mkdir(parents=True, exist_ok=True)
        for index in range(page_count):
            page = data[HEADER_SIZE + index * PAGE_SIZE : HEADER_SIZE + (index + 1) * PAGE_SIZE]
            raw_path = args.extract / f"page_{index:04d}.rgb565"
            raw_path.write_bytes(page)
            if args.png:
                write_png(page, args.extract / f"page_{index:04d}.png")
        print(f"extracted {page_count} pages to {args.extract}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
