#!/usr/bin/env python3
"""Pack an extracted Soul Reaver BIGFILE with loader-compatible checksums.

The upstream cd-dat-utils packer preserves checksum values from the original
structure YAML. That is correct for an unchanged tree, but edited streamed
assets need their 32-bit word-sum refreshed because the game verifies it while
loading. This wrapper keeps the upstream container layout and recalculates the
checksum field for every aligned file before packing.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from cd_dat_utils.core.config import Config
from cd_dat_utils.core.dat import BigFile, from_unpacked, pack_bigfile


def checksum(data: bytes) -> int:
    """Match LOAD_UpdateCheckSum: sum little-endian 32-bit words modulo 2^32."""
    if len(data) % 4:
        raise ValueError(f"asset length {len(data)} is not 32-bit aligned")
    if not data:
        return 0
    words = struct.unpack(f"<{len(data) // 4}I", data)
    return sum(words) & 0xFFFFFFFF


def recompute_checksums(bigfile: BigFile) -> int:
    changed = 0
    for folder in bigfile.folder_list:
        for entry in folder.file_list:
            if entry.contents is None:
                continue
            new_checksum = checksum(entry.contents)
            if entry.checksum != new_checksum:
                entry.checksum = new_checksum
                changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="cd-dat-utils YAML config")
    parser.add_argument("--input", type=Path, required=True, help="extracted BIGFILE directory")
    parser.add_argument("--output", type=Path, required=True, help="new BIGFILE.DAT path")
    args = parser.parse_args()

    config = Config.from_yaml(str(args.config))
    if config.bigfile is None:
        parser.error("configuration does not contain a bigfile section")
    bigfile = from_unpacked(str(args.input), config.bigfile)
    changed = recompute_checksums(bigfile)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pack_bigfile(bigfile, str(args.output))
    print(f"Packed {args.output} ({args.output.stat().st_size:,} bytes)")
    print(f"Checksums refreshed: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
