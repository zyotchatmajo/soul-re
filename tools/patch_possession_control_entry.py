#!/usr/bin/env python3
"""CFG-safe possession control hooks at existing process-function entries."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import pefile


# MONAPI_PossessNext stores the selected host with:
#   mov qword ptr [rip + 0x24FF86], rbx
# at RVA 0x98E63.  The RIP target is 0x98E6A + 0x24FF86 = 0x2E8DF0.
SELECTED_HOST_GLOBAL_RVA = 0x2E8DF0
# The native possession routine uses this global as the player/owner instance.
# It is the object that receives the second 0x100000F/0x1000010 notification.
POSSESSION_OWNER_GLOBAL_RVA = 0x2A89310
INSTANCE_POST_RVA = 0x79D10
STREAM_GET_OBJECT_TRACKER_RVA = 0x115B40
SET_ACTION_PLAY_HOST_ANIMATION_DATA_RVA = 0x10E790
LOAD_PLAY_XA_RVA = 0x11C010
# Native monster message used by the original game to mark a monster as
# externally controlled. It sets mvFlags bit 0x4 and changes path states
# (pursue/wander/projectile/flee) to idle.
MON_CONTROL_MESSAGE = 0x4000E
PHYSICS_SET_VEL_RVA = 0xBB970
PHYSICS_MOVE_RVA = 0xBB610
MON_MOVE_FORWARD_RVA = 0x9D0B0
MON_PLAY_ANIM_ID_IF_NOT_PLAYING_RVA = 0x9D560
MON_PLAY_ANIM_IF_NOT_PLAYING_RVA = 0x9D590
MON_ATTACK_ENTRY_RVA = 0xA2FC0
# Native soul-drain visual helper. It emits the same dot/lightning link used
# by the original soul-suck/constrict attacks, from the source monster to the
# target instance. This is an opt-in test because it is visual-only here.
MON_DRAIN_EFFECTS_RVA = 0x9BC70
# Native force-projectile constructor.  The retail Raziel path calls this as
# PHYSOB_BirthProjectile(player, 49, reaverType); the returned event block has
# its spawned Instance* at +0x10 in the x64 remaster layout.
PHYSOB_BIRTH_PROJECTILE_RVA = 0xBE770
# CheckForceCollision is installed as the projectile instance's collideFunc.
# We replace that per-instance callback after birth, rather than hooking
# GetPhysObCollisionType (which is only used by ordinary physical weapons).
# The x64 Instance layout places processFunc/collideFunc at +0x198/+0x1A0.
INSTANCE_COLLIDE_FUNC_OFFSET = 0x1A0
CHECK_FORCE_COLLISION_RVA = 0xBBFA0
CHECK_FORCE_COLLISION_EXPECTED = bytes.fromhex("48 89 5C 24 18")
PROJECTILE_JOINT = 49
PROJECTILE_TYPE = 0
PROJECTILE_LAUNCH_MESSAGE = 0x800010
PROJECTILE_AIM_MASK = 0x0800       # R2 aim (RazielCommands[5])
PROJECTILE_CIRCLE_MASK = 0x0040   # Circle (RazielCommands[1])
# Native screen-wipe helper in the installed PC build.  0x10EB60 is a
# similarly-shaped control-data routine, not GAMELOOP_SetScreenWipe.
GAMELOOP_SET_SCREEN_WIPE_RVA = 0x6FE00
# The glyph acquisition uses the generic white flash effect for its bright
# transition.  It owns its own ramp/decay, so no second reveal wipe is needed.
FX_START_GENERIC_FLASH_RVA = 0x5A8D0
PROJECTILE_FLASH_NUMBER = 24
PROJECTILE_FADE_DELAY_FRAMES = 60
PROJECTILE_FADE_FRAMES = 12
PROJECTILE_FADE_TYPE = 10
CAMERA_SET_FOCUS_RVA = 0x20E90
CAMERA_GLOBAL_RVA = 0x3B0DD00
# x64 Camera::core.rotation.z.  CAMERA_SetLookRot writes the separate lookRot
# pair at +0x484/+0x488; the core rotation is the world-space view direction
# used by the renderer and the aim camera.
CAMERA_CORE_ROTATION_Z_OFFSET = 0x124
# Camera::lookRot.z is the player's horizontal look offset.  The native
# follow-camera code derives its view yaw as lookRot.z + focusInstance.yaw +
# 0x800, so the projectile can use the equivalent player-yaw + lookRot.z
# value directly without depending on the damped camera core orientation.
CAMERA_LOOK_ROT_Z_OFFSET = 0x488
INSTANCE_ROTATION_Z_OFFSET = 0xD2
# Native Raziel animation/effect helpers (verified against the clean PC
# remaster image).  The animation helper takes a CharacterState*; Raziel.State
# starts at RAZIEL_STATE_GLOBAL_RVA.
G2_SWITCH_ANIM_CHARACTER_RVA = 0x10E180
FX_POWER_RING_RVA = 0x534B0
# These are the native glow helpers.  The power-ring helper above is also used
# by the low-health warning and therefore produces the wrong visual for the
# possession/glyph pose.
FX_GLOW_MANY_RVA = 0x53320
# The glyph collectible's halo is not a glow or the low-health warning.  Its
# ObjectEffect list starts six generic particle emitters (particle entries
# 206..211) on the player/collectible instance.  This is the same path used by
# gstone/gfire/etc. when the pickup is initialized.
# The supplied PDB's lexical address (0x55310) does not belong to the newer
# installed Steam build.  Byte-matching the complete procedure against the
# clean installed image resolves its callable entry to 0x5AD30.
FX_START_GENERIC_PARTICLE_RVA = 0x5AD30
# This is the instance-level helper used by Raziel's native
# ``razSwitchVAnim`` path.  Unlike G2EmulationSwitchAnimationCharacter it
# takes the live Instance* directly and accepts the VAnim's keylist/frame/
# mode/alpha arguments in the normal Windows x64 ABI.
G2_INSTANCE_SWITCH_ANIM_ALPHA_RVA = 0x10D6A0
RAZIEL_STATE_GLOBAL_RVA = 0x2A88588
# Enabled after isolating the halo path; the native CharacterState entry point
# and its stack argument layout are now corrected.
# The direct G2 animation call is unsafe from the RazielProcess hook: it
# re-enters animation/state code at a point where the character state is not
# guaranteed to be ready and has caused startup/possession crashes. Keep the
# stable halo effect enabled while we resolve a safe native glyph transition.
# Disabled deliberately: keylist 112 is the Force-glyph cast/imbue pose, not
# the reward/acquisition animation.  Acquisition copies one of GLFHOST's four
# clips to Raziel through the event ATTR_HOSTINSTANCE path; that host instance
# is not available in the possession hook, so applying this pose here would be
# visibly wrong.
GLYPH_ANIMATION_ENABLED = True
# stringAnimations[1] is the native Force-glyph pose in the retail Raziel
# object.  Its VAnim selects keylist 112 for all three sections, four frames,
# mode 1 and alpha 0.  We apply that VAnim once per possession; the native
# clip then owns the pose instead of resetting it every frame.
GLYPH_POSE_KEYLIST = 1
GLYPH_POSE_FRAMES = 6
GLYPH_POSE_MODE = 2
GLYPH_POSE_ALPHA = 0
GLYPH_POSE_WAIT_FRAMES = 240
# Keep the voice call disabled for the first clip-transition retest.  The
# previous build crashed after adding a direct LOAD_PlayXA call; this switch
# lets us distinguish the voice ABI/lifecycle from the animation transition.
GLYPH_VOICE_ENABLED = False
PLAY_ANIMATION_RVA = 0x10CF70
TIME_MULT_GLOBAL_RVA = 0x2A89628
RAZIEL_PROCESS_RVA = 0xFC160
POSSESSION_TRIGGER_RVA = 0xE55FA
# Clean retail call at the possession selector entry.  All generated DLLs
# redirect this call to the payload's edge-triggered selector wrapper.
POSSESSION_TRIGGER_EXPECTED = bytes.fromhex("E8 C1 76 02 00")
POSSESS_NEXT_RVA = 0x98D60

HOOKS = [
    # The leading REX prefix is part of the original prologue.
    (0x94DF0, bytes.fromhex("40 53 48 83 EC 20"), 6),  # HUMAN_WaitForWeapon
    (0x99960, bytes.fromhex("48 89 5C 24 18"), 5),  # MonsterProcess
    # RazielProcess begins with three complete register-save instructions.  We
    # preserve both so the trampoline never starts in the middle of an x64
    # instruction.
    (RAZIEL_PROCESS_RVA, bytes.fromhex("48 89 5C 24 08 48 89 6C 24 10 48 89 74 24 18"), 15),
    # Force projectiles resolve enemy hits through this native entry directly
    # (the per-instance collideFunc is only the sphere fallback path).
    (CHECK_FORCE_COLLISION_RVA, CHECK_FORCE_COLLISION_EXPECTED, 5),
]


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


class Emitter:
    def __init__(self, base: int):
        self.base = base
        self.buf = bytearray()
        self.labels: dict[str, int] = {}
        # (displacement offset, target, bytes after the displacement).
        # Most RIP-relative references end immediately after disp32.  A
        # c7/c6 store has an immediate constant after disp32, so its RIP
        # address is measured from the end of that immediate instead.
        self.fixups: list[tuple[int, int | str, int]] = []

    def emit(self, data: bytes) -> None:
        self.buf += data

    def label(self, name: str) -> None:
        self.labels[name] = len(self.buf)

    def rel32(self, target: int | str, tail: int = 0) -> None:
        pos = len(self.buf)
        self.buf += b"\0\0\0\0"
        self.fixups.append((pos, target, tail))

    def call(self, target: int | str) -> None:
        self.emit(b"\xe8")
        self.rel32(target)

    def jmp(self, target: int | str) -> None:
        self.emit(b"\xe9")
        self.rel32(target)

    def jcc(self, opcode: bytes, target: int | str) -> None:
        self.emit(opcode)
        self.rel32(target)

    def rip_load_game(self, target: int, reg: str = "rax") -> None:
        enc = {
            "rax": b"\x48\x8b\x05",
            "rcx": b"\x48\x8b\x0d",
            "rdx": b"\x48\x8b\x15",
        }[reg]
        self.emit(enc)
        self.rel32(target)

    def rip_load_local(self, label: str, reg: str = "rax") -> None:
        enc = {"rax": b"\x48\x8b\x05", "rcx": b"\x48\x8b\x0d", "rdx": b"\x48\x8b\x15"}[reg]
        self.emit(enc)
        self.rel32(label)

    def rip_store_game(self, target: int, reg: str = "rax") -> None:
        enc = {"rax": b"\x48\x89\x05"}[reg]
        self.emit(enc)
        self.rel32(target)

    def rip_store_local_byte(self, label: str) -> None:
        """Store AL into a one-byte state cell inside the payload section."""
        self.emit(b"\x88\x05")
        self.rel32(label)

    def rip_load_local_word(self, label: str) -> None:
        """Load an unsigned 16-bit payload state cell into EAX."""
        self.emit(b"\x0f\xb7\x05")
        self.rel32(label)

    def rip_store_local_word(self, label: str) -> None:
        """Store AX into a 16-bit payload state cell."""
        self.emit(b"\x66\x89\x05")
        self.rel32(label)

    def lea_local(self, label: str, reg: str = "rax") -> None:
        """Load the payload-local address (not its contents) into a register."""
        enc = {"rax": b"\x48\x8d\x05", "rcx": b"\x48\x8d\x0d"}[reg]
        self.emit(enc)
        self.rel32(label)

    def rip_store_local(self, label: str, reg: str = "rax") -> None:
        """Store a 64-bit volatile register into a payload state cell."""
        enc = {"rax": b"\x48\x89\x05", "rcx": b"\x48\x89\x0d", "rdx": b"\x48\x89\x15"}[reg]
        self.emit(enc)
        self.rel32(label)

    def finish(self) -> bytes:
        out = bytearray(self.buf)
        for pos, target, tail in self.fixups:
            target_rva = self.labels[target] + self.base if isinstance(target, str) else target
            out[pos : pos + 4] = struct.pack(
                "<i", target_rva - (self.base + pos + 4 + tail)
            )
        return bytes(out)


def emit_attack(e: Emitter, done: str, after_attack: str, suffix: str) -> None:
    """Play the selected monster's actual attack clip when Cross is held."""
    no_attack = f"no_attack_{suffix}_{done}"
    e.emit(b"\x41\xf6\xc0\x80")
    e.jcc(b"\x0f\x84", no_attack)
    e.emit(b"\x48\x8b\x44\x24\x20")
    e.emit(b"\x48\x8b\x90\xc0\x02\x00\x00")
    # MonsterVars::attackType is at +0x1A8 in the x64 remaster layout.
    # +0x1D0 is a different field and silently prevents X from finding a
    # valid attack table.
    e.emit(b"\x48\x8b\x82\xa8\x01\x00\x00")
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", no_attack)
    # MonsterAttackAttributes::animList is a packed byte array at +0x1B;
    # attackType itself begins with range/damage fields, not a pointer.
    e.emit(b"\x0f\xbe\x50\x1b")
    e.emit(b"\x83\xfa\xff")
    e.jcc(b"\x0f\x84", no_attack)
    e.emit(b"\x41\xb8\x01\x00\x00\x00")
    e.emit(b"\x48\x8b\x4c\x24\x20")
    # Use the game's own attack-entry path.  It sets the attack state/mode,
    # starts the first attack clip, and preserves the monster's normal
    # animation callbacks instead of merely switching a clip pointer.
    e.call(MON_ATTACK_ENTRY_RVA)
    e.jmp(after_attack)
    e.label(no_attack)


