"""Exercise the HTTP Apply boundary using disposable scene copies only."""
import hashlib
import json
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from tools.level_editor import _parse_scene_objects
from tools.level_viewer import ViewerHandler


SCENE = Path(r"F:\SteamLibrary\steamapps\common\Soul Reaver I-II\1\AREA\CITY\BIN\CITY9.PCM")


@unittest.skipUnless(SCENE.is_file(), "Steam test asset is not installed")
class ViewerApplyApiTests(unittest.TestCase):
    def test_native_apply_with_object_names_and_repeat(self):
        with tempfile.TemporaryDirectory(prefix="soul-re-api-test-") as directory:
            root = Path(directory)
            target = root / "scene.pcm"
            shutil.copyfile(SCENE, target)
            handler = type("TestViewerHandler", (ViewerHandler,), {
                "root": root, "write_root": root, "write_token": "test-token",
                "log_message": lambda *args: None,
            })
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                native = _parse_scene_objects(target)[0]
                moved = dict(native, x=native["x"] + 1)

                def apply():
                    payload = {
                        "path": target.name,
                        "source_sha1": hashlib.sha1(target.read_bytes()).hexdigest(),
                        "placements": [moved], "remove_runtime_ids": [],
                    }
                    request = Request(
                        f"http://127.0.0.1:{server.server_port}/api/apply-placements",
                        data=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json", "X-Soul-Re-Token": "test-token"},
                    )
                    with urlopen(request, timeout=10) as response:
                        self.assertEqual(response.status, 200)
                        return json.load(response)

                first = apply()
                self.assertTrue(first["written"])
                self.assertGreaterEqual(first["changed"], 1)
                self.assertTrue(Path(first["backup"]).is_file())
                self.assertEqual(_parse_scene_objects(target)[0]["x"], moved["x"])
                content = target.read_bytes()
                second = apply()
                self.assertFalse(second["written"])
                self.assertEqual(second["changed"], 0)
                self.assertEqual(target.read_bytes(), content)
                self.assertEqual(len(list(root.glob("*.bak-*"))), 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
