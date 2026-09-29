import os
import unittest

from gb import history
from gbtest import ok


class RenderPrepareTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")

    def test_prepare_snapshots_scene(self):
        ok("add_primitive", type="cube", name="Box")
        res = ok("render_prepare", job_id="r1")
        self.assertTrue(os.path.isfile(res["scene"]), "snapshot .blend not written")
        self.assertTrue(res["scene"].endswith("scene.blend"))
        self.assertEqual(res["objects"], 1)

    def test_prepare_reports_camera_presence(self):
        res = ok("render_prepare", job_id="r2")
        self.assertFalse(res["has_camera"])
        ok("add_primitive", type="camera", name="Cam")
        res = ok("render_prepare", job_id="r3")
        self.assertTrue(res["has_camera"])

    def test_sys_info_lists_engines_and_devices(self):
        res = ok("sys_info")
        self.assertIn("BLENDER_WORKBENCH", res["engines"])
        self.assertIn("CYCLES", res["engines"])
        self.assertIsInstance(res["cycles_devices"], list)


if __name__ == "__main__":
    unittest.main()