def emit_soul_link(e: Emitter) -> None:
    """Emit one native soul-drain link update for the selected host."""
    # MON_DoDrainEffects(source, target) is normally called once per frame by
    # soul-suck states. Repeating it while possession is active keeps the
    # lightning/ribbon visible instead of producing a one-frame flash.
    # r8d contains the native command bits and is still needed by the control
    # path after this call. MON_DoDrainEffects is free to clobber volatile
    # registers, so keep the command word in our local stack area.
    e.emit(b"\x44\x89\x44\x24\x34")
    e.emit(b"\x48\x8b\x4c\x24\x20")
    e.emit(b"\x48\x8b\x15")
    e.rel32(POSSESSION_OWNER_GLOBAL_RVA)
    e.call(MON_DRAIN_EFFECTS_RVA)
    e.emit(b"\x44\x8b\x44\x24\x34")


def emit_projectile_possess_target(e: Emitter) -> None:
    """Enter possession for an exact projectile collision target.

    This mirrors the useful tail of MONAPI_PossessNext: focus the camera,
    notify the previous host/target/owner, and publish the selected-host
    global.  It is called from RazielProcess, not from the collision callback,
    so the instance message queues are not mutated during collision traversal.
    RCX is the target Instance* on entry.
    """
    e.label("projectile_possess_target")
    e.emit(b"\x48\x83\xec\x38")
    e.emit(b"\x48\x89\x4c\x24\x30")  # target
    # The projectile collision can leave a short-lived velocity/acceleration
    # impulse on the hit instance even when its knockback message is skipped.
    # Clear the six native motion slots before the target enters external
    # control, so possession starts from a stable standstill.
    e.emit(b"\x48\x8b\x44\x24\x30")
    # CheckForceCollision also marks the hit as FORCED/AMPLIFIEDFORCED.  Those
    # flags are consumed by the normal event/physics path and make a
    # projectile-possessed monster keep behaving as if it is airborne.  Clear
    # both variants before handing the instance to monster control.
    e.emit(b"\x8b\x48\x24")
    e.emit(b"\x81\xe1\xff\xff\xff\x7f")
    e.emit(b"\x89\x48\x24")
    e.emit(b"\x8b\x48\x28")
    e.emit(b"\x81\xe1\xff\xff\xfe\xff")
    e.emit(b"\x89\x48\x28")
    e.emit(bytes.fromhex("48 c7 80 cc 02 00 00 00 00 00 00"))
    e.emit(bytes.fromhex("c7 80 d4 02 00 00 00 00 00 00"))
    e.emit(bytes.fromhex("48 c7 80 d8 02 00 00 00 00 00 00"))
    e.emit(bytes.fromhex("c7 80 e0 02 00 00 00 00 00 00"))
    # Restore the impact-time position snapshot.  The native projectile
    # collision can advance the target one physics step before RazielProcess
    # consumes the pending possession, which leaves the host floating even
    # though its velocity is later zeroed.  Position and oldPos are three
    # signed 16-bit coordinates at +0x9c/+0xa4 in the x64 Instance layout.
    e.emit(b"\x48\x89\xc2")
    e.rip_load_local_word("projectile_saved_hit_x")
    e.emit(b"\x66\x89\x82\x9c\x00\x00\x00")
    e.rip_load_local_word("projectile_saved_hit_y")
    e.emit(b"\x66\x89\x82\x9e\x00\x00\x00")
    e.rip_load_local_word("projectile_saved_hit_z")
    e.emit(b"\x66\x89\x82\xa0\x00\x00\x00")
    e.rip_load_local_word("projectile_saved_old_x")
    e.emit(b"\x66\x89\x82\xa4\x00\x00\x00")
    e.rip_load_local_word("projectile_saved_old_y")
    e.emit(b"\x66\x89\x82\xa6\x00\x00\x00")
    e.rip_load_local_word("projectile_saved_old_z")
    e.emit(b"\x66\x89\x82\xa8\x00\x00\x00")
    # Camera focus is deliberately deferred until the presentation peak.  A
    # projectile hit should leave the view on Raziel while the glyph pose and
    # flash play; the end-of-process transition below focuses the new host.
    # If another host is selected, release it exactly as the native selector
    # does.  The normal v45 release path still handles the player sentinel.
    e.rip_load_game(SELECTED_HOST_GLOBAL_RVA)
    e.emit(b"\x48\x89\x44\x24\x28")
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "projectile_no_previous")
    e.rip_load_game(POSSESSION_OWNER_GLOBAL_RVA, reg="rcx")
    e.emit(b"\x48\x39\x44\x24\x28")
    e.jcc(b"\x0f\x84", "projectile_no_previous")
    e.emit(b"\x48\x8b\x4c\x24\x28")
    e.emit(b"\xba\x10\x00\x00\x01")
    e.emit(b"\x45\x31\xc0")
    e.call(INSTANCE_POST_RVA)
    e.label("projectile_no_previous")
    # Target receives the native "possess me" notification.
    e.emit(b"\x48\x8b\x4c\x24\x30")
    e.emit(b"\xba\x0f\x00\x00\x01")
    e.emit(b"\x45\x31\xc0")
    e.call(INSTANCE_POST_RVA)
    # Player receives the target as message data, then selected-host is set.
    e.rip_load_game(POSSESSION_OWNER_GLOBAL_RVA, reg="rcx")
    e.emit(b"\x48\x8b\x54\x24\x30")
    e.emit(b"\x49\x89\xd0")
    e.emit(b"\xba\x0f\x00\x00\x01")
    e.call(INSTANCE_POST_RVA)
    e.emit(b"\x48\x8b\x44\x24\x30")
    e.rip_store_game(SELECTED_HOST_GLOBAL_RVA)
    # Arm the presentation-only transition.  RazielProcess starts the wipe
    # after its native update has completed, so the normal player state code
    # cannot overwrite the newly scheduled fade.
    e.emit(b"\xc7\x05")
    e.rel32("projectile_fade_timer", tail=4)
    e.emit(struct.pack("<I", PROJECTILE_FADE_DELAY_FRAMES))
    e.emit(b"\xc6\x05")
    e.rel32("projectile_fade_started", tail=1)
    e.emit(b"\x00")
    e.emit(b"\x48\x83\xc4\x38")
    e.emit(b"\xc3")


