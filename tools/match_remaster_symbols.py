#!/usr/bin/env python3
"""Match PDB symbol signatures from a reference build to another PE.

The Remaster PDB bundle and the installed Steam DLL have the same CodeView
GUID but different PDB ages.  This tool therefore treats the PDB RVAs as
reference-only and searches the target .text section for each function's
leading bytes.  It never writes to either binary.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import pefile


def read_text(pe: pefile.PE):
    for section in pe.sections:
        name = section.Name.rstrip(b"\0")
        if name == b".text":
            return section.VirtualAddress, pe.get_data(section.VirtualAddress, section.SizeOfRawData)
    raise ValueError("PE has no .text section")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--symbols", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bytes", type=int, default=16, dest="byte_count")
    parser.add_argument("--limit", type=int, default=10000)
    args = parser.parse_args()

    ref = pefile.PE(str(args.reference))
    target = pefile.PE(str(args.target))
    ref_text_rva, ref_text = read_text(ref)
    target_text_rva, target_text = read_text(target)

    rows = []
    with args.symbols.open(newline="", encoding="utf-8") as fp:
        for row in csv.DictReader(fp):
            if len(rows) >= args.limit:
                break
            if not row.get("rva") or row.get("symtype") != "2":
                continue
            rva = int(row["rva"])
            if not (ref_text_rva <= rva < ref_text_rva + len(ref_text)):
                continue
            ref_off = rva - ref_text_rva
            signature = ref_text[ref_off : ref_off + args.byte_count]
            if len(signature) < args.byte_count:
                continue
            hits = []
            start = 0
            while True:
                pos = target_text.find(signature, start)
                if pos < 0:
                    break
                hits.append(target_text_rva + pos)
                start = pos + 1
            rows.append(
                {
                    "name": row["name"],
                    "reference_rva": hex(rva),
                    "target_rvas": ";".join(hex(x) for x in hits),
                    "match_count": len(hits),
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=["name", "reference_rva", "target_rvas", "match_count"])
        writer.writeheader()
        writer.writerows(rows)
    unique = sum(row["match_count"] == 1 for row in rows)
    print(f"wrote {len(rows)} symbol matches; {unique} unique")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
