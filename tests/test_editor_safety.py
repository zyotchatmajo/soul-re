from __future__ import annotations

import struct
import json
import tempfile
import unittest
from pathlib import Path

from tools.level_editor import (
    _apply_managed_scene_placements,
    _apply_scene_lights,
    _compare_stream_portals,
    _append_object_names,
    _append_scene_placements,
    _collect_scene_identifiers,
    _parse_scene_markers,
    _parse_scene_objects,
    _parse_scene_lights,
    _parse_bsp_roots,
    _parse_stream_portals,
    _patch_scene,
    _scene_alignment_report,
    ensure_distinct_paths,
)
from tools.level_viewer import _event_report, _patch_events
from tools.nudge_portal import COORDINATE_OFFSETS


AREA = Path(r"F:\SteamLibrary\steamapps\common\Soul Reaver I-II\1\AREA")
CITY9 = AREA / "CITY" / "BIN" / "CITY9.PCM"
CITY10 = AREA / "CITY" / "BIN" / "CITY10.PCM"
CITY11 = AREA / "CITY" / "BIN" / "CITY11.PCM"
UNDRCT9 = AREA / "UNDRCT" / "BIN" / "UNDRCT9.PCM"


@unittest.skipUnless(CITY9.is_file() and CITY10.is_file() and CITY11.is_file(), "Steam test assets are not installed")
class EditorSafetyTests(unittest.TestCase):
    def test_placement_is_unique_idempotent_and_z_yaw(self) -> None:
        placement = {
            "kind": "object",
            "asset": "audit_test.srm",
            "name": "audit_test",
            "x": 100,
            "y": 200,
            "z": 300,
            "rotation": 90,
        }
        with tempfile.TemporaryDirectory(prefix="soul-re-editor-test-") as temp:
            first = Path(temp) / "first.pcm"
            second = Path(temp) / "second.pcm"
            reserved = _collect_scene_identifiers(AREA)
            first_result = _append_scene_placements(CITY9, [placement], first, reserved)
            second_result = _append_scene_placements(first, [placement], second, reserved)
            self.assertFalse(first_result[0]["existing"])
            self.assertTrue(second_result[0]["existing"])
            self.assertEqual(
                sum(m.kind == "intro" for m in _parse_scene_markers(first)),
                sum(m.kind == "intro" for m in _parse_scene_markers(second)),
            )
            self.assertEqual(first.read_bytes(), second.read_bytes())
            data = second.read_bytes()
            module_offset = struct.unpack_from("<I", data, 0)[0]
            module_offset = (4 + module_offset * 4 + 0x7FF) & ~0x7FF
            terrain = module_offset + struct.unpack_from("<I", data, module_offset)[0]
            intro_relative = struct.unpack_from("<I", data, terrain + 0x0C)[0]
            intro_count = struct.unpack_from("<i", data, terrain + 0x08)[0]
            record = module_offset + intro_relative + (intro_count - 1) * 0x4C
            self.assertEqual(struct.unpack_from("<hhh", data, record + 0x18), (0, 0, 1024))
            self.assertEqual(data[record + 0x40 : record + 0x4C], b"\0" * 12)

    def test_stream_portals_are_exposed_with_all_frames(self) -> None:
        portals = _parse_stream_portals(CITY9)
        self.assertTrue(portals)
        portal = portals[0]
        self.assertIn("signal_id", portal)
        self.assertIn("stream_id", portal)
        self.assertEqual(len(portal["t1"]), 3)
        self.assertEqual(len(portal["t2"]), 3)
        self.assertEqual(len(portal["bounds_min"]), 3)

    def test_native_city9_city10_portal_frames_match(self) -> None:
        report = _compare_stream_portals(CITY9, CITY10)
        matched = [item for item in report["matches"] if item.get("right")]
        self.assertTrue(matched)
        self.assertTrue(any(item["status"] == "exact" for item in matched))

    def test_native_city10_city11_portal_frames_match(self) -> None:
        report = _compare_stream_portals(CITY10, CITY11)
        matched = [item for item in report["matches"] if item.get("right")]
        self.assertTrue(matched)
        self.assertTrue(any(item["status"] == "exact" for item in matched))

    def test_bsp_roots_are_exposed(self) -> None:
        roots = _parse_bsp_roots(CITY9)
        self.assertGreaterEqual(len(roots), 2)
        self.assertEqual(len(roots[0]["sphere"]), 4)
        self.assertEqual(len(roots[0]["box"]), 6)

    def test_scene_alignment_reports_transformed_roots(self) -> None:
        report = _scene_alignment_report(CITY9, CITY10)
        self.assertTrue(report["pairs"])
        self.assertIn("bsp_root_pairs", report["pairs"][0])

    def test_multispline_marker_move_is_rejected(self) -> None:
        marker = next(m for m in _parse_scene_markers(CITY9) if m.kind == "intro" and m.index == 43)
        payload = {"format": "soul-re-scene-v1", "markers": [marker.__dict__ | {"x": marker.x + 1}]}
        with tempfile.TemporaryDirectory(prefix="soul-re-editor-test-") as temp:
            scene = Path(temp) / "scene.json"
            output = Path(temp) / "out.pcm"
            scene.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                _patch_scene(CITY9, scene, output)

    def test_binary_editors_reject_in_place_output(self) -> None:
        with self.assertRaises(ValueError):
            ensure_distinct_paths(CITY9, CITY9)

    def test_object_name_table_is_updated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="soul-re-editor-test-") as temp:
            first = Path(temp) / "first.pcm"
            second = Path(temp) / "second.pcm"
            _append_scene_placements(
                CITY9,
                [{"kind": "object", "asset": "audit_test.srm", "name": "audit_test", "x": 1, "y": 2, "z": 3}],
                first,
                _collect_scene_identifiers(AREA),
            )
            self.assertEqual(_append_object_names(first, ["audit_test"], second), ["audit_test"])

    def test_managed_placements_update_remove_and_reapply_idempotently(self) -> None:
        with tempfile.TemporaryDirectory(prefix="soul-re-editor-test-") as temp:
            root = Path(temp)
            first, second, third, removed = (root / name for name in ("first.pcm", "second.pcm", "third.pcm", "removed.pcm"))
            placement = {
                "kind": "object",
                "asset": "audit_test.srm",
                "runtime_name": "audit_runtime",
                "x": 100,
                "y": 200,
                "z": 300,
                "rotation": 90,
            }
            first_result = _apply_managed_scene_placements(CITY9, [placement], [], first, _collect_scene_identifiers(AREA))
            runtime_id = first_result["assigned"][0]["runtime_id"]
            moved = dict(placement, runtime_id=runtime_id, x=111)
            _apply_managed_scene_placements(first, [moved], [], second, _collect_scene_identifiers(AREA))
            repeat = _apply_managed_scene_placements(second, [moved], [], third, _collect_scene_identifiers(AREA))
            self.assertEqual(repeat["changed"], 0)
            self.assertEqual(second.read_bytes(), third.read_bytes())
            removed_result = _apply_managed_scene_placements(second, [], [runtime_id], removed, _collect_scene_identifiers(AREA))
            self.assertEqual(removed_result["removed"], [runtime_id])
            self.assertEqual(
                sum(m.kind == "intro" for m in _parse_scene_markers(removed)),
                sum(m.kind == "intro" for m in _parse_scene_markers(CITY9)),
            )

    def test_native_intro_can_be_explicitly_updated(self) -> None:
        native = _parse_scene_objects(CITY9)[0]
        with tempfile.TemporaryDirectory(prefix="soul-re-editor-test-") as temp:
            output = Path(temp) / "native-moved.pcm"
            moved = dict(native, x=int(native["x"]) + 1)
            result = _apply_managed_scene_placements(CITY9, [moved], [], output, _collect_scene_identifiers(AREA))
            self.assertEqual(result["changed"], 1)
            changed = next(item for item in _parse_scene_objects(output) if item["runtime_id"] == native["runtime_id"])
            self.assertEqual(changed["x"], moved["x"])