def emit_projectile_collision(e: Emitter, done: str) -> None:
    """Capture the monster hit by the pending force projectile."""
    e.emit(b"\x48\x83\xec\x28")
    e.emit(b"\x48\x89\x4c\x24\x20")  # projectile instance
    e.rip_load_local("projectile_instance")
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "projectile_collision_call_" + done)
    e.emit(b"\x48\x39\x44\x24\x20")
    e.jcc(b"\x0f\x85", "projectile_collision_call_" + done)
    # x64 Instance::collideInfo is +0x158; CollideInfo::inst1 is +0x28.
    e.emit(b"\x48\x8b\x44\x24\x20")
    e.emit(b"\x48\x8b\x80\x58\x01\x00\x00")
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "projectile_collision_call_" + done)
    # Only instance-vs-instance collisions (type1 == 1) are possession hits.
    e.emit(b"\x80\x78\x07\x01")
    e.jcc(b"\x0f\x85", "projectile_collision_call_" + done)
    e.emit(b"\x48\x8b\x40\x28")
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "projectile_collision_call_" + done)
    e.rip_load_game(POSSESSION_OWNER_GLOBAL_RVA, reg="rcx")
    e.emit(b"\x48\x39\xc8")
    e.jcc(b"\x0f\x84", "projectile_collision_call_" + done)
    e.rip_store_local("projectile_hit_target")
    e.emit(b"\x31\xc0")
    e.rip_store_local("projectile_instance")
    e.label("projectile_collision_call_" + done)
    e.emit(b"\x48\x8b\x4c\x24\x20")
    e.call("trampoline_" + done)
    e.emit(b"\x48\x83\xc4\x28")
    e.emit(b"\xc3")


def emit_projectile_collision_callback(e: Emitter) -> None:
    """Wrap the force projectile's native CheckForceCollision callback.

    The collision system calls an Instance::collideFunc with the normal
    two-argument ABI (instance, gameTracker).  CheckForceCollision reads the
    actual hit from ``instance->collideInfo->inst1``; it is not passed in RDX.
    The old v51 hook watched GetPhysObCollisionType, which is not on the
    force-projectile path, so it never saw the enemy hit.  This callback is
    installed directly on the newly-born projectile, records the native
    collision record, then tail-calls the original CheckForceCollision.
    Possession is still entered one frame later from RazielProcess, keeping
    message/camera changes out of collision traversal.
    """
    e.label("projectile_collision_callback")
    # Do not build a new call frame here.  The collision dispatcher has a
    # compiler-generated callback ABI, and preserving its original RSP is the
    # safest way to forward the fifth/sixth arguments.  We inspect only the
    # volatile RAX register, then tail-jump to the original callback.

    e.rip_load_local("projectile_instance")
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "projectile_callback_no_capture")
    e.emit(b"\x48\x39\xc8")       # projectile instance == RCX?
    e.jcc(b"\x0f\x85", "projectile_callback_no_capture")
    # CheckForceCollision reads Instance::collideInfo at +0x158.  Its type1
    # byte is +0x07: 1 means instance-vs-instance; 3 means BSP/terrain.  The
    # other instance (inst1) is +0x28 in the x64 CollideInfo layout.
    e.emit(b"\x48\x8b\x81\x58\x01\x00\x00")
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "projectile_callback_no_capture")
    e.emit(b"\x80\x78\x07\x01")
    e.jcc(b"\x0f\x85", "projectile_callback_no_capture")
    e.emit(b"\x48\x8b\x50\x28")
    e.emit(b"\x48\x85\xd2")
    e.jcc(b"\x0f\x84", "projectile_callback_no_capture")
    # Do not turn a self-hit into possession if the projectile reports the
    # player as its other collider.
    e.rip_load_game(POSSESSION_OWNER_GLOBAL_RVA)
    e.emit(b"\x48\x39\xc2")
    e.jcc(b"\x0f\x84", "projectile_callback_no_capture")
    e.rip_store_local("projectile_hit_target", reg="rdx")
    e.emit(b"\x31\xc0")
    e.rip_store_local("projectile_instance")
    # Restore the real callback before calling it.  This prevents a second
    # callback on the same projectile from queuing a duplicate target if the
    # collision system revisits the object during its death animation.
    e.rip_load_local("projectile_original_collide")
    e.emit(b"\x48\x89\x81" + struct.pack("<I", INSTANCE_COLLIDE_FUNC_OFFSET))

    e.label("projectile_callback_no_capture")
    e.rip_load_local("projectile_original_collide")
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "projectile_callback_return")
    e.emit(b"\xff\xe0")       # tail-jump: preserve native callback frame
    e.label("projectile_callback_return")
    e.emit(b"\xc3")


