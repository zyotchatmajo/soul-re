#!/usr/bin/env python3
"""Extract local and global CodeView procedure symbols from a PDB."""

from __future__ import annotations

import argparse
import csv
import struct
import sys
from pathlib import Path


PROC_TYPES = {
    0x110F: ("S_LPROC32", 35),
    0x1110: ("S_GPROC32", 35),
    0x1146: ("S_LPROC32_ID", 35),
    0x1147: ("S_GPROC32_ID", 35),
    # Newer MSVC builds use the DPC / v2 procedure record variants as well.
    0x1141: ("S_LPROC32_DPC", 35),
    0x1142: ("S_GPROC32_DPC", 35),
    0x1143: ("S_LPROC32_DPC_ID", 35),
    0x1144: ("S_GPROC32_DPC_ID", 35),
    0x1145: ("S_LPROC32_DPC_ID", 35),
}


def load_pdb(path: Path):
    try:
        import pdbparse  # type: ignore
    except ImportError as exc:
        raise SystemExit("pdbparse is required; use Python 3.11 with construct 2.9.x") from exc
    pdb = pdbparse.parse(str(path), fast_load=True)
    pdb.STREAM_DBI.load()
    pdb._update_names()
    pdb.STREAM_SECT_HDR = pdb.STREAM_SECT_HDR.reload()
    pdb.STREAM_SECT_HDR.load()
    return pdb


def section_rva(pdb, segment: int, offset: int):
    sections = getattr(pdb.STREAM_SECT_HDR, "sections", ())
    if not segment or segment > len(sections):
        return None
    return int(sections[segment - 1].VirtualAddress) + offset


def procedures(pdb):
    result = []
    for module_index, header in enumerate(pdb.STREAM_DBI.DBIExHeaders):
        stream_index = int(header.stream)
        if stream_index <= 0 or stream_index >= len(pdb.streams):
            continue
        data = pdb.streams[stream_index].data
        pos = 0
        while pos + 4 <= len(data):
            length, kind = struct.unpack_from("<HH", data, pos)
            # CodeView stores the total record size in `length`, including
            # the two-byte kind field but excluding the size field itself.
            end = pos + 2 + length
            # Some optimized MSVC module streams begin with a four-byte
            # padding record whose size includes its header.  Real records
            # are aligned after that padding; accept both forms below.
            if pos == 0 and length == 4 and kind == 0:
                end = pos + length
            if end > len(data) or length < 2:
                break
            spec = PROC_TYPES.get(kind)
            if spec:
                kind_name, name_offset = spec
                payload = data[pos + 4 : end]
                if len(payload) >= name_offset:
                    offset = struct.unpack_from("<I", payload, 28)[0]
                    segment = struct.unpack_from("<H", payload, 32)[0]
                    name = payload[name_offset:].split(b"\0", 1)[0].decode("utf-8", "replace")
                    if name:
                        result.append({
                            "module": module_index,
                            "module_name": str(getattr(header, "modName", "")),
                            "kind": kind_name,
                            "name": name,
                            "segment": segment,
                            "offset": offset,
                            "rva": section_rva(pdb, segment, offset),
                        })
            pos = end
    result.sort(key=lambda x: (x["rva"] is None, x["rva"] or 0, x["name"]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdb", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = procedures(load_pdb(args.pdb))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=["module", "module_name", "kind", "name", "segment", "offset", "rva"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} procedure symbols to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
