#!/usr/bin/env python3
"""Report Raziel's native glyph string animations and remaster SRA clips.

The glyph icon object is a menu/spell object.  Raziel's pose table lives in
the Raziel object data instead, where each SAnim points at a VAnim containing
the three G2 keylist IDs used by the remaster RAZIEL.SRA archive.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

try:
    from level_editor import _module_offset
    from level_viewer import _sra_animation_catalog
except ImportError:  # pragma: no cover - direct invocation from repository root
    from tools.level_editor import _module_offset
    from tools.level_viewer import _sra_animation_catalog


def read_glyph_table(path: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    data = path.read_bytes()
    module_offset, relocations = _module_offset(data)

    def u32(relative: int) -> int:
        return struct.unpack_from("<I", data, module_offset + relative)[0]

    # Object.data is the pointer at Object+0x1C. RazielData.stringAnimations
    # is the pointer at RazielData+0x20.
    object_data = u32(0x1C)
    string_table = u32(object_data + 0x20)
    entries: list[dict[str, object]] = []
    cursor = string_table
    for index in range(7):
        s_anim = u32(cursor)
        cursor += 4
        if s_anim == 0:
            break
        anim_ptr, next_ptr, mode, timer_data, speed, pad = struct.unpack_from(
            "<IIhhhh", data, module_offset + s_anim
        )
        v_anim = list(data[module_offset + anim_ptr : module_offset + anim_ptr + 8])
        entries.append(
            {
                "string_index": index,
                "sanim_offset": s_anim,
                "next_sanim_offset": next_ptr,
                "vanim_offset": anim_ptr,
                "g2_keylists": v_anim[:3],
                "frames": v_anim[3],
                "mode": v_anim[4],
                "alpha": v_anim[5],
                "frame": int.from_bytes(bytes(v_anim[6:8]), "little"),
                "timer_data": timer_data,
                "speed_adjust": speed,
                "pad": pad,
            }
        )
    return (
        {
            "path": str(path),
            "module_offset": module_offset,
            "relocation_count": relocations,
            "object_data_offset": object_data,
            "string_animation_table_offset": string_table,
        },
        entries,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raziel-drm", type=Path, required=True)
    parser.add_argument("--raziel-sra", type=Path, required=True)
    args = parser.parse_args()

    meta, entries = read_glyph_table(args.raziel_drm)
    catalog = _sra_animation_catalog(args.raziel_sra)
    clips = {int(item["index"]): item for item in catalog["clips"]}
    for entry in entries:
        entry["clips"] = [clips.get(int(key)) for key in entry["g2_keylists"]]
    print(json.dumps({"raziel": meta, "sra": catalog["summary"], "glyph_animations": entries}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
