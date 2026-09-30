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


REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_job(args, job_id):
    """Run bridge/render_job.py the way the job manager does and return its result line."""
    import json
    import subprocess

    import bpy

    prep = ok("render_prepare", job_id=job_id)
    entry = os.path.join(REPO, "bridge", "render_job.py")
    # stdin stays open for the whole render: the job exits when it closes, like under ghostblend.
    proc = subprocess.Popen([bpy.app.binary_path, "-b", prep["scene"], "--python", entry, "--", json.dumps(args)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    out = proc.stdout.read().decode("utf-8", "replace")
    proc.wait(timeout=300)
    proc.stdin.close()
    line = next((l for l in out.splitlines() if l.startswith("@@gbjob:")), None)
    assert line, f"no result line; output tail: {out[-500:]}"
    return json.loads(line[len("@@gbjob:"):])


def magic(path, n=4):
    with open(path, "rb") as f:
        return f.read(n)


def corner_rgba(path):
    """Bottom-left pixel of an image file as RGBA floats."""
    import bpy

    img = bpy.data.images.load(path, check_existing=False)
    try:
        return [round(v, 3) for v in img.pixels[0:4]]
    finally:
        bpy.data.images.remove(img)


class RenderFormatTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")
        ok("add_primitive", type="monkey", name="M")
        self.out = os.path.join(history.S["dir"], "renders", "formats")

    def test_jpeg_from_extension(self):
        path = os.path.join(self.out, "still.jpg")
        res = run_job({"output_path": path, "engine": "workbench", "resolution": [64, 48]}, "f1")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["outputs"][0]["format"], "jpeg")
        self.assertEqual(magic(path, 2), b"\xff\xd8")

    def test_multilayer_exr(self):
        path = os.path.join(self.out, "layers.exr")
        res = run_job({"output_path": path, "engine": "cycles", "samples": 1, "device": "cpu",
                       "resolution": [32, 32], "format": "exr_multilayer"}, "f2")
        self.assertTrue(res["ok"], res)
        self.assertEqual(magic(path), b"\x76\x2f\x31\x01")

    def test_mp4_animation(self):
        ok("animate", object="M", property="location", keys=[{"frame": 1, "value": [0, 0, 0]}, {"frame": 4, "value": [1, 0, 0]}])
        res = run_job({"output_path": os.path.join(self.out, "clip_"), "engine": "workbench", "resolution": [64, 64],
                       "format": "mp4", "frame_start": 1, "frame_end": 4}, "f3")
        self.assertTrue(res["ok"], res)
        video = res["outputs"][0]["animation"]
        self.assertTrue(video.endswith(".mp4") and os.path.getsize(video) > 0, res)

    def test_mp4_needs_a_frame_range(self):
        res = run_job({"output_path": os.path.join(self.out, "x.mp4"), "engine": "workbench"}, "f4")
        self.assertFalse(res["ok"])
        self.assertIn("frame_start", res["error"])

    def _transparent_scene(self):
        # Like many product files: transparent film over a coloured world.
        ok("run_python", code=(
            "bpy.context.scene.render.film_transparent = True\n"
            "bpy.context.scene.world.color = (0.8, 0.2, 0.2)\n"))

    def test_png_keeps_the_scenes_transparent_film(self):
        self._transparent_scene()
        path = os.path.join(self.out, "keep_alpha.png")
        res = run_job({"output_path": path, "engine": "workbench", "resolution": [32, 32]}, "f6")
        self.assertTrue(res["ok"], res)
        self.assertEqual(corner_rgba(path)[3], 0.0, "transparent film lost its alpha")

    def test_transparent_false_shows_the_background(self):
        self._transparent_scene()
        path = os.path.join(self.out, "opaque.png")
        res = run_job({"output_path": path, "engine": "workbench", "resolution": [32, 32],
                       "transparent": False}, "f7")
        self.assertTrue(res["ok"], res)
        rgba = corner_rgba(path)
        self.assertEqual(rgba[3], 1.0)
        self.assertGreater(rgba[0], 0.3, f"expected the red world background, got {rgba}")

    def test_png_sequence_and_auto_camera(self):
        res = run_job({"output_path": os.path.join(self.out, "seq", "f_"), "engine": "workbench",
                       "resolution": [32, 32], "frame_start": 1, "frame_end": 2}, "f5")
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(res["outputs"][0]["files"]), 2)


if __name__ == "__main__":
    unittest.main()