def emit_force_collision_observer(e: Emitter, done: str) -> None:
    """Capture direct force-projectile instance hits at CheckForceCollision.

    Force projectiles use a line-collision path that calls CheckForceCollision
    directly with ``(projectile, hit_instance, tface, ...)``.  The projectile's
    per-instance collideFunc is only used by a different sphere-collision path,
    so observing that callback misses the normal enemy hit.  This wrapper keeps
    the native ABI and stack untouched, records the direct RDX hit once, and
    then runs the original prologue through the normal trampoline.
    """
    passthrough = "force_collision_passthrough_" + done
    e.rip_load_local("projectile_instance")
    e.emit(b"\x48\x85\xc0")              # pending projectile exists?
    e.jcc(b"\x0f\x84", passthrough)
    e.emit(b"\x48\x39\xc8")              # RCX == pending projectile?
    e.jcc(b"\x0f\x85", passthrough)
    e.emit(b"\x48\x85\xd2")              # RDX hit instance non-null?
    e.jcc(b"\x0f\x84", passthrough)
    e.emit(b"\x4d\x85\xc0")              # R8 tface == 0 (instance hit)
    e.jcc(b"\x0f\x85", passthrough)
    e.rip_load_local("projectile_hit_target")
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x85", passthrough)      # already captured this projectile
    e.rip_load_game(POSSESSION_OWNER_GLOBAL_RVA)
    e.emit(b"\x48\x39\xc2")              # do not possess Raziel himself
    e.jcc(b"\x0f\x84", passthrough)
    # CheckForceCollision's sixth argument is the oblique flag at the
    # caller's [rsp+0x28].  The native routine posts 0x800000 to a hit
    # instance when this is zero, which applies the projectile knockback that
    # makes a newly possessed enemy slide/fly.  Mark this one direct hit as
    # oblique so the projectile still resolves normally but skips that target
    # impulse.  The write is scoped to our pending projectile and enemy hit.
    e.emit(b"\xc7\x44\x24\x28\x01\x00\x00\x00")
    # Snapshot the hit instance before the native routine can apply its
    # collision-side position/physics update.
    e.emit(b"\x66\x8b\x82\x9c\x00\x00\x00")
    e.rip_store_local_word("projectile_saved_hit_x")
    e.emit(b"\x66\x8b\x82\x9e\x00\x00\x00")
    e.rip_store_local_word("projectile_saved_hit_y")
    e.emit(b"\x66\x8b\x82\xa0\x00\x00\x00")
    e.rip_store_local_word("projectile_saved_hit_z")
    e.emit(b"\x66\x8b\x82\xa4\x00\x00\x00")
    e.rip_store_local_word("projectile_saved_old_x")
    e.emit(b"\x66\x8b\x82\xa6\x00\x00\x00")
    e.rip_store_local_word("projectile_saved_old_y")
    e.emit(b"\x66\x8b\x82\xa8\x00\x00\x00")
    e.rip_store_local_word("projectile_saved_old_z")
    e.rip_store_local("projectile_hit_target", reg="rdx")
    e.emit(b"\x31\xc0")
    e.rip_store_local("projectile_instance")
    e.label(passthrough)
    e.jmp("trampoline_" + done)


def emit_glyph_style(e: Emitter) -> None:
    """Request the native GLFHOST acquisition pose, without direct particles.

    The retail glyph event uses GLFHOST as an animation donor.  We reproduce
    only that safe host-animation message here: the tracker is requested by
    name, a small persistent host-shaped block points at the loaded GLFHOST
    object, and Raziel receives the same puppet/action message used by the
    event system.  The gstone particle emitters are deliberately not called
    here; they require gstone's multi-model object and crash when attached to
    Raziel.
    """
    # glyph_fx_latch is a small presentation state: 0 = not started,
    # 1 = clip 0 is rising and the voice is playing, 2 = clip 1 is looping.
    # Do not latch until the tracker reports a loaded object.  This lets the
    # loader finish after possession instead of permanently losing the test.
    e.rip_load_local("glyph_fx_latch")
    e.emit(b"\x3c\x02")
    e.jcc(b"\x0f\x84", "glyph_fx_done")
    e.emit(b"\x3c\x01")
    e.jcc(b"\x0f\x84", "glyph_pose_rise")

    # First possession frame: request GLFHOST and play clip 0 once.  The
    # original event uses this rising clip before switching to clip 1; going
    # directly to clip 1 was the source of the small upward displacement.
    # STREAM_GetObjectTracker("glfhost") returns the tracker and starts the
    # asynchronous load when this object is not resident yet.
    e.emit(b"\x48\x8d\x0d")
    e.rel32("glyph_host_name")
    e.call(STREAM_GET_OBJECT_TRACKER_RVA)
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "glyph_fx_done")
    e.emit(b"\x48\x89\x44\x24\x40")  # [rsp+40] = tracker
    e.emit(b"\x0f\xb7\x40\x18")       # status at x64 tracker +0x18
    e.emit(b"\x83\xf8\x02")            # OBJECTTRACKER_USED
    e.jcc(b"\x0f\x85", "glyph_fx_done")
    e.emit(b"\x48\x8b\x44\x24\x40")
    e.emit(b"\x48\x8b\x50\x10")        # tracker->object at +0x10
    e.emit(b"\x48\x85\xd2")
    e.jcc(b"\x0f\x84", "glyph_fx_done")
    e.emit(b"\x48\x89\x15")
    e.rel32("glyph_host_object")

    # Enter the same puppet state used by the glyph event.
    e.emit(b"\x48\x8b\x4c\x24\x60")
    e.emit(b"\xba\x0e\x00\x04\x00")
    e.emit(b"\x41\xb8\x01\x00\x00\x00")
    e.call(INSTANCE_POST_RVA)

    # Build and post ATTR_HOSTINSTANCE + ATTR_ANIMATION(0), frame 0,
    # interpolation 6, play-once mode 1.  The native state handler then lets
    # the authored rise complete before we switch all sections to clip 1.
    e.emit(b"\x48\x8b\x4c\x24\x60")
    e.emit(b"\x48\x8d\x15")
    e.rel32("glyph_host_instance")
    e.emit(b"\x41\xb8\x00\x00\x00\x00")
    e.emit(b"\x45\x31\xc9")
    e.emit(b"\xc7\x44\x24\x20\x06\x00\x00\x00")
    e.emit(b"\xc7\x44\x24\x28\x01\x00\x00\x00")
    e.call(SET_ACTION_PLAY_HOST_ANIMATION_DATA_RVA)
    e.emit(b"\x48\x8b\x4c\x24\x60")
    e.emit(b"\xba\x03\x00\x04\x00")
    e.emit(b"\x49\x89\xc0")            # R8 = allocated action payload
    e.call(INSTANCE_POST_RVA)

    if GLYPH_VOICE_ENABLED:
        # STONE11 action 8 starts voice line 407 after the acquisition
        # sequence begins.  LOAD_PlayXA is the native one-argument voice
        # entry in this installed build; using it avoids the event VM's
        # wait-state machinery.
        e.emit(b"\xb9\x97\x01\x00\x00")
        e.call(LOAD_PLAY_XA_RVA)

    e.emit(b"\xb0\x01")
    e.rip_store_local_byte("glyph_fx_latch")
    e.emit(b"\xc7\x05")
    e.rel32("glyph_pose_wait", tail=4)
    e.emit(struct.pack("<I", GLYPH_POSE_WAIT_FRAMES))
    e.jmp("glyph_fx_done")

    # Hold clip 0 long enough for its authored rise to finish, then switch to
    # the looping acquisition pose (clip 1).  The event's 41-key clip is about
    # four seconds in the extracted SRA; 240 frames is the closest safe
    # presentation-only approximation without reading animation internals.
    e.label("glyph_pose_rise")
    e.emit(b"\x8b\x05")
    e.rel32("glyph_pose_wait")
    e.emit(b"\x85\xc0")
    e.jcc(b"\x0f\x84", "glyph_pose_loop")
    e.emit(b"\xff\xc8")
    e.emit(b"\x89\x05")
    e.rel32("glyph_pose_wait")
    e.jmp("glyph_fx_done")

    e.label("glyph_pose_loop")
    e.emit(b"\x48\x8b\x4c\x24\x60")
    e.emit(b"\x48\x8d\x15")
    e.rel32("glyph_host_instance")
    e.emit(b"\x41\xb8\x01\x00\x00\x00")
    e.emit(b"\x45\x31\xc9")
    e.emit(b"\xc7\x44\x24\x20\x06\x00\x00\x00")
    e.emit(b"\xc7\x44\x24\x28\x02\x00\x00\x00")
    e.call(SET_ACTION_PLAY_HOST_ANIMATION_DATA_RVA)
    e.emit(b"\x48\x8b\x4c\x24\x60")
    e.emit(b"\xba\x03\x00\x04\x00")
    e.emit(b"\x49\x89\xc0")
    e.call(INSTANCE_POST_RVA)
    e.emit(b"\xb0\x02")
    e.rip_store_local_byte("glyph_fx_latch")
    e.label("glyph_fx_done")


