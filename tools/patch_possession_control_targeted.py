#!/usr/bin/env python3
"""Redirect only the currently possessed instance to a control callback.

This leaves INSTANCE_ProcessFunctions untouched.  The possession selector is
wrapped, and after the original selector chooses its host, that host's
processFunc field (+0x198) is replaced with a small callback.  The previous
host is restored when possession changes.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import pefile


TRIGGER_RVA = 0xE55FA
TRIGGER_EXPECTED = bytes.fromhex("E8 C1 76 02 00")
POSSESS_NEXT_RVA = 0x98D60
TRIGGER_POC_EXPECTED = b"\xe8" + struct.pack("<i", POSSESS_NEXT_RVA - (TRIGGER_RVA + 5))
SELECTED_HOST_GLOBAL_RVA = 0x2E8DE9
PHYSICS_SET_VEL_RVA = 0xBB970
PHYSICS_MOVE_RVA = 0xBB610
PLAY_ANIMATION_RVA = 0x10CF70
TIME_MULT_GLOBAL_RVA = 0x2A89628


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


class Emitter:
    def __init__(self, base: int):
        self.base = base
        self.buf = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, int | str]] = []

    def emit(self, data: bytes) -> None:
        self.buf += data

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ValueError(f"duplicate label: {name}")
        self.labels[name] = len(self.buf)

    def rel32(self, target: int | str) -> None:
        pos = len(self.buf)
        self.buf += b"\0\0\0\0"
        self.fixups.append((pos, target))

    def finish(self) -> bytes:
        out = bytearray(self.buf)
        for pos, target in self.fixups:
            target_rva = self.labels[target] + self.base if isinstance(target, str) else target
            out[pos : pos + 4] = struct.pack("<i", target_rva - (self.base + pos + 4))
        return bytes(out)

    def call(self, target: int | str) -> None:
        self.emit(b"\xe8")
        self.rel32(target)

    def jcc(self, opcode: bytes, target: int | str) -> None:
        self.emit(opcode)
        self.rel32(target)

    def rip_load(self, label: str, reg: str = "rax") -> None:
        # mov r64, [rip+disp32]
        enc = {"rax": b"\x48\x8b\x05", "rcx": b"\x48\x8b\x0d", "rdx": b"\x48\x8b\x15"}[reg]
        self.emit(enc)
        self.rel32(label)

    def rip_store(self, label: str, reg: str = "rax") -> None:
        # mov [rip+disp32], r64
        enc = {"rax": b"\x48\x89\x05", "rcx": b"\x48\x89\x0d", "rdx": b"\x48\x89\x15"}[reg]
        self.emit(enc)
        self.rel32(label)

    def lea_rip(self, label: str, reg: str = "rdx") -> None:
        enc = {"rdx": b"\x48\x8d\x15", "rax": b"\x48\x8d\x05"}[reg]
        self.emit(enc)
        self.rel32(label)


def build_payload(rva: int, force_forward: bool, probe_only: bool) -> bytes:
    e = Emitter(rva)

    # Wrapper called from the existing trigger.  MONAPI_PossessNext has no
    # arguments and the trigger ignores its return value.
    e.label("selector_wrapper")
    e.emit(b"\x48\x83\xec\x28")  # sub rsp, 0x28 (shadow + alignment)
    e.call(POSSESS_NEXT_RVA)
    e.emit(b"\x48\x83\xc4\x28")
    e.rip_load("selected_slot")
    e.emit(b"\x48\x85\xc0")  # test rax,rax
    e.jcc(b"\x0f\x84", "selector_done")
    e.rip_load("target_slot", "rcx")
    e.emit(b"\x48\x39\xc1")  # cmp rcx,rax
    e.jcc(b"\x0f\x84", "selector_done")
    e.emit(b"\x48\x85\xc9")  # test rcx,rcx
    e.jcc(b"\x0f\x84", "no_previous")
    e.rip_load("original_slot", "rdx")
    e.emit(b"\x48\x89\x91\x98\x01\x00\x00")  # mov [rcx+0x198],rdx
    e.label("no_previous")
    e.rip_store("target_slot")
    e.emit(b"\x48\x8b\x88\x98\x01\x00\x00")  # mov rcx,[rax+0x198]
    e.rip_store("original_slot", "rcx")
    e.lea_rip("process_wrapper")
    e.emit(b"\x48\x89\x90\x98\x01\x00\x00")  # mov [rax+0x198],rdx
    e.label("selector_done")
    e.emit(b"\xc3")

    # Callback installed only on the selected host.  It calls the original
    # process function first, then applies native monster movement.
    e.label("process_wrapper")
    e.emit(b"\x48\x83\xec\x30")  # shadow space + instance/tracker locals
    e.emit(b"\x48\x89\x4c\x24\x20")  # mov [rsp+0x20],rcx
    e.emit(b"\x48\x89\x54\x24\x28")  # mov [rsp+0x28],rdx
    e.rip_load("original_slot")
    e.call_register = None
    e.emit(b"\xff\xd0")  # call rax
    e.emit(b"\x48\x8b\x4c\x24\x20")  # mov rcx,[rsp+0x20]
    e.rip_load("target_slot")
    e.emit(b"\x48\x39\xc1")  # cmp rcx,rax
    e.jcc(b"\x0f\x85", "process_done")
    if probe_only:
        # Exercise only the callback handoff; leave all movement calls dead.
        e.emit(b"\xe9")
        e.rel32("process_done")
    e.emit(b"\x48\x8b\x54\x24\x28")  # mov rdx,[rsp+0x28]
    # 0x98 does not fit as a signed disp8 (it would decode as -0x68), so use
    # the disp32 form explicitly.
    e.emit(b"\x48\x8d\x82\x98\x00\x00\x00")  # lea rax,[rdx+0x98]
    e.emit(b"\x44\x8b\x00")  # mov r8d,[rax]
    if force_forward:
        e.emit(b"\x90" * 7)
    else:
        e.emit(b"\x41\xf7\xc0\x03\x00\x00\x00")  # test r8d,3
        e.jcc(b"\x0f\x84", "process_done")

    # Left/right turning from controlCommand bits 2/3.
    e.emit(b"\x41\xf6\xc0\x04")
    e.jcc(b"\x0f\x84", "no_left")
    e.emit(b"\x48\x8b\x44\x24\x20")
    e.emit(b"\x66\x81\x80\xd2\x00\x00\x00\x80\x00")
    e.label("no_left")
    e.emit(b"\x41\xf6\xc0\x08")
    e.jcc(b"\x0f\x84", "no_right")
    e.emit(b"\x48\x8b\x44\x24\x20")
    e.emit(b"\x66\x81\xa8\xd2\x00\x00\x00\x80\x00")
    e.label("no_right")

    e.emit(b"\x48\x8b\x44\x24\x20")
    e.emit(b"\x48\x8b\x88\xc0\x02\x00\x00")  # MonsterVars
    e.emit(b"\x44\x0f\xbf\x81\x38\x02\x00\x00")  # speed
    e.emit(b"\x41\xf6\xc0\x02")
    e.jcc(b"\x0f\x84", "speed_positive")
    e.emit(b"\x41\xf7\xd8")
    e.label("speed_positive")
    e.emit(b"\x48\x8b\x4c\x24\x20")
    e.emit(b"\x48\x8d\x91\xce\x00\x00\x00")
    e.call(PHYSICS_SET_VEL_RVA)
    e.emit(b"\x44\x8b\x05")
    e.rel32(TIME_MULT_GLOBAL_RVA)
    e.emit(b"\x48\x8b\x4c\x24\x20")
    e.emit(b"\x48\x8d\x91\x9c\x00\x00\x00")
    e.call(PHYSICS_MOVE_RVA)
    e.emit(b"\x48\x8b\x4c\x24\x20")
    e.call(PLAY_ANIMATION_RVA)
    e.label("process_done")
    e.emit(b"\x48\x83\xc4\x30")
    e.emit(b"\xc3")

    while len(e.buf) % 8:
        e.emit(b"\0")
    e.label("target_slot")
    e.emit(b"\0" * 8)
    e.label("original_slot")
    e.emit(b"\0" * 8)
    e.label("selected_slot")
    e.emit(b"\0" * 8)
    # The selector's RIP-relative load must point at the game's selected-host
    # global, so this slot is initialized as a rel32-indirect pointer target by
    # replacing its contents with the address at load time in the wrapper.
    # We instead use a direct RIP-relative load in the final fixup below.
    return e.finish()


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
        "<8sIIIIIIHHI", b".ptc\0\0\0\0", len(payload), rva, raw_size, raw_ptr, 0, 0, 0, 0, 0xE0000020
    )
    struct.pack_into("<H", raw, nt + 4 + 2, pe.FILE_HEADER.NumberOfSections + 1)
    struct.pack_into("<I", raw, nt + 4 + 20 + 56, align(rva + len(payload), pe.OPTIONAL_HEADER.SectionAlignment))
    return rva


def patch(input_path: Path, output_path: Path, force_forward: bool, probe_only: bool) -> None:
    pe = pefile.PE(str(input_path), fast_load=True)
    raw = bytearray(input_path.read_bytes())
    off = pe.get_offset_from_rva(TRIGGER_RVA)
    actual = bytes(raw[off : off + 5])
    if actual not in (TRIGGER_EXPECTED, TRIGGER_POC_EXPECTED):
        raise SystemExit("possession trigger guard failed")
    # First build at the eventual section RVA, then initialize the selected
    # global slot with a pointer-sized absolute target via the section data.
    last = max(pe.sections, key=lambda s: s.VirtualAddress + s.Misc_VirtualSize)
    section_rva = align(last.VirtualAddress + last.Misc_VirtualSize, pe.OPTIONAL_HEADER.SectionAlignment)
    payload = bytearray(build_payload(section_rva, force_forward, probe_only))
    # selected_slot is the final 8-byte slot.  Store the selected-host global's
    # RVA; the selector wrapper converts it to a pointer using RIP-relative load
    # from the game's image by overwriting the slot with its absolute address.
    # The simpler and ASLR-safe route is to make selected_slot itself a pointer
    # read target in code; patch its three-byte load below to direct game global.
    # Locate the first selected_slot rip-load (48 8b 05) and set its fixup.
    # Rebuild with a direct memory load by replacing the slot load's disp32.
    selected_marker = b"\x48\x8b\x05"
    pos = payload.find(selected_marker)
    if pos < 0:
        raise SystemExit("selected-host load marker missing")
    payload[pos + 3 : pos + 7] = struct.pack("<i", SELECTED_HOST_GLOBAL_RVA - (section_rva + pos + 7))
    new_rva = append_section(raw, pe, payload)
    raw[off : off + 5] = b"\xe8" + struct.pack("<i", new_rva - (TRIGGER_RVA + 5))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(raw)
    print(f"wrote {output_path}; .ptc RVA 0x{new_rva:X}, {len(payload)} bytes")
    if probe_only:
        print("diagnostic mode: callback handoff probe only")
    else:
        print("diagnostic mode: forced forward" if force_forward else "targeted process control enabled")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--force-forward", action="store_true")
    ap.add_argument("--probe-only", action="store_true")
    args = ap.parse_args()
    patch(args.input, args.output, args.force_forward, args.probe_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