class PortalLayoutTests(unittest.TestCase):
    def test_all_portal_coordinate_vectors_are_shifted(self) -> None:
        self.assertEqual(COORDINATE_OFFSETS, (0x18, 0x20, 0x2C, 0x34, 0x3C, 0x44, 0x4C, 0x54))


@unittest.skipUnless(UNDRCT9.is_file(), "Steam light test asset is not installed")
class LightSafetyTests(unittest.TestCase):
    def test_native_light_position_update_is_idempotent(self) -> None:
        lights = _parse_scene_lights(UNDRCT9)
        self.assertTrue(lights)
        with tempfile.TemporaryDirectory(prefix="soul-re-light-test-") as temp:
            first = Path(temp) / "first.pcm"
            second = Path(temp) / "second.pcm"
            moved = dict(lights[0], x=int(lights[0]["x"]) + 2)
            self.assertEqual(_apply_scene_lights(UNDRCT9, [moved], first), 1)
            self.assertEqual(_apply_scene_lights(first, [moved], second), 0)
            self.assertEqual(first.read_bytes(), second.read_bytes())


@unittest.skipUnless(
    (AREA / "MRLOCK" / "BIN" / "MRLOCK6.PCM").is_file(),
    "Steam event test asset is not installed",
)
class EventSafetyTests(unittest.TestCase):
    def test_event_round_trip_without_script_mutation(self) -> None:
        source = AREA / "MRLOCK" / "BIN" / "MRLOCK6.PCM"
        with tempfile.TemporaryDirectory(prefix="soul-re-event-test-") as temp:
            output = Path(temp) / "mrlock6.pcm"
            self.assertEqual(_patch_events(source, {"patches": []}, output), 0)
            self.assertEqual(len(_event_report(source)["events"]), len(_event_report(output)["events"]))

    def test_script_word_edits_are_rejected(self) -> None:
        source = AREA / "MRLOCK" / "BIN" / "MRLOCK6.PCM"
        report = _event_report(source)
        event = report["events"][0]
        with tempfile.TemporaryDirectory(prefix="soul-re-event-test-") as temp:
            with self.assertRaises(ValueError):
                _patch_events(
                    source,
                    {
                        "patches": [
                            {
                                "event_index": event["index"],
                                "kind": "script_word",
                                "action_index": 0,
                                "script": "action",
                                "word_index": 0,
                                "value": 0,
                            }
                        ]
                    },
                    Path(temp) / "mrlock6.pcm",
                )


if __name__ == "__main__":
    unittest.main()
