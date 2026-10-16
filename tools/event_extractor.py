#!/usr/bin/env python3
"""Extract Soul Reaver level puzzle/event data into JSON or readable Markdown.

The level ``PuzzleInstances`` graph is relocatable, just like the terrain and
intro arrays.  This tool follows that graph, resolves object references to
intro names where possible, and preserves the original p-code words alongside
best-effort opcode/attribute labels.  It is intentionally an inspection tool;
it does not modify the asset.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
from pathlib import Path
from typing import Any

try:  # direct ``python tools/event_extractor.py`` invocation
    from level_editor import _module_offset, _parse_drm, _parse_scene_markers
except ModuleNotFoundError:  # imported as ``tools.event_extractor``
    from tools.level_editor import _module_offset, _parse_drm, _parse_scene_markers


EVENT_OBJECT_NAMES = {
    0: "none",
    1: "instance",
    2: "wildcard",
    3: "event",
    4: "terrain_group",
    5: "area",
    6: "signal",
    7: "vm_object",
}

# EVENT_ParseOpcode stores opcodes as one-based short values.  These labels
# mirror the switch in src/Game/EVENT.c; the game-specific operands are kept
# raw because their widths depend on the selected attribute.
PCODE_NAMES = {
    1: "push_object",
    2: "modify_attribute",
    3: "execute_equals",
    4: "compare_equal",
    5: "execute_stack",
    7: "game_object",
    8: "end_or_unknown",
    9: "end_script",
    10: "end_script",
    12: "push_number",
    13: "add",
    14: "subtract",
    15: "multiply",
    16: "divide",
    17: "increment_compare",
    18: "decrement_compare",
    19: "greater_than",
    20: "not_less_than",
    21: "less_than",
    22: "not_greater_than",
    23: "modulo",
    24: "compare_not_equal",
    28: "game_object",
    29: "player_object",
    30: "sublist_object",
    31: "mark_action_conditional",
    32: "bitwise_and_test",
}

MATH_NAMES = {
    1: "+",
    2: "-",
    3: "*",
    4: "/",
    5: "%",
    6: ">",
    7: "<",
    8: ">=",
    9: "<=",
    10: "==",
    11: "!=",
    12: "bit-test",
}

# These groups are deliberately based on the implementations in EVENT.c, not
# on names guessed from the serialized data.  They make the report useful to a
# human without pretending that the bytecode is a decompiled source script.
ATTRIBUTE_SEMANTICS = {
    "voice": {
        "ATTR_VOICEPLAY",
        "ATTR_VOICEVOL",
        "ATTR_VOICEISDONE",
        "ATTR_HASVOICESTARTED",
        "ATTR_WAITFORVOICEDONE",
    },
    "sound_effect": {"ATTR_SOUND", "ATTR_SOUNDALARM", "ATTR_SET_VOLUME", "ATTR_SET_PITCH", "ATTR_MOVEVOLUME", "ATTR_MOVEPITCH"},
    "movement": {
        "ATTR_POSITION",
        "ATTR_X",
        "ATTR_Y",
        "ATTR_Z",
        "ATTR_ROTATION",
        "ATTR_MOVEZ",
        "ATTR_MOVEZSNAP",
        "ATTR_WATERMOVEZ",
        "ATTR_WATERMOVEZSNAP",
        "ATTR_MOVESNAP",
        "ATTR_MOVETOPOS",
        "ATTR_SETPOSITION",
        "ATTR_SETROTATION",
        "ATTR_MOVETOROT",
        "ATTR_MOVETOROLL",
        "ATTR_ROTATETOWARD",
        "ATTR_ANIMTOPOS",
    },
    "camera": {
        "ATTR_CAMERA",
        "ATTR_SMOOTH",
        "ATTR_SPLINE0",
        "ATTR_SPLINE1",
        "ATTR_TILT",
        "ATTR_DISTANCE",
        "ATTR_RESTOREMODE",
        "ATTR_SETFOCUS",
        "ATTR_SCREENWIPE",
        "ATTR_SHAKE",
        "ATTR_ROLL",
        "ATTR_MOVETOROLL",
        "ATTR_LOOKAT",
        "ATTR_STOPLOOKAT",
    },
    "animation": {
        "ATTR_ANIMATE",
        "ATTR_ANIMATION",
        "ATTR_SETCLIP",
        "ATTR_SETACTION",
        "ATTR_FRAME",
        "ATTR_PLAY",
        "ATTR_PLAYLOOP",
        "ATTR_PLAYTO",
        "ATTR_FORWARD",
        "ATTR_BACKWARD",
        "ATTR_INTERPFRAMES",
    },
    "event_flow": {"ATTR_CALL", "ATTR_TIMER", "ATTR_SKIP_PUPPET_SHOW", "ATTR_PLAYMOVIE"},
    "player_state": {
        "ATTR_SPECTRAL",
        "ATTR_MATERIAL",
        "ATTR_FULL_HEALTH",
        "ATTR_RAZ_HEALTH",
        "ATTR_RAZ_MANA",
        "ATTR_PLAYEREVENT",
        "ATTR_PLAYEREVENTHISTORY",
        "ATTR_HOLDING_ID",
        "ATTR_ONGROUND",
    },
}

COMPARISON_OPCODES = {
    "compare_equal",
    "compare_not_equal",
    "increment_compare",
    "decrement_compare",
    "greater_than",
    "not_less_than",
    "less_than",
    "not_greater_than",
    "bitwise_and_test",
    "execute_equals",
}


def _attribute_names() -> dict[int, str]:
    """Read the authoritative ATTR_ENUM names from the checked-in header."""
    header = Path(__file__).resolve().parents[1] / "include" / "Game" / "EXTRA.h"
    names: dict[int, str] = {}
    in_enum = False
    for line in header.read_text(encoding="utf-8").splitlines():
        if "enum ATTR_ENUM" in line:
            in_enum = True
            continue
        if in_enum and line.strip().startswith("};"):
            break
        if not in_enum:
            continue
        match = re.search(r"\b(ATTR_[A-Z0-9_]+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)", line)
        if match:
            names[int(match.group(2), 0)] = match.group(1)
    return names


def _signed(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def _attribute_semantics(name: str | None) -> list[str]:
    if not name:
        return []
    return [group for group, names in ATTRIBUTE_SEMANTICS.items() if name in names]


def _semantic_description(flags: set[str], is_condition: bool = False) -> str:
    descriptions: list[str] = []
    if is_condition:
        descriptions.append("This is a condition; the action line runs only if it evaluates true.")
    if "voice" in flags:
        descriptions.append("It references streamed voice playback or a voice-completion check.")
    if "sound_effect" in flags:
        descriptions.append("It references an instance sound effect and may wait for that sound data to load.")
    if "player_movement" in flags:
        descriptions.append("It targets Raziel/player and reads or changes his position or rotation.")
    elif "movement" in flags:
        descriptions.append("It reads or changes an instance/terrain position, rotation, or movement path.")
    if "camera" in flags:
        descriptions.append("It changes the cinematic camera, spline, focus, or camera timing.")
    if "animation" in flags:
        descriptions.append("It selects, starts, stops, or seeks an instance animation.")
    if "event_flow" in flags:
        descriptions.append("It controls event sequencing, a timer, or a nested event call.")
    if "player_state" in flags:
        descriptions.append("It reads a player state such as material, health, mana, or player-event history.")
    if not descriptions:
        return "No high-level behavior was recognized; inspect the raw p-code words."
    return " ".join(descriptions)


def _annotate_script(script: dict[str, Any], is_condition: bool = False) -> None:
    """Add conservative semantic labels to a script's token stream."""
    flags: set[str] = set()
    if is_condition:
        flags.add("condition")
    last_object_token: dict[str, Any] | None = None
    voice_indices: list[int] = []
    sound_numbers: list[int] = []
    player_movement = False
    tokens = script["tokens"]
    for token_index, token in enumerate(tokens):
        opcode = token["opcode"]
        if opcode in {"push_object", "player_object", "game_object", "sublist_object"}:
            last_object_token = token
            if opcode == "player_object":
                token["semantic"] = ["player_reference"]
                flags.add("player_reference")
            elif opcode == "game_object":
                token["semantic"] = ["global_game_object"]
            elif opcode == "sublist_object":
                token["semantic"] = ["sublist_reference"]
            else:
                token["semantic"] = ["object_reference"]
        elif opcode in COMPARISON_OPCODES:
            token["semantic"] = ["condition_operator"]
            flags.add("comparison")
        if opcode == "modify_attribute":
            attr = token.get("attribute")
            semantic = _attribute_semantics(attr)
            token["semantic"] = semantic
            flags.update(semantic)
            next_token = tokens[token_index + 1] if token_index + 1 < len(tokens) else None
            if attr == "ATTR_VOICEPLAY" and next_token and next_token.get("opcode") == "push_number":
                token["value_hint"] = next_token.get("operand")
                voice_indices.append(next_token.get("operand"))
            elif attr == "ATTR_SOUND" and next_token:
                # ATTR_SOUND consumes one raw short directly in the C parser,
                # so it is not encoded as a normal push_number opcode.
                token["sound_number_hint"] = next_token.get("raw")
                sound_numbers.append(next_token.get("raw"))
            if last_object_token is not None:
                token["target_opcode"] = last_object_token["opcode"]
                if "operand" in last_object_token:
                    token["target_object_index"] = last_object_token["operand"]
                if last_object_token["opcode"] == "player_object":
                    flags.add("player_reference")
                    if "movement" in semantic:
                        player_movement = True
    if player_movement:
        flags.add("player_movement")
    script["semantic_flags"] = sorted(flags)
    if voice_indices:
        script["voice_indices"] = voice_indices
    if sound_numbers:
        script["sound_numbers"] = sound_numbers
    script["human_summary"] = _semantic_description(flags, is_condition=is_condition)
    details: list[str] = []
    if voice_indices:
        details.append("voice index/indices: " + ", ".join(str(value) for value in voice_indices))
    if sound_numbers:
        details.append("instance sound number(s): " + ", ".join(str(value) for value in sound_numbers))
    if details:
        script["human_summary"] += " " + "; ".join(details) + "."


