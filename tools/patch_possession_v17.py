#!/usr/bin/env python3
"""Possession control for the Soul Reaver 1 remaster (sr1.dll), v17.

Rewrite of patch_possession_control_entry.py.  Every engine offset below was
checked against the shipped x64 code (see comments); several offsets used by
v12-v16 were PSX-era guesses that point at the wrong MonsterVars fields.

What it does
  * Triangle (the "hold/grab" command, message 0x80000020) pressed while not
    possessing: MONAPI_PossessNext picks a host.  If nothing was selected the
    press is forwarded to Raziel unchanged, so grabbing blocks still works.
  * Triangle pressed while possessing: release straight back to Raziel
    (camera, 0x1000010 notifications, selected-host global).
  * The host's AI is skipped entirely while possessed: we run a reduced copy of
    MonsterProcess (timers, animation, damage FX, look-at, fade) without
    MONSENSE_DoSenses or the state function, then apply player control.
  * Raziel keeps running but gets an empty command block (only the Triangle
    press edge survives, so the release toggle works) and is held still.
  * The host is validated every frame; if it is freed or dies, control returns
    to Raziel automatically.

Controls while possessed: Up/Down walk forward, Left/Right turn, Cross plays
the monster's first combat attack animation (visual only; no hit spheres).

Input: any of
  - the Water Reaver DLL (trigger call at 0xE55FA still EnMessageQueueData),
  - the possession POC DLL (trigger already redirected to PossessNext),
  - a DLL already patched by any patch_possession_control_entry.py build or
    by this script (the old .pce section is stripped and hooks restored).
No third-party modules are required.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

# ---------------------------------------------------------------------------
# Engine addresses (RVAs in the shipped sr1.dll)
# ---------------------------------------------------------------------------
GAMETRACKER_RVA = 0x2A892C0           # gameTrackerX (passed as rdx to processFunc)
OWNER_RVA = GAMETRACKER_RVA + 0x50    # gameTrackerX.playerInstance
INSTANCE_LIST_RVA = GAMETRACKER_RVA + 0x60  # list->first at +8, inst->next at +0x10
CONTROL_COMMAND_RVA = GAMETRACKER_RVA + 0x98  # [0]=held [+4]=pressed [+8]=released
SELECTED_HOST_RVA = 0x2E8DF0          # written by MONAPI_PossessNext
HOLD_BUTTON_MASK_RVA = 0x22ACDC       # mask GetControllerMessages uses for 0x80000020
CAMERA_RVA = 0x3B0DD00                # theCamera, as passed by PossessNext

POSSESS_NEXT_RVA = 0x98D60
CAMERA_SET_FOCUS_RVA = 0x20E90
INSTANCE_POST_RVA = 0x79D10
EN_MESSAGE_QUEUE_RVA = 0x10CCC0       # EnMessageQueueData(queue, id, data)
MON_DO_COMBAT_TIMERS_RVA = 0xA44A0
G2EMU_PLAY_ANIMATION_RVA = 0x10CF70
MONTABLE_GET_DAMAGE_EFFECT_RVA = 0xA7850
MON_PROCESS_LOOK_AT_RVA = 0x9DB30
MON_PROCESS_SPECIAL_FADE_RVA = 0x9DCC0
MON_PLAY_ANIM_ID_IF_NOT_PLAYING_RVA = 0x9D560
MON_PLAY_ANIM_IF_NOT_PLAYING_RVA = 0x9D590
MON_MOVE_FORWARD_RVA = 0x9D0B0

HUMAN_WAIT_FOR_WEAPON_RVA = 0x94DF0
MONSTER_PROCESS_RVA = 0x99960
RAZIEL_PROCESS_RVA = 0xFC160
TRIGGER_RVA = 0xE55FA                 # call EnMessageQueueData for 0x80000020/0x20000020

HOOKS = [
    (HUMAN_WAIT_FOR_WEAPON_RVA, bytes.fromhex("40 53 48 83 EC 20")),
    (MONSTER_PROCESS_RVA, bytes.fromhex("48 89 5C 24 18")),
    (RAZIEL_PROCESS_RVA, bytes.fromhex("48 89 5C 24 08 48 89 6C 24 10 48 89 74 24 18")),
]

# ---------------------------------------------------------------------------
# Structure offsets (x64 remaster), with where each was confirmed
# ---------------------------------------------------------------------------
I_ROT_Z = 0xD2        # MON_MoveForward: lea rdx,[rcx+0xCE] = &rotation
I_DATA = 0x40         # MonsterProcess: cmp qword [rcx+0x40],0
I_FLAGS = 0x24
I_FLAGS2 = 0x28
I_WATERFACE = 0x1C8   # MonsterProcess clears these two
I_WATERFACE_T = 0x1D8
I_EXTRA = 0x2C0       # extraData (MonsterVars*)
I_XVEL = 0x2CC        # x/y/zVel = 0x2CC/0x2D0/0x2D4, x/y/zAccl = 0x2D8/0x2DC/0x2E0
MV_QUEUE = 0x08       # MessageQueue head/tail (PurgeMessageQueue zeroes 8 bytes)
MV_ATTACK_TYPE = 0x1A8  # MON_ChooseCombatMove stores chosen attack here
MV_ENEMY = 0x1C0      # (for reference; enemy->relativePosition.y read at +0x16)
MV_HELPER_1F8 = 0x1F8  # remaster-only pointer touched by MonsterProcess
MV_SPEED = 0x238      # MON_MoveForward: movsx r8d,word [mv+0x238]
MV_SUBATTR = 0x280
SUB_COMBAT = 0x10
COMBAT_NUM_ATTACKS = 0x1D
COMBAT_ATTACK_LIST = 0x2C
MA_ATTACK_LIST = 0x50  # MonsterAttributes.attackAttributesList, stride 0x20
ATTACK_ANIM_LIST = 0x1B

MVFLAG_DEAD = 0x200    # set by MON_DeadEntry; MonsterQuery reports "dead"
MSG_HOLD_PRESS = 0x80000020
MSG_UNPOSSESS = 0x1000010
ANIM_WALK = 2
ANIM_STANCE = 10


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


class Emitter:
    def __init__(self, base: int):
        self.base = base
        self.buf = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, int | str]] = []

    def emit(self, data: bytes | str) -> None:
        self.buf += bytes.fromhex(data) if isinstance(data, str) else data

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ValueError(f"duplicate label {name}")
        self.labels[name] = len(self.buf)

    def rel32(self, target: int | str) -> None:
        self.fixups.append((len(self.buf), target))
        self.buf += b"\0\0\0\0"

    def call(self, target: int | str) -> None:
        self.emit(b"\xe8")
        self.rel32(target)

    def jmp(self, target: int | str) -> None:
        self.emit(b"\xe9")
        self.rel32(target)

    def je(self, target: int | str) -> None:
        self.emit(b"\x0f\x84")
        self.rel32(target)

    def jne(self, target: int | str) -> None:
        self.emit(b"\x0f\x85")
        self.rel32(target)

    def rip(self, opcode: str, target: int) -> None:
        """RIP-relative instruction with no trailing immediate."""
        self.emit(opcode)
        self.rel32(target)

    def finish(self) -> bytes:
        out = bytearray(self.buf)
        for pos, target in self.fixups:
            dest = self.base + self.labels[target] if isinstance(target, str) else target
            out[pos:pos + 4] = struct.pack("<i", dest - (self.base + pos + 4))
        return bytes(out)


def d32(v: int) -> str:
    return struct.pack("<I", v & 0xFFFFFFFF).hex()


def w16(v: int) -> str:
    return struct.pack("<H", v & 0xFFFF).hex()


# RIP-relative opcode prefixes (ModRM 00/reg/101)
MOV_RAX_M = "48 8b 05"
MOV_RCX_M = "48 8b 0d"
MOV_RDX_M = "48 8b 15"
MOV_RBX_M = "48 8b 1d"
CMP_RAX_M = "48 3b 05"
CMP_RBX_M = "48 3b 1d"
MOV_M_RAX = "48 89 05"
LEA_RCX_M = "48 8d 0d"
MOV_EAX_M = "8b 05"
MOV_ESI_M = "8b 35"


def build_payload(base: int, speed: int, turn: int, animation_only: bool):
    e = Emitter(base)
    entries: dict[int, int] = {}

    # ---- HUMAN_WaitForWeapon wrapper: native init retry, then control ------
    entries[HUMAN_WAIT_FOR_WEAPON_RVA] = len(e.buf)
    e.rip(MOV_RAX_M, SELECTED_HOST_RVA)
    e.emit("48 39 c1")                       # cmp rcx,rax
    e.jne("tramp_wfw")
    e.emit("48 83 ec 28")                    # sub rsp,0x28
    e.emit("48 89 4c 24 20")                 # [rsp+0x20]=instance
    e.call("tramp_wfw")
    e.emit("48 8b 4c 24 20")
    e.call("possessed_process")
    e.emit("48 83 c4 28 c3")
    e.label("tramp_wfw")
    e.emit(HOOKS[0][1])
    e.jmp(HUMAN_WAIT_FOR_WEAPON_RVA + len(HOOKS[0][1]))

    # ---- MonsterProcess wrapper -------------------------------------------
    entries[MONSTER_PROCESS_RVA] = len(e.buf)
    e.rip(MOV_RAX_M, SELECTED_HOST_RVA)
    e.emit("48 39 c1")                       # cmp rcx,rax
    e.jne("tramp_mp")
    e.emit("48 8b 81" + d32(I_EXTRA))        # rax = mv
    e.emit("48 85 c0")
    e.je("tramp_mp")
    e.emit("f7 00" + d32(MVFLAG_DEAD))       # dying host: let native death run
    e.jne("tramp_mp")
    e.jmp("possessed_process")               # tail call, rcx = instance
    e.label("tramp_mp")
    e.emit(HOOKS[1][1])
    e.jmp(MONSTER_PROCESS_RVA + len(HOOKS[1][1]))

    # ---- possessed_process(rcx=instance): MonsterProcess minus AI ----------
    e.label("possessed_process")
    e.emit("53")                             # push rbx
    e.emit("48 83 ec 20")
    e.emit("48 89 cb")                       # rbx = instance
    e.emit("48 8b 83" + d32(I_EXTRA))        # rax = mv
    e.emit("48 85 c0")
    e.je("pp_out")
    e.emit("48 83 7b" + f"{I_DATA:02x}" + "00")  # cmp qword [rbx+0x40],0
    e.je("pp_out")
    e.emit("f7 00" + d32(0x80000))           # same early-out as native
    e.jne("pp_out")
    e.emit("48 8b 88" + d32(MV_HELPER_1F8))  # rcx = [mv+0x1F8]
    e.emit("48 85 c9")
    e.je("pp_no_helper")
    e.emit("66 c7 41 1c 00 00")              # word [rcx+0x1C] = 0
    e.label("pp_no_helper")
    e.emit("31 c9")                          # ecx = 0
    e.emit("48 89 8b" + d32(I_WATERFACE))
    e.emit("48 89 8b" + d32(I_WATERFACE_T))
    e.emit("48 89 48" + f"{MV_QUEUE:02x}")   # purge the host's message queue
    e.emit("48 89 d9")
    e.call(MON_DO_COMBAT_TIMERS_RVA)
    e.emit("83 63" + f"{I_FLAGS2:02x}" + "ed")  # flags2 &= ~0x12
    e.emit("48 8b 83" + d32(I_EXTRA))
    e.emit("f6 00 80")                       # mvFlags & 0x80 -> no emulated anim
    e.jne("pp_no_anim")
    e.emit("48 89 d9")
    e.call(G2EMU_PLAY_ANIMATION_RVA)
    e.label("pp_no_anim")
    e.emit("48 89 d9")
    e.call("control_body")
    e.emit("48 8b 83" + d32(I_EXTRA))
    e.emit("83 20 be")                       # mvFlags &= ~0x41
    e.emit("f7 43" + f"{I_FLAGS:02x}" + d32(0x200))
    e.je("pp_no_dmg")
    e.emit("48 89 d9")
    e.call(MONTABLE_GET_DAMAGE_EFFECT_RVA)
    e.emit("31 d2 48 89 d9 ff d0")           # rax(instance, 0)
    e.label("pp_no_dmg")
    e.emit("48 89 d9")
    e.call(MON_PROCESS_LOOK_AT_RVA)
    e.emit("48 89 d9")
    e.call(MON_PROCESS_SPECIAL_FADE_RVA)
    e.emit("48 8b 83" + d32(I_EXTRA))
    e.emit("48 8b 88" + d32(MV_HELPER_1F8))
    e.emit("48 85 c9")
    e.je("pp_out")
    e.emit("66 83 79 1c 00")                 # cmp word [rcx+0x1C],0
    e.jne("pp_out")
    e.emit("48 c7 80" + d32(MV_HELPER_1F8) + "00 00 00 00")
    e.label("pp_out")
    e.emit("48 83 c4 20 5b c3")

    # ---- control_body(rcx=instance) ---------------------------------------
    e.label("control_body")
    e.emit("53 56")                          # push rbx; push rsi
    e.emit("48 83 ec 28")
    e.emit("48 89 cb")
    e.rip(MOV_ESI_M, CONTROL_COMMAND_RVA)    # esi = held buttons
    e.emit("40 f6 c6 04")                    # left
    e.je("cb_no_left")
    e.emit("66 81 83" + d32(I_ROT_Z) + w16(turn))
    e.label("cb_no_left")
    e.emit("40 f6 c6 08")                    # right
    e.je("cb_no_right")
    e.emit("66 81 ab" + d32(I_ROT_Z) + w16(turn))
    e.label("cb_no_right")
    e.emit("40 f6 c6 80")                    # Cross
    e.je("cb_no_attack")
    e.emit("48 89 d9")
    e.call("pick_attack_anim")
    e.emit("83 f8 ff")
    e.je("cb_no_attack")
    e.emit("89 c2")                          # edx = anim id
    e.emit("41 b8 01 00 00 00")              # play once
    e.emit("48 89 d9")
    e.call(MON_PLAY_ANIM_ID_IF_NOT_PLAYING_RVA)
    e.jmp("cb_stop")
    e.label("cb_no_attack")
    e.emit("40 f6 c6 03")                    # up/down
    e.je("cb_idle")
    e.emit("48 89 d9")
    e.emit("ba" + d32(ANIM_WALK))
    e.emit("41 b8 02 00 00 00")              # loop
    e.call(MON_PLAY_ANIM_IF_NOT_PLAYING_RVA)
    if animation_only:
        e.jmp("cb_out")
    else:
        e.emit("48 8b 83" + d32(I_EXTRA))
        e.emit("66 c7 80" + d32(MV_SPEED) + w16(speed))
        e.emit("48 89 d9")
        e.call(MON_MOVE_FORWARD_RVA)
        e.jmp("cb_out")
    e.label("cb_idle")
    e.emit("48 89 d9")
    e.emit("ba" + d32(ANIM_STANCE))
    e.emit("41 b8 02 00 00 00")
    e.call(MON_PLAY_ANIM_IF_NOT_PLAYING_RVA)
    e.label("cb_stop")
    e.emit("48 8b 83" + d32(I_EXTRA))
    e.emit("66 c7 80" + d32(MV_SPEED) + "00 00")
    e.emit("31 c0")
    e.emit("48 89 83" + d32(I_XVEL))
    e.emit("89 83" + d32(I_XVEL + 8))
    e.emit("48 89 83" + d32(I_XVEL + 12))
    e.emit("89 83" + d32(I_XVEL + 20))
    e.label("cb_out")
    e.emit("48 83 c4 28 5e 5b c3")

    # ---- pick_attack_anim(rcx=instance) -> eax (anim id or -1); leaf ------
    e.label("pick_attack_anim")
    e.emit("48 8b 81" + d32(I_EXTRA))        # rax = mv
    e.emit("48 8b 90" + d32(MV_ATTACK_TYPE))  # rdx = last chosen attack
    e.emit("48 85 d2")
    e.jne("pa_have")
    e.emit("48 8b 90" + d32(MV_SUBATTR))
    e.emit("48 85 d2")
    e.je("pa_none")
    e.emit("48 8b 52" + f"{SUB_COMBAT:02x}")
    e.emit("48 85 d2")
    e.je("pa_none")
    e.emit("80 7a" + f"{COMBAT_NUM_ATTACKS:02x}" + "00")
    e.je("pa_none")
    e.emit("4c 0f be 42" + f"{COMBAT_ATTACK_LIST:02x}")  # r8 = attackList[0]
    e.emit("48 8b 51" + f"{I_DATA:02x}")     # rdx = MonsterAttributes
    e.emit("48 85 d2")
    e.je("pa_none")
    e.emit("48 8b 52" + f"{MA_ATTACK_LIST:02x}")
    e.emit("48 85 d2")
    e.je("pa_none")
    e.emit("49 c1 e0 05")                    # r8 *= 0x20
    e.emit("4c 01 c2")
    e.label("pa_have")
    e.emit("0f be 42" + f"{ATTACK_ANIM_LIST:02x}")
    e.emit("c3")
    e.label("pa_none")
    e.emit("b8 ff ff ff ff c3")

    # ---- RazielProcess wrapper (rcx=player, rdx=gameTracker, r8=commands) --
    entries[RAZIEL_PROCESS_RVA] = len(e.buf)
    e.emit("48 83 ec 78")
    e.emit("48 89 4c 24 60")
    e.emit("48 89 54 24 68")
    e.emit("4c 89 44 24 70")
    e.call("validate_host")
    e.emit("48 8b 4c 24 60")
    e.emit("48 8b 54 24 68")
    e.emit("4c 8b 44 24 70")
    e.emit("85 c0")
    e.je("raz_normal")
    e.emit("31 c0")
    for off in (0x20, 0x28, 0x30, 0x38, 0x40, 0x48):
        e.emit("48 89 44 24" + f"{off:02x}")
    e.rip(MOV_EAX_M, HOLD_BUTTON_MASK_RVA)
    e.emit("41 23 40 04")                    # eax &= commands.pressed
    e.emit("89 44 24 24")                    # private.pressed = hold press only
    e.emit("4c 8d 44 24 20")
    e.call("tramp_raz")
    e.rip(MOV_RAX_M, SELECTED_HOST_RVA)
    e.emit("48 85 c0")
    e.je("raz_done")
    e.rip(CMP_RAX_M, OWNER_RVA)
    e.je("raz_done")
    e.emit("48 8b 4c 24 60")
    e.emit("31 c0")
    e.emit("48 89 81" + d32(I_XVEL))
    e.emit("89 81" + d32(I_XVEL + 8))
    e.emit("48 89 81" + d32(I_XVEL + 12))
    e.emit("89 81" + d32(I_XVEL + 20))
    e.jmp("raz_done")
    e.label("raz_normal")
    e.call("tramp_raz")
    e.label("raz_done")
    e.emit("48 83 c4 78 c3")
    e.label("tramp_raz")
    e.emit(HOOKS[2][1])
    e.jmp(RAZIEL_PROCESS_RVA + len(HOOKS[2][1]))

    # ---- validate_host() -> eax=1 while a live host is possessed -----------
    e.label("validate_host")
    e.emit("48 83 ec 28")
    e.rip(MOV_RAX_M, SELECTED_HOST_RVA)
    e.emit("48 85 c0")
    e.je("vh_no")
    e.rip(CMP_RAX_M, OWNER_RVA)
    e.je("vh_no")
    e.rip(MOV_RCX_M, INSTANCE_LIST_RVA)
    e.emit("48 85 c9")
    e.je("vh_lost")
    e.emit("48 8b 49 08")                    # first instance
    e.label("vh_loop")
    e.emit("48 85 c9")
    e.je("vh_lost")
    e.emit("48 39 c1")
    e.je("vh_found")
    e.emit("48 8b 49 10")                    # next
    e.jmp("vh_loop")
    e.label("vh_found")
    e.emit("48 8b 88" + d32(I_EXTRA))
    e.emit("48 85 c9")
    e.je("vh_dead")
    e.emit("f7 01" + d32(MVFLAG_DEAD))
    e.jne("vh_dead")
    e.emit("b8 01 00 00 00 48 83 c4 28 c3")
    e.label("vh_dead")
    e.emit("b9 01 00 00 00")                 # host still exists: notify it
    e.call("release_possession")
    e.jmp("vh_no")
    e.label("vh_lost")
    e.emit("31 c9")                          # host gone: never touch it
    e.call("release_possession")
    e.label("vh_no")
    e.emit("31 c0 48 83 c4 28 c3")

    # ---- release_possession(ecx = host pointer still valid) ---------------
    e.label("release_possession")
    e.emit("53 56")
    e.emit("48 83 ec 28")
    e.emit("89 ce")                          # esi = host valid
    e.rip(MOV_RBX_M, SELECTED_HOST_RVA)
    e.emit("48 85 db")
    e.je("rp_out")
    e.rip(CMP_RBX_M, OWNER_RVA)
    e.je("rp_out")
    e.rip(LEA_RCX_M, CAMERA_RVA)
    e.rip(MOV_RDX_M, OWNER_RVA)
    e.call(CAMERA_SET_FOCUS_RVA)
    e.emit("85 f6")
    e.je("rp_owner")
    e.emit("48 8b 83" + d32(I_EXTRA))
    e.emit("48 85 c0")
    e.je("rp_post_host")
    e.emit("31 c9")
    e.emit("48 89 48" + f"{MV_QUEUE:02x}")   # drop messages queued while possessed
    e.emit("66 89 88" + d32(MV_SPEED))
    e.label("rp_post_host")
    e.emit("45 31 c0")
    e.emit("ba" + d32(MSG_UNPOSSESS))
    e.emit("48 89 d9")
    e.call(INSTANCE_POST_RVA)
    e.label("rp_owner")
    e.emit("45 31 c0")
    e.emit("ba" + d32(MSG_UNPOSSESS))
    e.rip(MOV_RCX_M, OWNER_RVA)
    e.call(INSTANCE_POST_RVA)
    e.rip(MOV_RAX_M, OWNER_RVA)
    e.rip(MOV_M_RAX, SELECTED_HOST_RVA)      # native "back to Raziel" sentinel
    e.label("rp_out")
    e.emit("48 83 c4 28 5e 5b c3")

    # ---- trigger stub, replaces call EnMessageQueueData at 0xE55FA --------
    entries[TRIGGER_RVA] = len(e.buf)
    e.emit("81 fa" + d32(MSG_HOLD_PRESS))
    e.jne(EN_MESSAGE_QUEUE_RVA)              # release edge etc.: unchanged
    e.emit("48 83 ec 38")
    e.emit("48 89 4c 24 20")
    e.emit("48 89 54 24 28")
    e.emit("4c 89 44 24 30")
    e.rip(MOV_RAX_M, SELECTED_HOST_RVA)
    e.emit("48 85 c0")
    e.je("st_select")
    e.rip(CMP_RAX_M, OWNER_RVA)
    e.je("st_select")
    e.emit("b9 01 00 00 00")                 # validated earlier this frame
    e.call("release_possession")
    e.jmp("st_consumed")
    e.label("st_select")
    e.call(POSSESS_NEXT_RVA)
    e.rip(MOV_RAX_M, SELECTED_HOST_RVA)
    e.emit("48 85 c0")
    e.je("st_forward")
    e.rip(CMP_RAX_M, OWNER_RVA)
    e.je("st_forward")
    e.label("st_consumed")
    e.emit("48 83 c4 38 c3")
    e.label("st_forward")                    # no host: Raziel gets the press
    e.emit("48 8b 4c 24 20")
    e.emit("48 8b 54 24 28")
    e.emit("4c 8b 44 24 30")
    e.emit("48 83 c4 38")
    e.jmp(EN_MESSAGE_QUEUE_RVA)

    return e.finish(), entries


# ---------------------------------------------------------------------------
# Minimal PE handling (no pefile dependency)
# ---------------------------------------------------------------------------
class PE:
    def __init__(self, raw: bytearray):
        self.raw = raw
        self.nt = struct.unpack_from("<I", raw, 0x3C)[0]
        if raw[self.nt:self.nt + 4] != b"PE\0\0":
            raise SystemExit("not a PE file")
        self.nsec_off = self.nt + 6
        opt_size = struct.unpack_from("<H", raw, self.nt + 20)[0]
        self.opt = self.nt + 24
        self.sec_table = self.opt + opt_size
        self.sect_align, self.file_align = struct.unpack_from("<II", raw, self.opt + 32)
        self.size_of_image_off = self.opt + 56

    @property
    def nsec(self) -> int:
        return struct.unpack_from("<H", self.raw, self.nsec_off)[0]

    def sections(self):
        out = []
        for i in range(self.nsec):
            o = self.sec_table + 40 * i
            name = self.raw[o:o + 8].rstrip(b"\0").decode("ascii", "replace")
            vsize, va, rsize, rptr = struct.unpack_from("<IIII", self.raw, o + 8)
            out.append((name, va, vsize, rptr, rsize, o))
        return out

    def off(self, rva: int) -> int:
        for _, va, vsize, rptr, rsize, _ in self.sections():
            if va <= rva < va + max(vsize, rsize):
                return rptr + rva - va
        raise SystemExit(f"RVA 0x{rva:X} not in any section")

    def rva_in_section(self, rva: int, name: str) -> bool:
        return any(n == name and va <= rva < va + vs for n, va, vs, *_ in self.sections())


def rel_target(raw: bytes, off: int, rva: int) -> int:
    return rva + 5 + struct.unpack_from("<i", raw, off + 1)[0]


def rel_insn(op: int, at: int, target: int) -> bytes:
    return bytes([op]) + struct.pack("<i", target - (at + 5))


def strip_previous_patch(raw: bytearray) -> bytearray:
    pe = PE(raw)
    secs = pe.sections()
    if not secs or secs[-1][0] != ".pce":
        return raw
    name, va, vsize, rptr, rsize, hdr = secs[-1]
    for entry, original in HOOKS:
        o = pe.off(entry)
        if raw[o] == 0xE9 and pe.rva_in_section(rel_target(raw, o, entry), ".pce"):
            raw[o:o + len(original)] = original
    o = pe.off(TRIGGER_RVA)
    if raw[o] == 0xE8 and pe.rva_in_section(rel_target(raw, o, TRIGGER_RVA), ".pce"):
        raw[o:o + 5] = rel_insn(0xE8, TRIGGER_RVA, EN_MESSAGE_QUEUE_RVA)
    raw[hdr:hdr + 40] = b"\0" * 40
    struct.pack_into("<H", raw, pe.nsec_off, pe.nsec - 1)
    last = max(pe.sections(), key=lambda s: s[1] + s[2])
    struct.pack_into("<I", raw, pe.size_of_image_off, align(last[1] + last[2], pe.sect_align))
    del raw[rptr:]
    print(f"stripped previous .pce section (RVA 0x{va:X}, {vsize} bytes) and restored hooks")
    return raw


def patch(inp: Path, outp: Path, speed: int, turn: int, animation_only: bool) -> None:
    raw = strip_previous_patch(bytearray(inp.read_bytes()))
    pe = PE(raw)

    for entry, original in HOOKS:
        o = pe.off(entry)
        if bytes(raw[o:o + len(original)]) != original:
            raise SystemExit(f"guard failed at 0x{entry:X}: {raw[o:o + len(original)].hex(' ')}")
    o = pe.off(TRIGGER_RVA)
    t = rel_target(raw, o, TRIGGER_RVA) if raw[o] == 0xE8 else None
    if t not in (EN_MESSAGE_QUEUE_RVA, POSSESS_NEXT_RVA):
        raise SystemExit(f"guard failed at trigger 0x{TRIGGER_RVA:X}: {raw[o:o + 5].hex(' ')}")

    last = max(pe.sections(), key=lambda s: s[1] + s[2])
    new_rva = align(last[1] + last[2], pe.sect_align)
    payload, entries = build_payload(new_rva, speed, turn, animation_only)

    if pe.sec_table + 40 * (pe.nsec + 1) > struct.unpack_from("<I", raw, pe.opt + 60)[0]:
        raise SystemExit("no room for another section header")
    raw_ptr = align(len(raw), pe.file_align)
    raw_size = align(len(payload), pe.file_align)
    raw.extend(b"\0" * (raw_ptr - len(raw)))
    raw.extend(payload)
    raw.extend(b"\0" * (raw_size - len(payload)))
    hdr = pe.sec_table + 40 * pe.nsec
    raw[hdr:hdr + 40] = struct.pack("<8sIIIIIIHHI", b".pce", len(payload), new_rva,
                                    raw_size, raw_ptr, 0, 0, 0, 0, 0x60000020)  # code|R|X
    struct.pack_into("<H", raw, pe.nsec_off, pe.nsec + 1)
    struct.pack_into("<I", raw, pe.size_of_image_off, align(new_rva + len(payload), pe.sect_align))

    for entry, original in HOOKS:
        o = pe.off(entry)
        raw[o:o + 5] = rel_insn(0xE9, entry, new_rva + entries[entry])
        raw[o + 5:o + len(original)] = b"\x90" * (len(original) - 5)
    o = pe.off(TRIGGER_RVA)
    raw[o:o + 5] = rel_insn(0xE8, TRIGGER_RVA, new_rva + entries[TRIGGER_RVA])

    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_bytes(raw)
    print(f"wrote {outp}; .pce at RVA 0x{new_rva:X}, {len(payload)} bytes")
    mode = "animation only" if animation_only else f"speed {speed}"
    print(f"possessed movement: {mode}, turn {turn}/frame")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--speed", type=int, default=50, help="MonsterVars speed while walking (default 50)")
    ap.add_argument("--turn", type=int, default=0x80, help="rotation units per frame, 4096 = full turn (default 128)")
    ap.add_argument("--animation-only", action="store_true",
                    help="play the walk clip without MON_MoveForward")
    a = ap.parse_args()
    if not 0 <= a.speed <= 0x7FFF or not 0 < a.turn < 0x800:
        raise SystemExit("speed must be 0..32767 and turn 1..2047")
    patch(a.input, a.output, a.speed, a.turn, a.animation_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
