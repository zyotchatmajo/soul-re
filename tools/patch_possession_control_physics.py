#!/usr/bin/env python3
"""Add a guarded physics-control shim to the stable possession POC.

The dormant possession routine already selects a monster and the camera follows
it.  Running Raziel's full state machine on a MonsterVars instance is unsafe,
so this patch leaves the normal monster process intact and adds a small hook at
the end of MonsterProcess.  The selected monster receives the current digital
direction input through the existing PhysicsSetVelFromRot/PhysicsMove path.

The patch appends a private executable section instead of consuming tiny text
padding caves.  Input and output files are always separate, and all original
bytes are guarded.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import pefile


MONSTER_ENTRY_RVA = 0x99960
MONSTER_ENTRY_EXPECTED = bytes.fromhex("48 89 5C 24 18 57 48 83 EC 20")
LOOKAT_CALL_RVA = 0x99A2E
LOOKAT_CALL_EXPECTED = bytes.fromhex("E8 FD 40 00 00")
LOOKAT_TARGET_RVA = 0x9DB30
MONSTER_BODY_RVA = 0x9996A
PHYSICS_SET_VEL_RVA = 0xBB970
PHYSICS_MOVE_RVA = 0xBB610
PLAY_ANIMATION_RVA = 0x10CF70
SELECTED_HOST_GLOBAL_RVA = 0x2E8DE9
# This is gameTrackerX.timeMult in the installed PC remaster build.
TIME_MULT_GLOBAL_RVA = 0x2A89628

FILE_ALIGNMENT = 0x200
SECTION_ALIGNMENT = 0x1000
SECTION_CHARACTERISTICS = 0xE0000020  # code | execute | read | write


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


class Code:
    def __init__(self, base_rva: int):
        self.base = base_rva
        self.buf = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str, int]] = []

    def label(self, name: str) -> None:
        self.labels[name] = self.base + len(self.buf)

    def emit(self, data: bytes) -> None:
        self.buf += data

    def rel32(self, opcode: bytes, label: str) -> None:
        self.emit(opcode)
        self.fixups.append((len(self.buf), label, 4))
        self.emit(b"\x00\x00\x00\x00")

    def finish(self) -> bytes:
        out = bytearray(self.buf)
        for pos, label, size in self.fixups:
            if size != 4:
                raise AssertionError("unexpected fixup size")
            target = self.labels[label]
            next_rva = self.base + pos + size
            out[pos : pos + size] = struct.pack("<i", target - next_rva)
        return bytes(out)


def rip_load(reg_opcode: bytes, instruction_rva: int, next_len: int, target_rva: int) -> bytes:
    return reg_opcode + struct.pack("<i", target_rva - (instruction_rva + next_len))


def build_section(section_rva: int, force_forward: bool = False) -> tuple[bytes, int, int]:
    # Keep the code at fixed offsets so the RIP-relative data references are
    # easy to audit in a hex/disassembly check.
    entry_off = 0x000
    lookat_off = 0x080
    gt_slot_off = 0x200

    c = Code(section_rva + entry_off)
    # MonsterProcess entry hook: preserve the exact original prologue and
    # remember the current GameTracker pointer for the later call-site hook.
    c.emit(b"\x48\x89\x15")
    c.emit(struct.pack("<i", (section_rva + gt_slot_off) - (c.base + len(c.buf) + 4)))
    c.emit(b"\x48\x89\x5c\x24\x18\x57\x48\x83\xec\x20")
    c.emit(b"\xe9\x00\x00\x00\x00")
    entry_code = bytes(c.buf)

    c = Code(section_rva + lookat_off)
    # rbx is MonsterProcess's live instance register at the replaced call.
    c.emit(b"\x48\x8b\x05")
    c.emit(struct.pack("<i", SELECTED_HOST_GLOBAL_RVA - (c.base + len(c.buf) + 4)))
    c.emit(b"\x48\x39\xc3")
    c.rel32(b"\x0f\x85", "tail")

    # Load the current controlCommand[0] from the GameTracker captured at the
    # function entry.  Digital directions use the same low nibble as Raziel's
    # existing GetControllerInput routine.
    c.emit(b"\x48\x8b\x15")
    c.emit(struct.pack("<i", (section_rva + gt_slot_off) - (c.base + len(c.buf) + 4)))
    c.emit(b"\x48\x8d\x82\x98\x00\x00\x00")  # rax = &gameTracker->controlCommand[0]
    c.emit(b"\x44\x8b\x00")  # r8d = controlCommand[0]

    # Left/right turn.  The selected monster remains in its native coordinate
    # system; a small per-frame turn keeps this independent of Raziel globals.
    c.emit(b"\x41\xf6\xc0\x04")
    c.rel32(b"\x0f\x84", "no_left")
    c.emit(b"\x66\x81\x83\xd2\x00\x00\x00\x80\x00")
    c.label("no_left")
    c.emit(b"\x41\xf6\xc0\x08")
    c.rel32(b"\x0f\x84", "no_right")
    c.emit(b"\x66\x81\x83\xd2\x00\x00\x00\x80\x00")
    # Correct the second operation to subtraction by flipping the opcode's
    # direction byte in-place below after the first assembled pass.
    c.label("no_right")

    # Move forward/backward using the monster's own speed and physics.  The
    # remaster's 64-bit MonsterVars layout places speed at +0x238.
    if force_forward:
        # Keep the input load for diagnostics, but bypass the no-input branch.
        c.emit(b"\x90" * 10)
    else:
        c.emit(b"\x41\xf7\xc0\x03\x00\x00\x00")
        c.rel32(b"\x0f\x84", "tail")
    c.emit(b"\x48\x8b\x8b\xc0\x02\x00\x00")
    c.emit(b"\x45\x0f\xbf\x81\x38\x02\x00\x00")
    c.emit(b"\x41\xf6\xc0\x02")
    c.rel32(b"\x0f\x84", "speed_ready")
    c.emit(b"\x41\xf7\xd8")
    c.label("speed_ready")
    c.emit(b"\x48\x8b\xcb\x48\x8d\x93\xce\x00\x00\x00")
    c.rel32(b"\xe8", "physics_set_vel")
    c.emit(b"\x44\x8b\x05")
    c.emit(struct.pack("<i", TIME_MULT_GLOBAL_RVA - (c.base + len(c.buf) + 4)))
    c.emit(b"\x48\x8d\x93\x9c\x00\x00\x00\x48\x8b\xcb")
    c.rel32(b"\xe8", "physics_move")
    c.emit(b"\x48\x8b\xcb")
    c.rel32(b"\xe8", "play_animation")
    c.label("tail")
    c.emit(b"\x48\x8b\xcb")
    c.rel32(b"\xe9", "lookat_target")
    lookat_code = bytearray(c.buf)

    # The second turn opcode is subtraction (81 / 5) rather than addition
    # (81 / 0).  Locate the exact second occurrence and change ModRM.
    adds = [i for i in range(len(lookat_code) - 1) if lookat_code[i : i + 2] == b"\x66\x81"]
    if len(adds) != 2:
        raise AssertionError(f"expected two turn operations, found {adds}")
    lookat_code[adds[1] + 2] = 0xAB  # modrm group /5: sub word [rbx+0xd2], imm16

    # Relabel the calls/jumps now that the code has its final location.  The
    # helper labels in the first pass use section-relative targets.
    labels = {
        "monster_body": section_rva + MONSTER_BODY_RVA - section_rva,
        "lookat_target": section_rva + LOOKAT_TARGET_RVA - section_rva,
        "physics_set_vel": section_rva + PHYSICS_SET_VEL_RVA - section_rva,
        "physics_move": section_rva + PHYSICS_MOVE_RVA - section_rva,
        "play_animation": section_rva + PLAY_ANIMATION_RVA - section_rva,
    }
    # The initial builder intentionally uses labels; overwrite all fixups in
    # the assembled byte stream with the real target RVAs.
    def patch_targets(buf: bytearray, base: int, mapping: dict[str, int], offsets: list[tuple[int, str]]) -> None:
        for pos, name in offsets:
            buf[pos : pos + 4] = struct.pack("<i", mapping[name] - (base + pos + 4))

    # Rebuild using a small direct decoder of the fixup positions from the
    # second builder instance.
    direct = Code(section_rva + lookat_off)
    direct.buf = bytearray(c.buf)
    direct.labels = c.labels
    direct.fixups = c.fixups
    for pos, name, size in direct.fixups:
        target = labels.get(name, direct.labels.get(name))
        if target is None:
            raise AssertionError(f"unresolved target {name}")
        direct.buf[pos : pos + size] = struct.pack("<i", target - (direct.base + pos + size))
    lookat_code = bytearray(direct.buf)
    lookat_code[adds[1] + 2] = 0xAB

    # Patch the entry stub's one jump against the final address.
    entry = bytearray(entry_code)
    entry[-4:] = struct.pack("<i", MONSTER_BODY_RVA - (section_rva + len(entry)))

    section = bytearray(max(gt_slot_off + 8, lookat_off + len(lookat_code)))
    section[entry_off : entry_off + len(entry)] = entry
    section[lookat_off : lookat_off + len(lookat_code)] = lookat_code
    # gt_slot starts zeroed; it is written at every MonsterProcess entry.
    return bytes(section), entry_off, lookat_off


def append_section(raw: bytearray, pe: pefile.PE, payload: bytes, name: bytes) -> int:
    last = max(pe.sections, key=lambda s: s.VirtualAddress + s.Misc_VirtualSize)
    new_rva = align(last.VirtualAddress + last.Misc_VirtualSize, pe.OPTIONAL_HEADER.SectionAlignment)
    raw_ptr = align(len(raw), pe.OPTIONAL_HEADER.FileAlignment)
    raw_size = align(len(payload), pe.OPTIONAL_HEADER.FileAlignment)
    if len(raw) < raw_ptr:
        raw.extend(b"\x00" * (raw_ptr - len(raw)))
    raw.extend(payload)
    raw.extend(b"\x00" * (raw_size - len(payload)))

    nt = pe.DOS_HEADER.e_lfanew
    section_table = nt + 4 + 20 + pe.FILE_HEADER.SizeOfOptionalHeader
    new_header = section_table + pe.FILE_HEADER.NumberOfSections * 40
    raw[new_header : new_header + 40] = struct.pack(
        "<8sIIIIIIHHI",
        name.ljust(8, b"\x00"),
        len(payload),
        new_rva,
        raw_size,
        raw_ptr,
        0,
        0,
        0,
        0,
        SECTION_CHARACTERISTICS,
    )
    struct.pack_into("<H", raw, nt + 4 + 2, pe.FILE_HEADER.NumberOfSections + 1)
    size_image_off = nt + 4 + 20 + 56
    new_size_image = align(new_rva + len(payload), pe.OPTIONAL_HEADER.SectionAlignment)
    struct.pack_into("<I", raw, size_image_off, new_size_image)
    return new_rva


def patch(input_path: Path, output_path: Path, force_forward: bool = False) -> None:
    pe = pefile.PE(str(input_path), fast_load=True)
    raw = bytearray(input_path.read_bytes())

    for rva, expected in ((MONSTER_ENTRY_RVA, MONSTER_ENTRY_EXPECTED), (LOOKAT_CALL_RVA, LOOKAT_CALL_EXPECTED)):
        off = pe.get_offset_from_rva(rva)
        actual = bytes(raw[off : off + len(expected)])
        if actual != expected:
            raise SystemExit(f"guard failed at RVA 0x{rva:X}: expected {expected.hex(' ')}, found {actual.hex(' ')}")

    payload, entry_off, lookat_off = build_section(0, force_forward)  # rebuilt after RVA is known
    # Compute the section RVA before assembling RIP-relative code.
    last = max(pe.sections, key=lambda s: s.VirtualAddress + s.Misc_VirtualSize)
    section_rva = align(last.VirtualAddress + last.Misc_VirtualSize, pe.OPTIONAL_HEADER.SectionAlignment)
    payload, entry_off, lookat_off = build_section(section_rva, force_forward)
    new_rva = append_section(raw, pe, payload, b".pssn")
    if new_rva != section_rva:
        raise AssertionError("section RVA changed while appending")

    def call_patch(rva: int, target: int) -> bytes:
        return b"\xE8" + struct.pack("<i", target - (rva + 5))

    entry_off_raw = pe.get_offset_from_rva(MONSTER_ENTRY_RVA)
    raw[entry_off_raw : entry_off_raw + 5] = b"\xE9" + struct.pack("<i", new_rva + entry_off - (MONSTER_ENTRY_RVA + 5))
    lookat_off_raw = pe.get_offset_from_rva(LOOKAT_CALL_RVA)
    raw[lookat_off_raw : lookat_off_raw + 5] = call_patch(LOOKAT_CALL_RVA, new_rva + lookat_off)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(raw)
    print(f"wrote {output_path}")
    print(f"appended .pssn at RVA 0x{new_rva:X}, size {len(payload)}")
    if force_forward:
        print("diagnostic mode: selected host receives a forced forward physics step")
    else:
        print("selected host now receives digital direction input through native monster physics")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force-forward", action="store_true", help="diagnostic: move selected host every frame")
    args = parser.parse_args()
    patch(args.input, args.output, args.force_forward)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
