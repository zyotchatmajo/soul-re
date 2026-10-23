#!/usr/bin/env python3
"""Match optimized CodeView procedures across Remaster DLL builds.

Relative calls/jumps and RIP-relative data references are masked because the
installed Steam DLL was built at a different PDB age.  The resulting matches
are candidates for review, not automatic patches.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import capstone
import pefile


def text_section(pe):
    for section in pe.sections:
        if section.Name.rstrip(b"\0") == b".text":
            return section.VirtualAddress, pe.get_data(section.VirtualAddress, section.SizeOfRawData)
    raise ValueError("PE has no .text section")


def masked_signature(data: bytes, rva: int, length: int):
    offset = rva
    window = bytearray(data[offset : offset + length])
    mask = bytearray(b"\x01" * len(window))
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    for insn in md.disasm(bytes(window), rva):
        fields = []
        if insn.imm_size and insn.mnemonic in {
            "call", "jmp", "ja", "jae", "jb", "jbe", "jc", "je", "jg", "jge",
            "jl", "jle", "jna", "jnae", "jnb", "jnbe", "jnc", "jne", "jng",
            "jnge", "jnl", "jnle", "jno", "jnp", "jns", "jnz", "jo", "jp",
            "jpe", "jpo", "js", "jz",
        }:
            fields.append((insn.imm_offset, insn.imm_size))
        if insn.disp_size and any(op.type == capstone.x86.X86_OP_MEM and op.mem.base == capstone.x86.X86_REG_RIP for op in insn.operands):
            fields.append((insn.disp_offset, insn.disp_size))
        for start, size in fields:
            for i in range(start, start + size):
                if 0 <= insn.address - rva + i < len(mask):
                    mask[insn.address - rva + i] = 0
        if insn.address + insn.size >= rva + length:
            break
    return bytes(window), bytes(mask)


def find_all(haystack: bytes, pattern: bytes, mask: bytes):
    end = len(haystack) - len(pattern) + 1
    for start in range(max(0, end)):
        if all(not mask[i] or haystack[start + i] == pattern[i] for i in range(len(pattern))):
            yield start


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--procedures", type=Path, required=True)
    parser.add_argument("--name", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bytes", type=int, default=96)
    args = parser.parse_args()

    ref = pefile.PE(str(args.reference))
    target = pefile.PE(str(args.target))
    ref_text_rva, ref_text = text_section(ref)
    target_text_rva, target_text = text_section(target)
    wanted = set(args.name)
    rows = []
    with args.procedures.open(newline="", encoding="utf-8") as fp:
        for row in csv.DictReader(fp):
            if row["name"] not in wanted:
                continue
            rva = int(row["rva"])
            if not (ref_text_rva <= rva < ref_text_rva + len(ref_text)):
                continue
            pattern, mask = masked_signature(ref_text, rva - ref_text_rva, args.bytes)
            hits = [target_text_rva + x for x in find_all(target_text, pattern, mask)]
            rows.append({"name": row["name"], "reference_rva": hex(rva), "target_rvas": ";".join(hex(x) for x in hits), "match_count": len(hits)})

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=["name", "reference_rva", "target_rvas", "match_count"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} procedure candidates to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
