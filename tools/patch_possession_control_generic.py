#!/usr/bin/env python3
"""Route possession control through the generic instance process dispatcher.

The first physics shim hooked MonsterProcess directly.  The possession target
can use a different process callback, so this version hooks the common
INSTANCE_ProcessFunctions indirect call instead.  The original callback is
always invoked first; only the selected instance receives the extra native
monster-physics step.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import pefile


CALL_RVA = 0x7A33E
# call rax followed by the complete movsx instruction that must be relocated
CALL_EXPECTED = bytes.fromhex("FF D0 0F BF 83 B6 00 00 00")
CONTINUE_RVA = 0x7A347
SELECTED_HOST_GLOBAL_RVA = 0x2E8DE9
PHYSICS_SET_VEL_RVA = 0xBB970
PHYSICS_MOVE_RVA = 0xBB610
PLAY_ANIMATION_RVA = 0x10CF70
TIME_MULT_GLOBAL_RVA = 0x2A89628


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


class Builder:
    def __init__(self, base: int):
        self.base = base
        self.buf = bytearray()
        self.fixups: list[tuple[int, int]] = []

    def emit(self, data: bytes) -> None:
        self.buf += data

    def rel32(self, target: int) -> None:
        pos = len(self.buf)
        self.buf += b"\x00\x00\x00\x00"
        self.fixups.append((pos, target))

    def finish(self) -> bytes:
        out = bytearray(self.buf)
        for pos, target in self.fixups:
            out[pos : pos + 4] = struct.pack("<i", target - (self.base + pos + 4))
        return bytes(out)


def append_section(raw: bytearray, pe: pefile.PE, payload: bytes) -> int:
    last = max(pe.sections, key=lambda s: s.VirtualAddress + s.Misc_VirtualSize)
    rva = align(last.VirtualAddress + last.Misc_VirtualSize, pe.OPTIONAL_HEADER.SectionAlignment)
    raw_ptr = align(len(raw), pe.OPTIONAL_HEADER.FileAlignment)
    raw_size = align(len(payload), pe.OPTIONAL_HEADER.FileAlignment)
    raw.extend(b"\0" * (raw_ptr - len(raw)))
    raw.extend(payload)
    raw.extend(b"\0" * (raw_size - len(payload)))

    nt = pe.DOS_HEADER.e_lfanew
    table = nt + 4 + 20 + pe.FILE_HEADER.SizeOfOptionalHeader
    header = table + pe.FILE_HEADER.NumberOfSections * 40
    raw[header : header + 40] = struct.pack(
        "<8sIIIIIIHHI", b".psg\0\0\0\0", len(payload), rva, raw_size, raw_ptr, 0, 0, 0, 0, 0xE0000020
    )
    struct.pack_into("<H", raw, nt + 4 + 2, pe.FILE_HEADER.NumberOfSections + 1)
    struct.pack_into("<I", raw, nt + 4 + 20 + 56, align(rva + len(payload), pe.OPTIONAL_HEADER.SectionAlignment))
    return rva


def build_wrapper(rva: int, force_forward: bool) -> bytes:
    b = Builder(rva)
    # Preserve the caller's nonvolatile R12 and reserve a stack local for the
    # GameTracker pointer while the original process callback is running.
    b.emit(b"\x41\x54")  # push r12
    # The wrapper is now the caller of the original callback, so provide the
    # mandatory 32-byte Windows x64 home/shadow space plus one 8-byte local.
    b.emit(b"\x48\x83\xec\x30")  # sub rsp, 0x30
    b.emit(b"\x48\x89\x54\x24\x20")  # mov [rsp+0x20],rdx
    b.emit(b"\xff\xd0")  # call rax (original process callback)

    # Only the selected instance receives control.
    b.emit(b"\x48\x8b\x05")
    b.emit(struct.pack("<i", SELECTED_HOST_GLOBAL_RVA - (rva + len(b.buf) + 4)))
    b.emit(b"\x48\x39\xd8")  # cmp rax,rbx
    b.emit(b"\x0f\x85")
    skip_disp_pos = len(b.buf)
    b.emit(b"\0\0\0\0")

    # Inline controlCommand is at GameTracker + 0x98 (the wrapper used by
    # RazielProcess obtains it with LEA, not with an extra pointer load).
    b.emit(b"\x48\x8b\x44\x24\x20")  # mov rax,[rsp+0x20]
    b.emit(b"\x48\x8d\x80\x98\x00\x00\x00")  # lea rax,[rax+0x98]
    b.emit(b"\x44\x8b\x00")
    if force_forward:
        b.emit(b"\x90" * 7)
    else:
        b.emit(b"\x41\xf7\xc0\x03\x00\x00\x00")
        b.emit(b"\x0f\x84")
        no_move_pos = len(b.buf)
        b.emit(b"\0\0\0\0")

    # Turn with the left/right bits, then use MonsterVars->speed (+0x238) and
    # the remaster's native collision movement functions.
    b.emit(b"\x41\xf6\xc0\x04\x0f\x84\x09\x00\x00\x00")
    b.emit(b"\x66\x81\x83\xd2\x00\x00\x00\x80\x00")
    b.emit(b"\x41\xf6\xc0\x08\x0f\x84\x09\x00\x00\x00")
    b.emit(b"\x66\x81\xab\xd2\x00\x00\x00\x80\x00")
    b.emit(b"\x48\x8b\x8b\xc0\x02\x00\x00")
    # MonsterVars is in RCX after the extraData load above.  The old REX.B
    # prefix accidentally addressed R9, so the shim read an unrelated pointer.
    b.emit(b"\x44\x0f\xbf\x81\x38\x02\x00\x00")
    b.emit(b"\x41\xf6\xc0\x02\x0f\x84\x03\x00\x00\x00")
    b.emit(b"\x41\xf7\xd8")
    b.emit(b"\x48\x8b\xcb\x48\x8d\x93\xce\x00\x00\x00\xe8")
    b.rel32(PHYSICS_SET_VEL_RVA)
    b.emit(b"\x44\x8b\x05")
    b.emit(struct.pack("<i", TIME_MULT_GLOBAL_RVA - (rva + len(b.buf) + 4)))
    b.emit(b"\x48\x8d\x93\x9c\x00\x00\x00\x48\x8b\xcb\xe8")
    b.rel32(PHYSICS_MOVE_RVA)
    b.emit(b"\x48\x8b\xcb\xe8")
    b.rel32(PLAY_ANIMATION_RVA)

    # Relocated instruction from the original dispatcher, then continue after
    # the nine bytes replaced at the call site.
    skip_target = rva + len(b.buf)
    b.emit(b"\x0f\xbf\x83\xb6\x00\x00\x00")
    b.emit(b"\x48\x83\xc4\x30")  # add rsp, 0x30
    b.emit(b"\x41\x5c")
    b.emit(b"\xe9")
    b.rel32(CONTINUE_RVA)
    out = bytearray(b.finish())

    # The selected check's short-independent JNE points to the relocated tail.
    out[skip_disp_pos : skip_disp_pos + 4] = struct.pack("<i", skip_target - (rva + skip_disp_pos + 4))
    if not force_forward:
        # The no-move branch lands at the relocated tail.
        no_move_target = skip_target
        out[no_move_pos : no_move_pos + 4] = struct.pack("<i", no_move_target - (rva + no_move_pos + 4))
    return bytes(out)


def patch(input_path: Path, output_path: Path, force_forward: bool) -> None:
    pe = pefile.PE(str(input_path), fast_load=True)
    raw = bytearray(input_path.read_bytes())
    off = pe.get_offset_from_rva(CALL_RVA)
    if bytes(raw[off : off + len(CALL_EXPECTED)]) != CALL_EXPECTED:
        raise SystemExit("generic process call guard failed")
    last = max(pe.sections, key=lambda s: s.VirtualAddress + s.Misc_VirtualSize)
    section_rva = align(last.VirtualAddress + last.Misc_VirtualSize, pe.OPTIONAL_HEADER.SectionAlignment)
    payload = build_wrapper(section_rva, force_forward)
    new_rva = append_section(raw, pe, payload)
    raw[off : off + len(CALL_EXPECTED)] = b"\xe8" + struct.pack("<i", new_rva - (CALL_RVA + 5)) + b"\x90" * 4
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(raw)
    print(f"wrote {output_path}; .psg RVA 0x{new_rva:X}, {len(payload)} bytes")
    print("diagnostic mode: forced forward" if force_forward else "generic process callback control enabled")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--force-forward", action="store_true")
    args = ap.parse_args()
    patch(args.input, args.output, args.force_forward)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