class EventReader:
    def __init__(self, path: Path):
        self.path = path
        self.data = path.read_bytes()
        self.module_offset, self.relocation_count = _module_offset(self.data)
        self.module_size = len(self.data) - self.module_offset
        self.attributes = _attribute_names()
        self.intros = {
            marker.identifier: marker.name
            for marker in _parse_scene_markers(path)
            if marker.kind == "intro"
        }

    def _u32(self, relative: int) -> int:
        if relative < 0 or relative + 4 > self.module_size:
            raise ValueError(f"pointer field 0x{relative:X} is outside the module")
        return struct.unpack_from("<I", self.data, self.module_offset + relative)[0]

    def _i32(self, relative: int) -> int:
        if relative < 0 or relative + 4 > self.module_size:
            raise ValueError(f"integer field 0x{relative:X} is outside the module")
        return struct.unpack_from("<i", self.data, self.module_offset + relative)[0]

    def _range(self, relative: int, size: int, label: str) -> bytes:
        if relative < 0 or size < 0 or relative + size > self.module_size:
            raise ValueError(f"{label} at 0x{relative:X} extends beyond the module")
        start = self.module_offset + relative
        return self.data[start : start + size]

    def _cstring(self, relative: int) -> str:
        if relative == 0:
            return ""
        raw = self._range(relative, min(self.module_size - relative, 256), "string")
        return raw.split(b"\0", 1)[0].decode("ascii", errors="replace")

    def _script(self, relative: int | None, is_condition: bool = False) -> dict[str, Any] | None:
        if not relative:
            return None
        size, condition_bits, data_relative = struct.unpack(
            "<HHI", self._range(relative, 8, "script header")
        )
        raw = self._range(data_relative, size * 2, "script data")
        words = list(struct.unpack("<" + "h" * size, raw)) if size else []
        result = {
            "relative_offset": relative,
            "size_words": size,
            "condition_bits": condition_bits,
            "data_relative_offset": data_relative,
            "words": words,
            "tokens": self._tokens(words),
        }
        _annotate_script(result, is_condition=is_condition)
        return result

    def _tokens(self, words: list[int]) -> list[dict[str, Any]]:
        tokens: list[dict[str, Any]] = []
        index = 0
        while index < len(words):
            raw = words[index]
            unsigned = raw & 0xFFFF
            name = PCODE_NAMES.get(unsigned, f"opcode_{unsigned}")
            token: dict[str, Any] = {"word_index": index, "raw": raw, "opcode": name}
            index += 1
            # These three opcodes have a directly following operand in the
            # serialized stream. Attribute-specific trailing operands remain
            # visible as raw tokens instead of being guessed incorrectly.
            if unsigned in {1, 2, 12, 30} and index < len(words):
                operand = words[index]
                token["operand"] = operand
                if unsigned == 2:
                    token["attribute"] = self.attributes.get(
                        operand & 0xFFFF, f"ATTR_0x{operand & 0xFFFF:X}"
                    )
                    token["attribute_value"] = operand & 0xFFFF
                index += 1
            elif unsigned in {13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 32}:
                token["operation"] = MATH_NAMES.get(unsigned - 12, "unknown")
            tokens.append(token)
        return tokens

    def _event_object(self, relative: int) -> dict[str, Any]:
        object_id = struct.unpack("<h", self._range(relative, 2, "event object id"))[0]
        result: dict[str, Any] = {
            "relative_offset": relative,
            "id": object_id,
            "type": EVENT_OBJECT_NAMES.get(object_id, f"unknown_{object_id}"),
        }
        if object_id == 1:
            object_id, flags, unit_id, intro_id, instance_ptr, data_ptr = struct.unpack(
                "<hhiiII", self._range(relative, 20, "instance object")
            )
            result.update(
                {
                    "flags": flags,
                    "unit_id": unit_id,
                    "intro_unique_id": intro_id,
                    "intro_name": self.intros.get(intro_id),
                    "instance_pointer": instance_ptr,
                    "data_pointer": data_ptr,
                }
            )
        elif object_id == 2:
            _, spad, unit_id, name_ptr = struct.unpack(
                "<hhiI", self._range(relative, 12, "wildcard object")
            )
            result.update({"unit_id": unit_id, "object_name": self._cstring(name_ptr), "name_pointer": name_ptr})
        elif object_id == 3:
            _, event_number, unit_id, event_ptr = struct.unpack(
                "<hhiI", self._range(relative, 12, "event reference")
            )
            result.update({"event_number": event_number, "unit_id": unit_id, "event_pointer": event_ptr})
        elif object_id == 4:
            _, tgroup_number, unit_id, bsp_ptr, stream_ptr = struct.unpack(
                "<hhiII", self._range(relative, 16, "terrain group object")
            )
            result.update(
                {"tgroup_number": tgroup_number, "unit_id": unit_id, "bsp_pointer": bsp_ptr, "stream_pointer": stream_ptr}
            )
        elif object_id == 5:
            _, spad, unit_id, stream_ptr = struct.unpack(
                "<hhiI", self._range(relative, 12, "area object")
            )
            result.update({"unit_id": unit_id, "stream_pointer": stream_ptr})
        elif object_id == 6:
            _, signal_number, unit_id, signal_ptr = struct.unpack(
                "<hhiI", self._range(relative, 12, "signal object")
            )
            result.update({"signal_number": signal_number, "unit_id": unit_id, "signal_pointer": signal_ptr})
        elif object_id == 7:
            _, spad, unit_id = struct.unpack("<hhi", self._range(relative, 8, "VM object"))
            name = self._range(relative + 8, 12, "VM object name").split(b"\0", 1)[0].decode("ascii", "replace")
            vm_ptr = self._u32(relative + 20)
            result.update({"unit_id": unit_id, "vm_object_name": name, "vm_object_pointer": vm_ptr})
        return result

    def read(self) -> dict[str, Any]:
        puzzle_relative = self._u32(0xDC)
        if not puzzle_relative:
            return {"format": "soul-re-events-v1", "path": str(self.path), "puzzle_count": 0, "events": []}
        puzzle_count = self._i32(puzzle_relative)
        if not 0 <= puzzle_count <= 4096:
            raise ValueError(f"unreasonable puzzle count: {puzzle_count}")
        event_rels = [self._u32(puzzle_relative + 4 + index * 4) for index in range(puzzle_count)]
        events: list[dict[str, Any]] = []
        for index, event_relative in enumerate(event_rels):
            event_number, num_instances, num_actions, puppet = struct.unpack(
                "<hhBB", self._range(event_relative, 6, "event header")
            )
            variables = list(struct.unpack("<5h", self._range(event_relative + 6, 10, "event variables")))
            instance_list, conditional_list, action_list = struct.unpack(
                "<III", self._range(event_relative + 16, 12, "event lists")
            )
            if num_instances < 0 or num_instances > 4096 or num_actions > 255:
                raise ValueError(f"unreasonable event sizes at 0x{event_relative:X}")
            objects = []
            for object_index in range(num_instances):
                object_relative = self._u32(instance_list + object_index * 4)
                obj = self._event_object(object_relative)
                obj["index"] = object_index
                objects.append(obj)
            actions = []
            for action_index in range(num_actions):
                condition_relative = self._u32(conditional_list + action_index * 4)
                action_relative = self._u32(action_list + action_index * 4)
                condition = self._script(condition_relative, is_condition=True) if condition_relative else None
                action = self._script(action_relative) if action_relative else None
                for script in (condition, action):
                    if script:
                        for token in script["tokens"]:
                            object_index = token.get("target_object_index")
                            if object_index is not None and 0 <= object_index < len(objects):
                                target = objects[object_index]
                                token["target"] = {
                                    "index": object_index,
                                    "type": target.get("type"),
                                    "intro_name": target.get("intro_name"),
                                    "event_number": target.get("event_number"),
                                    "signal_number": target.get("signal_number"),
                                    "tgroup_number": target.get("tgroup_number"),
                                }
                all_scripts = [script for script in (condition, action) if script]
                attrs = sorted(
                    {
                        token["operand"]
                        for script in all_scripts
                        for token in script["tokens"]
                        if token["opcode"] == "modify_attribute" and "operand" in token
                    }
                )
                actions.append(
                    {
                        "index": action_index,
                        "condition": condition,
                        "action": action,
                        "attributes": [
                            {"value": value, "name": self.attributes.get(value, f"ATTR_0x{value:X}")}
                            for value in attrs
                        ],
                    }
                )
                action_entry = actions[-1]
                semantic_flags = sorted(
                    {
                        flag
                        for script in all_scripts
                        for flag in script.get("semantic_flags", [])
                        if flag != "condition"
                    }
                )
                action_entry["semantic_flags"] = semantic_flags
                action_entry["human_summary"] = _semantic_description(
                    set(semantic_flags), is_condition=condition is not None
                )
                evidence: list[str] = []
                for script in all_scripts:
                    for token in script["tokens"]:
                        attr = token.get("attribute")
                        if attr == "ATTR_VOICEPLAY" and "value_hint" in token:
                            evidence.append(f"voice index {token['value_hint']}")
                        elif attr == "ATTR_SOUND" and "sound_number_hint" in token:
                            evidence.append(f"instance sound {token['sound_number_hint']}")
                        elif attr == "ATTR_CALL" and token.get("target"):
                            target = token["target"]
                            if target.get("event_number") is not None:
                                evidence.append(f"calls event {target['event_number']}")
                        elif attr in ATTRIBUTE_SEMANTICS["movement"] and token.get("target_opcode") == "player_object":
                            evidence.append(f"player movement via {attr}")
                action_entry["evidence"] = list(dict.fromkeys(evidence))
                if evidence:
                    action_entry["human_summary"] += " Evidence: " + "; ".join(dict.fromkeys(evidence)) + "."
            camera_attrs = sorted(
                {
                    attribute["name"]
                    for action in actions
                    for attribute in action["attributes"]
                    if attribute["name"].startswith("ATTR_CAMERA")
                    or attribute["name"] in {"ATTR_SMOOTH", "ATTR_SPLINE0", "ATTR_SPLINE1", "ATTR_TILT", "ATTR_DISTANCE", "ATTR_RESTOREMODE", "ATTR_SETFOCUS", "ATTR_SCREENWIPE", "ATTR_MOVETOPOS", "ATTR_SETPOSITION", "ATTR_SETROTATION", "ATTR_MOVETOROT", "ATTR_LOOKAT", "ATTR_STOPLOOKAT"}
                }
            )
            event_flags = sorted(
                {
                    flag
                    for action in actions
                    for flag in action.get("semantic_flags", [])
                }
            )
            events.append(
                {
                    "index": index,
                    "relative_offset": event_relative,
                    "event_number": event_number,
                    "num_instances": num_instances,
                    "num_actions": num_actions,
                    "processing_puppet_show": puppet,
                    "event_variables": variables,
                    "mode": "normal" if event_number >= 0 else "puppet_show_or_definition",
                    "objects": objects,
                    "actions": actions,
                    "camera_attributes": camera_attrs,
                    "semantic_flags": event_flags,
                    "human_summary": (
                        "This is a puppet-show/definition event: it is dormant until another event calls it. "
                        "Once processing starts, one action line is attempted per game update and a timer, "
                        "voice wait, or sound-load wait can pause it."
                        if event_number < 0
                        else "This is a normal event: its condition/action lines are checked from the event-processing pass."
                    ),
                }
            )
        summary = _parse_drm(self.path)
        return {
            "format": "soul-re-events-v1",
            "path": str(self.path),
            "module_offset": self.module_offset,
            "module_size": self.module_size,
            "relocation_count": self.relocation_count,
            "world_name": summary.world_name,
            "puzzle_relative_offset": puzzle_relative,
            "puzzle_count": puzzle_count,
            "events": events,
        }


