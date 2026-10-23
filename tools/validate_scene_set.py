#!/usr/bin/env python3
"""Validate a staged City 9/10/11 scene set without modifying any files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from level_editor import _scene_alignment_report
except ModuleNotFoundError:
    from tools.level_editor import _scene_alignment_report


def _validate_pair(left: Path, right: Path, expected_left: str, expected_right: str) -> dict[str, object]:
    report = _scene_alignment_report(left, right)
    reciprocal = []
    for match in report["portal_comparison"]["matches"]:
        if match.get("right") is not None:
            reciprocal.append(match)
    if not reciprocal:
        raise ValueError(f"no reciprocal portal connects {left.name} to {right.name}")
    return {
        "left": str(left),
        "right": str(right),
        "expected_left": expected_left,
        "expected_right": expected_right,
        "reciprocal_portals": [
            {
                "left": match["left"]["name"],
                "right": match["right"]["name"],
                "status": match["status"],
                "raw_max_delta": match["max_abs_delta"],
                "alignment_translation": match["alignment_translation"],
            }
            for match in reciprocal
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city9", type=Path, required=True)
    parser.add_argument("--city10", type=Path, required=True)
    parser.add_argument("--city11", type=Path, required=True)
    parser.add_argument("--json", action="store_true", help="emit the validation report as JSON")
    args = parser.parse_args()
    for path in (args.city9, args.city10, args.city11):
        if not path.is_file():
            parser.error(f"scene not found: {path}")
    report = {
        "format": "soul-re-city-scene-set-v1",
        "city9_to_city10": _validate_pair(args.city9, args.city10, "city9", "city10"),
        "city10_to_city11": _validate_pair(args.city10, args.city11, "city10", "city11"),
    }
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    for key, value in report.items():
        if key == "format":
            continue
        print(key)
        for pair in value["reciprocal_portals"]:
            print(
                f"  {pair['left']} <-> {pair['right']}: {pair['status']} "
                f"raw_max_delta={pair['raw_max_delta']} "
                f"translation={pair['alignment_translation']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
