#!/usr/bin/env python3
"""Compare reciprocal stream-portal frames between two Soul Reaver scenes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from level_editor import _compare_stream_portals, _scene_alignment_report
except ModuleNotFoundError:
    from tools.level_editor import _compare_stream_portals, _scene_alignment_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", type=Path, required=True, help="first DRM/PCM scene")
    parser.add_argument("--right", type=Path, required=True, help="reciprocal DRM/PCM scene")
    parser.add_argument("--json", action="store_true", help="emit the full comparison as JSON")
    parser.add_argument("--alignment", action="store_true", help="include translated room bounds and BSP roots")
    args = parser.parse_args()
    report = _scene_alignment_report(args.left, args.right) if args.alignment else _compare_stream_portals(args.left, args.right)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    comparison = report["portal_comparison"] if args.alignment else report
    print(f"Left stream unit:  {comparison['left_stream_unit_id']}")
    print(f"Right stream unit: {comparison['right_stream_unit_id']}")
    matches = comparison["matches"]
    for match in matches:
        left = match["left"]
        right = match.get("right")
        if right is None:
            print(f"{left['name']}: unmatched")
            continue
        print(
            f"{left['name']} <-> {right['name']}: {match['status']} "
            f"max_delta={match['max_abs_delta']} "
            f"bounds_min={match['bounds_min_delta']} bounds_max={match['bounds_max_delta']} "
            f"alignment_translation={match['alignment_translation']}"
        )
    if args.alignment:
        for pair in report["pairs"]:
            print(f"{pair['left_portal']} <-> {pair['right_portal']}: translation={pair['translation']}")
            for root in pair["bsp_root_pairs"]:
                print(f"  BSP tree {root['index']} center_delta={root['center_delta']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