def emit_control(e: Emitter, done: str, force_forward: bool, speed: int,
                 turn: int, animation_only: bool,
                 native_control: bool = False,
                 soul_link: bool = False) -> None:
    # rcx/r8/r9 are volatile; the instance and tracker are saved in locals.
    e.emit(b"\x48\x83\xec\x38")
    e.emit(b"\x48\x89\x4c\x24\x20")
    e.emit(b"\x48\x89\x54\x24\x28")
    # Keep the controlled monster from sensing/retargeting Raziel.  Native
    # MONSENSE_SenseInstance skips hostile updates when MonsterVars::mvFlags
    # bit 0x200 is set; clearing enemy also removes any stale target.
    e.rip_load_game(SELECTED_HOST_GLOBAL_RVA)
    e.emit(b"\x48\x39\xc1")
    e.jcc(b"\x0f\x85", "pre_control_" + done)
    e.emit(b"\x48\x8b\x4c\x24\x20")
    e.emit(b"\x48\x8b\x81\xc0\x02\x00\x00")
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "pre_control_" + done)
    # Explicit encodings are used here because this is hand-emitted x64:
    #   or  dword ptr [rax], 0x200   (MonsterVars::mvFlags)
    #   mov qword ptr [rax+0x1c0],0  (MonsterVars::enemy)
    e.emit(bytes.fromhex("81 08 00 02 00 00"))
    e.emit(bytes.fromhex("48 c7 80 c0 01 00 00 00 00 00 00"))
    if native_control:
        # Tell the native monster code that this instance is externally
        # controlled. Unlike only clearing the enemy pointer, this exits any
        # existing patrol/path state before the native process runs.
        e.emit(bytes.fromhex("48 8b 4c 24 20"))
        e.emit(b"\xba" + struct.pack("<I", MON_CONTROL_MESSAGE))
        e.emit(bytes.fromhex("41 b8 01 00 00 00"))
        e.call(INSTANCE_POST_RVA)
        e.emit(bytes.fromhex("48 8b 4c 24 20"))
    e.label("pre_control_" + done)
    e.call("trampoline_" + done)
    e.emit(b"\x48\x8b\x4c\x24\x20")
    e.rip_load_game(SELECTED_HOST_GLOBAL_RVA)
    e.emit(b"\x48\x39\xc1")
    e.jcc(b"\x0f\x85", done)
    e.emit(b"\x48\x8b\x54\x24\x28")
    e.emit(b"\x48\x8d\x82\x98\x00\x00\x00")
    e.emit(b"\x44\x8b\x00")
    # Release/cycle is an edge-triggered action. The native selector may be
    # reached every frame while a button is held; a latch prevents Triangle
    # from alternating between Raziel and the monster.
    e.emit(b"\x44\x89\xc0")
    e.emit(b"\x25\x50\x00\x00\x00")
    e.emit(b"\x3d\x00\x00\x00\x00")
    e.jcc(b"\x0f\x85", "release_candidate_" + done)
    e.emit(b"\x44\x89\xc0")
    e.emit(b"\x25\x90\x00\x00\x00")
    e.emit(b"\x3d\x90\x00\x00\x00")
    e.jcc(b"\x0f\x84", "release_candidate_" + done)
    e.emit(b"\x31\xc0")
    e.rip_store_local_byte("release_latch")
    e.jmp("no_release_" + done)
    e.label("release_candidate_" + done)
    e.rip_load_local("release_latch")
    e.emit(b"\x84\xc0")
    e.jcc(b"\x0f\x85", "no_release_" + done)
    e.emit(b"\xb0\x01")
    e.rip_store_local_byte("release_latch")
    e.jmp("release_call_" + done)
    e.label("release_call_" + done)
    # Let the native selector perform the complete host/camera cycle.
    e.call("release_possession")
    e.jmp(done)
    e.label("no_release_" + done)
    if soul_link:
        emit_soul_link(e)
    if force_forward:
        e.emit(b"\x90" * 7)
    else:
        e.emit(b"\x41\xf7\xc0\x03\x00\x00\x00")
        e.jcc(b"\x0f\x84", "no_move_" + done)

    e.emit(b"\x41\xf6\xc0\x04")
    e.jcc(b"\x0f\x84", "no_left_" + done)
    e.emit(b"\x48\x8b\x44\x24\x20")
    e.emit(b"\x66\x81\x80\xd2\x00\x00\x00" + struct.pack("<H", turn))
    e.label("no_left_" + done)
    e.emit(b"\x41\xf6\xc0\x08")
    e.jcc(b"\x0f\x84", "no_right_" + done)
    e.emit(b"\x48\x8b\x44\x24\x20")
    e.emit(b"\x66\x81\xa8\xd2\x00\x00\x00" + struct.pack("<H", turn))
    e.label("no_right_" + done)
    emit_attack(e, done, done, "main")

    # Native walk clip (index 2) while forward is held.
    e.emit(b"\x48\x8b\x44\x24\x20")
    e.emit(b"\x48\x8b\x90\xc0\x02\x00\x00")
    e.emit(b"\x48\x8b\x82\x80\x02\x00\x00")
    e.emit(b"\x48\x8b\x00")
    e.emit(b"\x0f\xbe\x50\x02")
    e.emit(b"\x41\xb8\x02\x00\x00\x00")
    e.emit(b"\x48\x8b\x4c\x24\x20")
    e.call(MON_PLAY_ANIM_ID_IF_NOT_PLAYING_RVA)
    if not animation_only:
        e.emit(b"\x48\x8b\x44\x24\x20")
        e.emit(b"\x48\x8b\x88\xc0\x02\x00\x00")
        e.emit(b"\x66\xc7\x81\x38\x02\x00\x00" + struct.pack("<H", speed))
        e.emit(b"\x48\x8b\x4c\x24\x20")
        e.call(MON_MOVE_FORWARD_RVA)
    if not force_forward:
        e.jmp(done)
        e.label("no_move_" + done)
        emit_attack(e, done, "no_move_stance_" + done, "idle")
        e.label("no_move_stance_" + done)
        e.emit(b"\x48\x8b\x4c\x24\x20")
        e.emit(b"\xba\x0a\x00\x00\x00")
        e.emit(b"\x41\xb8\x02\x00\x00\x00")
        e.call(MON_PLAY_ANIM_IF_NOT_PLAYING_RVA)
        e.emit(b"\x48\x8b\x44\x24\x20")
        e.emit(b"\x48\x8b\x88\xc0\x02\x00\x00")
        e.emit(b"\x66\xc7\x81\x38\x02\x00\x00\x00\x00")
        e.emit(b"\x48\x8b\x44\x24\x20")
        e.emit(b"\x48\xc7\x80\xcc\x02\x00\x00\x00\x00\x00\x00")
        e.emit(b"\xc7\x80\xd4\x02\x00\x00\x00\x00\x00\x00")
        e.emit(bytes.fromhex("48 c7 80 d8 02 00 00 00 00 00 00"))
        e.emit(bytes.fromhex("c7 80 e0 02 00 00 00 00 00 00"))
    e.label(done)
    e.emit(b"\x48\x83\xc4\x38")
    e.emit(b"\xc3")


