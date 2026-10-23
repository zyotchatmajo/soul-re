#!/usr/bin/env python3
"""Extract a searchable symbol inventory from a Remaster PDB.

This is intentionally read-only: it parses the PDB and writes a CSV/JSON
inventory without touching the game binaries.  The PDB package is used only
for symbol metadata; disassembly and runtime patching are separate steps.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def load_pdb(path: Path):
    try:
        import pdbparse  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment guidance
        raise SystemExit(
            "pdbparse is required; use Python 3.11 with construct 2.9.x"
        ) from exc

    pdb = pdbparse.parse(str(path), fast_load=True)
    pdb.STREAM_DBI.load()
    pdb._update_names()
    pdb.STREAM_GSYM = pdb.STREAM_GSYM.reload()
    if pdb.STREAM_GSYM.size:
        pdb.STREAM_GSYM.load()
    pdb.STREAM_SECT_HDR = pdb.STREAM_SECT_HDR.reload()
    pdb.STREAM_SECT_HDR.load()
    return pdb


def section_rva(pdb, segment: int, offset: int) -> int | None:
    sections = getattr(pdb.STREAM_SECT_HDR, "sections", ())
    if not segment or segment > len(sections):
        return None
    return int(sections[segment - 1].VirtualAddress) + int(offset)


def collect(pdb):
    result = []
    for sym in getattr(pdb.STREAM_GSYM, "globals", ()):
        name = str(getattr(sym, "name", "") or "")
        if not name:
            continue
        segment = int(getattr(sym, "segment", 0) or 0)
        offset = int(getattr(sym, "offset", 0) or 0)
        result.append(
            {
                "name": name,
                "segment": segment,
                "offset": offset,
                "rva": section_rva(pdb, segment, offset),
                "symtype": int(getattr(sym, "symtype", 0) or 0),
                "leaf_type": int(getattr(sym, "leaf_type", 0) or 0),
            }
        )
    result.sort(key=lambda row: (row["rva"] is None, row["rva"] or 0, row["name"]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdb", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json", action="store_true", help="write JSON instead of CSV")
    args = parser.parse_args()

    rows = collect(load_pdb(args.pdb))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.json:
        args.output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    else:
        with args.output.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=["name", "segment", "offset", "rva", "symtype", "leaf_type"])
            writer.writeheader()
            writer.writerows(rows)
    print(f"wrote {len(rows)} named symbols to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