def _script_text(script: dict[str, Any] | None) -> str:
    if script is None:
        return "none"
    attrs = list(
        dict.fromkeys(
            token.get("attribute")
            for token in script["tokens"]
            if token.get("opcode") == "modify_attribute" and token.get("attribute")
        )
    )
    suffix = f"; attrs={', '.join(attrs)}" if attrs else ""
    token_text: list[str] = []
    for token in script["tokens"][:48]:
        item = token["opcode"]
        if token.get("opcode") == "modify_attribute":
            item += f"({token.get('attribute', token.get('operand'))})"
        elif "operand" in token:
            item += f"({token['operand']})"
        token_text.append(item)
    if len(script["tokens"]) > 48:
        token_text.append("…")
    flags = ", ".join(script.get("semantic_flags", [])) or "none"
    return (
        f"{script['size_words']} words; semantics={flags}\n"
        f"    meaning: {script.get('human_summary', '')}\n"
        "    pcode: " + ", ".join(token_text)
    )


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Event report: `{Path(report['path']).name}`",
        "",
        f"- World: `{report.get('world_name') or '<unnamed>'}`",
        f"- Puzzle/event count: **{report['puzzle_count']}**",
        f"- Puzzle table: `0x{report['puzzle_relative_offset']:X}` relative to module",
        "",
        "## Runtime model",
        "",
        "The game loads `Level.PuzzleInstances`, then `EVENT_ProcessEvents` walks each event. A non-negative event number is processed immediately. A negative event number is a puppet-show/definition entry: it is dormant until another event uses `ATTR_CALL` on it, which sets `processingPuppetShow`; after that, `EVENT_ProcessPuppetShow` attempts one action line at a time. A line can be gated by a condition script. `ATTR_TIMER`, voice-wait attributes, and sound-loading can set `EventAbortLine`, causing that line to resume later. The scripts are a small stack VM; this report preserves raw 16-bit words and adds conservative semantic labels.",
        "",
        "## How to answer common questions",
        "",
        "- **Voice line:** find `voice` in an event/line's semantics, especially `ATTR_VOICEPLAY`. The following number is the streamed voice index. `ATTR_VOICEISDONE`/`ATTR_WAITFORVOICEDONE` are completion/wait checks. `ATTR_SOUND` is an instance SFX, not a streamed voice line.",
        "- **Raziel moves:** find `player_movement`. It means the script used `player_object` together with a position/rotation/movement attribute such as `ATTR_SETPOSITION`, `ATTR_POSITION`, or `ATTR_SETROTATION`.",
        "- **A condition:** inspect the `condition:` block on the same line. Comparison tokens and the referenced object/attribute show what must be true before the action runs.",
        "- **Camera:** find `camera`; the report lists the camera attributes and the target spline/marker object where it can resolve one.",
        "The labels are static evidence, not a guarantee that the line ran in a particular playthrough. To prove runtime behavior, combine this report with the signal/event that calls the line and the relevant condition values.",
        "",
        "## Events",
        "",
    ]
    for event in report["events"]:
        lines.append(
            f"### Event {event['index']} — number `{event['event_number']}` ({event['mode']})"
        )
        lines.append(
            f"Instances: **{event['num_instances']}** · Actions: **{event['num_actions']}** · Puppet state: `{event['processing_puppet_show']}` · Variables: `{event['event_variables']}`"
        )
        lines.append(f"Meaning: {event.get('human_summary', '')}")
        if event.get("semantic_flags"):
            lines.append(f"Semantic flags: `{', '.join(event['semantic_flags'])}`")
        if event["camera_attributes"]:
            lines.append(f"Camera-related attributes: `{', '.join(event['camera_attributes'])}`")
        lines.append("")
        lines.append("Objects:")
        for obj in event["objects"]:
            details = [f"id={obj['id']} ({obj['type']})"]
            if obj.get("intro_unique_id") is not None:
                label = obj.get("intro_name") or "unknown intro"
                details.append(f"intro={obj['intro_unique_id']}:{label}")
            for key in ("event_number", "signal_number", "tgroup_number", "unit_id", "object_name", "vm_object_name"):
                if key in obj and obj[key] not in (None, ""):
                    details.append(f"{key}={obj[key]}")
            lines.append(f"- `{obj['index']}`: " + ", ".join(details))
        lines.append("")
        lines.append("Actions:")
        for action in event["actions"]:
            attrs = ", ".join(item["name"] for item in action["attributes"]) or "none"
            flags = ", ".join(action.get("semantic_flags", [])) or "none"
            lines.append(f"- **line {action['index']}**; attributes: `{attrs}`; semantics: `{flags}`")
            lines.append(f"  - meaning: {action.get('human_summary', '')}")
            if action.get("evidence"):
                lines.append(f"  - evidence: `{'; '.join(action['evidence'])}`")
            lines.append(f"  - condition: {_script_text(action['condition'])}")
            lines.append(f"  - action: {_script_text(action['action'])}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset", type=Path, help="DRM or area PCM containing Level.PuzzleInstances")
    parser.add_argument("--output", type=Path, help="write report here (default: stdout)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of Markdown")
    args = parser.parse_args(argv)
    report = EventReader(args.asset.resolve()).read()
    text = json.dumps(report, indent=2) + "\n" if args.json else to_markdown(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