def emit_freeze_raziel(e: Emitter, done: str) -> None:
    """Run Raziel normally, then remove residual motion during possession.

    RazielProcess receives the player instance in RCX.  Clearing the common
    instance velocity/acceleration slots after the native process preserves
    the normal state machine while preventing the player body from drifting
    when the camera/control focus is on a possessed monster.
    """
    # Leave room above the native function's caller home area and keep our
    # saved RCX outside the original prologue's [rsp+8..+18] writes.
    e.emit(b"\x48\x83\xec\x78")
    e.emit(b"\x48\x89\x4c\x24\x60")  # [rsp+60] = player instance
    e.emit(b"\x4c\x89\x44\x24\x58")  # [rsp+58] = command block
    e.emit(b"\x48\x89\x54\x24\x70")  # [rsp+70] = gameTracker argument
    # Complete the projectile-only white transition after the glyph rise has
    # had time to put Raziel into its looping airborne pose.  Direct Triangle
    # possession never arms this timer, so it remains instantaneous.
    e.rip_load_local("projectile_fade_timer")
    e.emit(b"\x85\xc0")
    e.jcc(b"\x0f\x84", "projectile_fade_idle_" + done)
    e.emit(b"\xff\xc8")
    e.emit(b"\x89\x05")
    e.rel32("projectile_fade_timer")
    e.label("projectile_fade_idle_" + done)
    # A projectile collision only records its target in the collision hook.
    # Consume it here, one frame later, and enter the same camera/message
    # transition as the native possession selector.
    e.rip_load_local("projectile_hit_target")
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "no_projectile_hit_" + done)
    e.emit(b"\x48\x89\x44\x24\x68")
    e.emit(b"\x31\xc0")
    e.rip_store_local("projectile_hit_target")
    # The target helper already releases any previous selected host.  Do not
    # gate this path on SELECTED_HOST_GLOBAL: after a native release that
    # global can temporarily contain a stale/non-null pointer even though
    # control is back with Raziel, which would silently discard a valid shot.
    e.emit(b"\x48\x8b\x4c\x24\x68")
    e.rip_load_game(POSSESSION_OWNER_GLOBAL_RVA)
    e.emit(b"\x48\x39\xc8")
    e.jcc(b"\x0f\x84", "no_projectile_hit_" + done)
    e.emit(b"\x48\x8b\x4c\x24\x68")
    e.call("projectile_possess_target")
    e.label("no_projectile_hit_" + done)
    # Fire only from the player path.  The command layout is held=[0],
    # pressed=[1], released=[2]; Circle is 0x40 and R2 aim is 0x800.  The
    # one-byte latch prevents duplicate launches if a frontend
    # reports the press bit for more than one frame.
    e.rip_load_game(SELECTED_HOST_GLOBAL_RVA)
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "projectile_input_candidate_" + done)
    e.rip_load_game(POSSESSION_OWNER_GLOBAL_RVA)
    e.emit(b"\x48\x39\xc8")
    e.jcc(b"\x0f\x85", "projectile_input_done_" + done)
    e.label("projectile_input_candidate_" + done)
    e.emit(b"\x4c\x8b\x44\x24\x58")
    e.emit(b"\x41\x8b\x00")
    e.emit(b"\x25\x00\x08\x00\x00")
    e.emit(b"\x3d\x00\x08\x00\x00")
    e.jcc(b"\x0f\x85", "projectile_input_clear_" + done)
    e.emit(b"\x41\x8b\x40\x04")
    e.emit(b"\x25\x40\x00\x00\x00")
    e.emit(b"\x3d\x40\x00\x00\x00")
    e.jcc(b"\x0f\x85", "projectile_input_clear_" + done)
    e.rip_load_local("projectile_latch")
    e.emit(b"\x84\xc0")
    e.jcc(b"\x0f\x85", "projectile_input_done_" + done)
    e.emit(b"\xb0\x01")
    e.rip_store_local_byte("projectile_latch")
    e.emit(b"\x48\x8b\x4c\x24\x60")
    e.emit(b"\xba" + struct.pack("<I", PROJECTILE_JOINT))
    e.emit(b"\x45\x31\xc0")
    e.call(PHYSOB_BIRTH_PROJECTILE_RVA)
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "projectile_input_done_" + done)
    e.emit(b"\x48\x8b\x50\x10")
    e.emit(b"\x48\x85\xd2")
    e.jcc(b"\x0f\x84", "projectile_input_done_" + done)
    e.emit(b"\x48\x89\x15")
    e.rel32("projectile_instance")
    # BirthProjectile only creates the force object and links it to Raziel's
    # hand. The retail state machine launches it one animation event later
    # by posting 0x800010 to the projectile; PhysicalObjectPost then calls
    # ThrowPhysOb with a NULL payload, which selects the native default
    # forward velocity/rotation. Reproduce that deferred message here so
    # the custom R2+Circle path behaves like the native force throw instead
    # of leaving the projectile attached and stationary.
    # ThrowPhysOb(NULL) uses the parent instance's body yaw.  During R2 aim
    # that yaw is not necessarily the camera direction, so temporarily feed
    # it the camera's world-space core rotation.  The native launch path then
    # computes its normal projectile velocity, including the existing gravity
    # and collision behavior, but along the direction Raziel is looking.
    e.emit(b"\x48\x8b\x4c\x24\x60")
    e.emit(b"\x0f\xb7\x81" + struct.pack("<I", INSTANCE_ROTATION_Z_OFFSET))
    e.rip_store_local_word("projectile_saved_player_yaw")
    e.emit(b"\x48\x8d\x05")
    e.rel32(CAMERA_GLOBAL_RVA)
    # lookRot.z is meaningful only in the camera's look-around mode (6).
    # Outside that mode it is stale from the previous aim session; use the
    # camera's target-focus heading instead so a post-possession shot cannot
    # inherit an old offset.
    e.emit(b"\x0f\xb7\x88\x20\x01\x00\x00")
    e.emit(b"\x83\xf9\x06")
    e.jcc(b"\x0f\x85", "projectile_aim_target_heading_" + done)
    e.emit(b"\x0f\xb7\x80" + struct.pack("<I", CAMERA_LOOK_ROT_Z_OFFSET))
    e.jmp("projectile_aim_heading_ready_" + done)
    e.label("projectile_aim_target_heading_" + done)
    e.emit(b"\x0f\xb7\x80\xea\x01\x00\x00")
    e.emit(b"\x2d\x00\x08\x00\x00")
    e.label("projectile_aim_heading_ready_" + done)
    # CAMERA's native follow code computes targetFocusRotation.z as
    # lookRot.z + focusInstance.rotation.z + 0x800.  The projectile's forward
    # angle is that view orientation minus the camera's 0x800 rear offset.
    e.emit(b"\x48\x8b\x4c\x24\x60")
    # Keep RCX as the player Instance* for the final store; use EDX for the
    # temporary yaw so the pointer is not accidentally truncated to an angle.
    e.emit(b"\x0f\xb7\x91" + struct.pack("<I", INSTANCE_ROTATION_Z_OFFSET))
    e.emit(b"\x01\xd0")
    e.emit(b"\x25\xff\x0f\x00\x00")
    e.emit(b"\x66\x89\x81" + struct.pack("<I", INSTANCE_ROTATION_Z_OFFSET))
    e.rip_load_local("projectile_instance", reg="rcx")
    e.emit(b"\xba" + struct.pack("<I", PROJECTILE_LAUNCH_MESSAGE))
    e.emit(b"\x45\x31\xc0")       # r8 = NULL data
    e.call(INSTANCE_POST_RVA)
    # Restore Raziel's body yaw immediately after the synchronous launch.
    e.emit(b"\x48\x8b\x4c\x24\x60")
    e.rip_load_local_word("projectile_saved_player_yaw")
    e.emit(b"\x66\x89\x81" + struct.pack("<I", INSTANCE_ROTATION_Z_OFFSET))
    # Force projectiles hit enemies through the direct CheckForceCollision
    # call, which is observed by its dedicated hook below.  Do not replace the
    # projectile's per-instance collideFunc: that callback belongs to a
    # different collision path and replacing it can destabilize later physics.
    e.label("projectile_input_done_" + done)
    e.jmp("projectile_input_after_" + done)
    e.label("projectile_input_clear_" + done)
    e.emit(b"\x31\xc0")
    e.rip_store_local_byte("projectile_latch")
    e.label("projectile_input_after_" + done)
    # PHYSOB_BirthProjectile and the deferred possession helper clobber the
    # volatile command pointer; restore it before any native Raziel call.
    e.emit(b"\x48\x8b\x4c\x24\x60")
    e.emit(b"\x4c\x8b\x44\x24\x58")
    e.emit(b"\x48\x8b\x54\x24\x70")
    # When possession is active, pass RazielProcess a private zeroed command
    # block.  This prevents the normal Raziel state machine from consuming
    # directional input and moving Raziel while the monster is controlled.
    e.rip_load_game(SELECTED_HOST_GLOBAL_RVA)
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "normal_input_" + done)
    # A few native release paths leave the selected pointer populated while
    # returning control to Raziel.  The process argument is authoritative: if
    # the selected object is the player instance, do not mask his commands.
    e.emit(b"\x48\x8b\x4c\x24\x60")
    e.emit(b"\x48\x39\xc8")
    e.jcc(b"\x0f\x84", "normal_input_" + done)
    # The native cycle represents "back to Raziel" by selecting the owner
    # instance itself, rather than storing NULL.  Treat that sentinel as no
    # active possession so Raziel receives normal input and camera focus.
    e.rip_load_game(POSSESSION_OWNER_GLOBAL_RVA, reg="rcx")
    e.emit(b"\x48\x39\xc8")
    e.jcc(b"\x0f\x84", "normal_input_" + done)
    # Preserve possession/release inputs while masking movement and combat.
    # 0x10 is Triangle, and 0x80 is Cross; keeping their chord available lets
    # the dormant native selector/release path see the same input as the
    # active monster hook.
    e.emit(b"\x41\x8b\x00")  # eax = original controlCommand[0]
    e.emit(b"\x25\x90\x00\x00\x00")  # eax &= 0x90 (Triangle+Cross only)
    e.emit(b"\x89\x44\x24\x50")  # save retained bit outside the zero block
    e.emit(b"\x31\xc0")
    for offset in (0x20, 0x28, 0x30, 0x38, 0x40, 0x48):
        e.emit(b"\x48\x89\x44\x24" + bytes([offset]))
    e.emit(b"\x8b\x44\x24\x50")
    e.emit(b"\x89\x44\x24\x20")
    e.emit(b"\x4c\x8d\x44\x24\x20")  # r8 = private zeroed commands
    e.call("trampoline_" + done)
    e.emit(b"\xe9")
    e.rel32("after_input_" + done)
    e.label("normal_input_" + done)
    # Re-arm the one-shot halo for the next possession.
    e.emit(b"\x31\xc0")
    e.rip_store_local_byte("glyph_fx_latch")
    # A released button arms the next Triangle/Circle press.  RazielProcess
    # is the reliable place to observe the real command block after control
    # has returned to the player.
    e.emit(b"\x41\x8b\x00")
    e.emit(b"\x25\x50\x00\x00\x00")
    e.emit(b"\x3d\x00\x00\x00\x00")
    e.jcc(b"\x0f\x85", "keep_release_latch_" + done)
    e.emit(b"\x41\x8b\x00")
    e.emit(b"\x25\x90\x00\x00\x00")
    e.emit(b"\x3d\x90\x00\x00\x00")
    e.jcc(b"\x0f\x84", "keep_release_latch_" + done)
    e.emit(b"\x31\xc0")
    e.rip_store_local_byte("release_latch")
    e.label("keep_release_latch_" + done)
    e.call("trampoline_" + done)
    e.label("after_input_" + done)
    e.rip_load_game(SELECTED_HOST_GLOBAL_RVA)
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", done)
    e.emit(b"\x48\x8b\x4c\x24\x60")
    e.emit(b"\x48\x85\xc9")
    e.jcc(b"\x0f\x84", done)
    e.emit(b"\x48\x39\xc8")
    e.jcc(b"\x0f\x84", done)
    # The native Raziel update is complete now, so schedule the projectile
    # transition without it being overwritten by the normal player state.
    # The delay keeps the camera on Raziel while the authored rise/halo plays.
    e.rip_load_local("projectile_fade_started")
    e.emit(b"\x84\xc0")
    e.jcc(b"\x0f\x85", "projectile_fade_done_" + done)
    e.rip_load_local("projectile_fade_timer")
    e.emit(b"\x85\xc0")
    e.jcc(b"\x0f\x84", "projectile_fade_done_" + done)
    # Use the same white generic flash used by the glyph acquisition event.
    # It owns the ramp/hold/decay internally and is safe to trigger from the
    # player process after the native update has returned.
    e.emit(b"\x48\x8b\x4c\x24\x60")
    e.emit(b"\xba" + struct.pack("<I", PROJECTILE_FLASH_NUMBER))
    e.call(FX_START_GENERIC_FLASH_RVA)
    # Move the camera only at the peak, while the white flash is active.
    e.rip_load_game(SELECTED_HOST_GLOBAL_RVA, reg="rdx")
    e.emit(b"\x48\x8d\x0d")
    e.rel32(CAMERA_GLOBAL_RVA)
    e.call(CAMERA_SET_FOCUS_RVA)
    e.emit(b"\xb0\x01")
    e.rip_store_local_byte("projectile_fade_started")
    e.label("projectile_fade_done_" + done)
    # Keep Raziel in the glyph-acquisition pose while the possessed host is
    # active, and show the matching halo once per possession.
    emit_glyph_style(e)
    # Native FX/animation helpers are free to clobber RCX. Reload the player
    # instance before touching its movement fields.
    e.emit(b"\x48\x8b\x4c\x24\x60")
    # Instance velocity and acceleration fields used by MON_MoveForward.
    e.emit(bytes.fromhex("48 c7 81 cc 02 00 00 00 00 00 00"))
    e.emit(bytes.fromhex("c7 81 d4 02 00 00 00 00 00 00"))
    e.emit(bytes.fromhex("48 c7 81 d8 02 00 00 00 00 00 00"))
    e.emit(bytes.fromhex("c7 81 e0 02 00 00 00 00 00 00"))
    e.label(done)
    e.emit(b"\x48\x83\xc4\x78")
    e.emit(b"\xc3")


def build_payload(rva: int, force_forward: bool, speed: int, turn: int,
                  animation_only: bool, soul_link: bool = False) -> tuple[bytes, dict[int, int]]:
    e = Emitter(rva)
    hook_meta: dict[int, int] = {}
    for index, (entry, original, length) in enumerate(HOOKS):
        done = f"done_{index}"
        wrapper = len(e.buf)
        hook_meta[entry] = wrapper
        if entry == RAZIEL_PROCESS_RVA:
            emit_freeze_raziel(e, done)
        elif entry == CHECK_FORCE_COLLISION_RVA:
            emit_force_collision_observer(e, done)
        else:
            emit_control(e, done, force_forward, speed, turn, animation_only,
                         native_control=(entry == 0x99960), soul_link=soul_link)
        e.label("trampoline_" + done)
        e.emit(original)
        e.emit(b"\xe9")
        e.rel32(entry + length)
    # Clear the temporary AI suppression on the current host, then let the
    # original selector perform its complete enemy-to-enemy/Raziel cycle.
    e.label("release_possession")
    # Both callers align this helper to a normal function entry. Keep the
    # selected pointer outside INSTANCE_Post/selector shadow space.
    e.emit(b"\x48\x83\xec\x38")
    e.rip_load_game(SELECTED_HOST_GLOBAL_RVA)
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "release_return")
    e.emit(b"\x48\x89\x44\x24\x30")
    e.emit(b"\x48\x8b\x4c\x24\x30")
    e.emit(b"\x48\x8b\x81\xc0\x02\x00\x00")
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "release_cycle")
    # Clear the native external-control bit directly before handing the
    # selector back to the player. Calling the monster message handler from
    # this release path can run state-entry code while the selector is already
    # changing, which is unsafe for some monster types.
    e.emit(b"\x81\x20\xfb\xfd\xff\xff")
    e.emit(b"\x48\xc7\x80\xc0\x01\x00\x00\x00\x00\x00\x00")
    e.label("release_cycle")
    e.call(POSSESS_NEXT_RVA)
    # MONAPI_PossessNext selects either another monster or the player.  When
    # it returns the player sentinel, explicitly leave Raziel's puppet state;
    # otherwise the pose test correctly returns the camera but leaves normal
    # movement input masked on the next frame.
    e.rip_load_game(SELECTED_HOST_GLOBAL_RVA)
    e.emit(b"\x48\x89\xc2")
    e.rip_load_game(POSSESSION_OWNER_GLOBAL_RVA, reg="rcx")
    e.emit(b"\x48\x39\xca")
    e.jcc(b"\x0f\x85", "release_return")
    e.emit(b"\xba\x0e\x00\x04\x00")
    e.emit(b"\x45\x31\xc0")
    e.call(INSTANCE_POST_RVA)
    e.label("release_return")
    e.emit(b"\x48\x83\xc4\x38")
    e.emit(b"\xc3")
    # Toggle the dormant possession selector: the first press selects the
    # next eligible host, while a press during possession clears the selected
    # host and lets the normal player/camera path resume.
    trigger_stub = len(e.buf)
    hook_meta[POSSESSION_TRIGGER_RVA] = trigger_stub
    # At the patched call site EDX distinguishes a fresh button event
    # (0x80000000) from the held/previous-state event (0x20000000).  The
    # latter is what caused Triangle to cycle back into the monster.
    e.emit(b"\xf7\xc2\x00\x00\x00\x20")
    e.jcc(b"\x0f\x85", "trigger_held")
    # The original trigger can be reached once per frame while the button is
    # held. Consume only the first call; the player-process hook clears this
    # latch after the button is released.
    e.rip_load_local("release_latch")
    e.emit(b"\x84\xc0")
    e.jcc(b"\x0f\x85", "trigger_held")
    e.emit(b"\xb0\x01")
    e.rip_store_local_byte("release_latch")
    e.rip_load_game(SELECTED_HOST_GLOBAL_RVA)
    e.emit(b"\x48\x85\xc0")
    e.jcc(b"\x0f\x84", "select_next")
    e.rip_load_game(POSSESSION_OWNER_GLOBAL_RVA, reg="rcx")
    e.emit(b"\x48\x39\xc8")
    e.jcc(b"\x0f\x84", "select_next")
    e.emit(b"\x48\x83\xec\x08")
    e.call("release_possession")
    e.emit(b"\x48\x83\xc4\x08")
    e.emit(b"\xc3")
    e.label("trigger_held")
    e.emit(b"\xc3")
    e.label("select_next")
    e.jmp(POSSESS_NEXT_RVA)
    emit_projectile_possess_target(e)
    emit_projectile_collision_callback(e)
    e.label("release_latch")
    e.emit(b"\x00")
    e.label("projectile_latch")
    e.emit(b"\x00")
    e.label("projectile_instance")
    e.emit(b"\x00" * 8)
    e.label("projectile_hit_target")
    e.emit(b"\x00" * 8)
    e.label("projectile_original_collide")
    e.emit(b"\x00" * 8)
    e.label("projectile_saved_hit_x")
    e.emit(b"\x00\x00")
    e.label("projectile_saved_hit_y")
    e.emit(b"\x00\x00")
    e.label("projectile_saved_hit_z")
    e.emit(b"\x00\x00")
    e.label("projectile_saved_old_x")
    e.emit(b"\x00\x00")
    e.label("projectile_saved_old_y")
    e.emit(b"\x00\x00")
    e.label("projectile_saved_old_z")
    e.emit(b"\x00\x00")
    e.label("projectile_saved_player_yaw")
    e.emit(b"\x00\x00")
    e.label("projectile_fade_timer")
    e.emit(b"\x00\x00\x00\x00")
    e.label("projectile_fade_started")
    e.emit(b"\x00")
    e.label("glyph_fx_latch")
    e.emit(b"\x00")
    e.label("glyph_pose_wait")
    e.emit(b"\x00\x00\x00\x00")
    # Persistent donor block: the installed host-animation helper only needs
    # an Instance-shaped object whose x64 object field is at +0x30.  The
    # object pointer is filled after GLFHOST reports OBJECTTRACKER_USED.
    e.label("glyph_host_instance")
    e.emit(b"\x00" * 0x30)
    e.label("glyph_host_object")
    e.emit(b"\x00" * 8)
    e.label("glyph_host_name")
    e.emit(b"glfhost\x00")
    return e.finish(), hook_meta


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
        "<8sIIIIIIHHI", b".pce\0\0\0\0", len(payload), rva, raw_size, raw_ptr, 0, 0, 0, 0, 0xE0000020
    )
    struct.pack_into("<H", raw, nt + 4 + 2, pe.FILE_HEADER.NumberOfSections + 1)
    struct.pack_into("<I", raw, nt + 4 + 20 + 56, align(rva + len(payload), pe.OPTIONAL_HEADER.SectionAlignment))
    return rva


def patch(input_path: Path, output_path: Path, force_forward: bool, speed: int,
          turn: int, animation_only: bool, soul_link: bool = False) -> None:
    if not 0 <= speed <= 0xFFFF:
        raise SystemExit("--speed must be between 0 and 65535")
    if not 0 < turn <= 0xFFFF:
        raise SystemExit("--turn must be between 1 and 65535")
    pe = pefile.PE(str(input_path), fast_load=True)
    raw = bytearray(input_path.read_bytes())
    last = max(pe.sections, key=lambda s: s.VirtualAddress + s.Misc_VirtualSize)
    section_rva = align(last.VirtualAddress + last.Misc_VirtualSize, pe.OPTIONAL_HEADER.SectionAlignment)
    payload, hooks = build_payload(section_rva, force_forward, speed, turn, animation_only, soul_link)
    new_rva = append_section(raw, pe, payload)
    # build_payload was based at section_rva, which equals new_rva.
    for entry, original, length in HOOKS:
        off = pe.get_offset_from_rva(entry)
        actual = bytes(raw[off : off + length])
        if actual != original:
            raise SystemExit(f"guard failed at 0x{entry:X}: {actual.hex(' ')}")
        target = new_rva + hooks[entry]
        disp = target - (entry + 5)
        raw[off : off + 5] = b"\xe9" + struct.pack("<i", disp)
        if length > 5:
            raw[off + 5 : off + length] = b"\x90" * (length - 5)
    trigger_off = pe.get_offset_from_rva(POSSESSION_TRIGGER_RVA)
    actual_trigger = bytes(raw[trigger_off : trigger_off + len(POSSESSION_TRIGGER_EXPECTED)])
    if actual_trigger != POSSESSION_TRIGGER_EXPECTED:
        raise SystemExit(
            f"guard failed at trigger 0x{POSSESSION_TRIGGER_RVA:X}: "
            f"{actual_trigger.hex(' ')}"
        )
    trigger_target = new_rva + hooks[POSSESSION_TRIGGER_RVA]
    raw[trigger_off : trigger_off + 5] = b"\xe8" + struct.pack(
        "<i", trigger_target - (POSSESSION_TRIGGER_RVA + 5)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(raw)
    print(f"wrote {output_path}; .pce RVA 0x{new_rva:X}, {len(payload)} bytes")
    print("diagnostic mode: forced forward" if force_forward else "entry control enabled")
    mode = "animation-only" if animation_only else f"explicit movement speed {speed}"
    link_mode = "enabled continuously while possessed" if soul_link else "disabled"
    print(f"possessed movement: {mode}; turn step {turn}; Raziel freeze hook: enabled; soul link: {link_mode}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--force-forward", action="store_true")
    ap.add_argument("--speed", type=int, default=50,
                    help="possessed movement speed written to MonsterVars (+0x238), default 50")
    ap.add_argument("--turn", type=lambda value: int(value, 0), default=0x100,
                    help="turn angle added/subtracted per frame while Left/Right is held (default 0x100)")
    ap.add_argument("--animation-only", action="store_true",
                    help="do not write speed or call MON_MoveForward; use the walk clip's native motion")
    ap.add_argument("--soul-link", action="store_true",
                    help="test the native soul-drain dot/lightning link on possessed attacks")
    args = ap.parse_args()
    patch(args.input, args.output, args.force_forward, args.speed, args.turn,
          args.animation_only, args.soul_link)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
