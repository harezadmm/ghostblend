import struct
import unittest

import bpy

from gbtest import call, ok


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    w, h = struct.unpack(">II", head[16:24])
    return w, h


class FramingTests(unittest.TestCase):
    """Camera placement must match Blender's conventions, not just produce a PNG."""

    def _camera(self, view):
        from mathutils import Vector

        from gb import framing

        data = bpy.data.cameras.new("t_" + view)
        cam = bpy.data.objects.new("t_" + view, data)
        bpy.context.scene.collection.objects.link(cam)
        framing.place_camera(cam, view, Vector((-1, -1, -1)), Vector((1, 1, 1)))
        bpy.context.view_layer.update()
        forward = cam.matrix_world.to_quaternion() @ Vector((0, 0, -1))
        loc = cam.location.copy()
        bpy.data.objects.remove(cam)
        bpy.data.cameras.remove(data)
        return loc, forward

    def test_persp_looks_from_front_right_above(self):
        loc, forward = self._camera("persp")
        self.assertGreater(loc.x, 0, "persp camera should be on +X")
        self.assertLess(loc.y, 0, "persp camera should be on -Y (the front)")
        self.assertGreater(loc.z, 0, "persp camera should be above")
        from mathutils import Vector

        # It looks along the fixed front-right-above direction; it is shifted sideways to
        # centre the subject rather than re-aimed, so it still faces the subject closely.
        self.assertAlmostEqual((forward - Vector((-1, 1, -0.6)).normalized()).length, 0, places=4)
        self.assertLess(forward.angle(-loc), 0.1, "persp camera should face the subject")

    def test_persp_fills_the_frame(self):
        # The perspective view is the most informative one; it must not waste the frame
        # on a loose bounding sphere, whatever the shape.
        from bpy_extras.object_utils import world_to_camera_view
        from mathutils import Vector

        from gb import framing

        sc = bpy.context.scene
        res = (sc.render.resolution_x, sc.render.resolution_y)
        sc.render.resolution_x = sc.render.resolution_y = 512  # previews are square
        data = bpy.data.cameras.new("t_fill")
        cam = bpy.data.objects.new("t_fill", data)
        sc.collection.objects.link(cam)
        try:
            shapes = {"cube": ((-1, -1, -1), (1, 1, 1)), "wall": ((-6, -0.5, -6), (6, 0.5, 6)),
                      "bar": ((-8, -0.5, -0.5), (8, 0.5, 0.5))}
            for shape, (lo, hi) in shapes.items():
                mins, maxs = Vector(lo), Vector(hi)
                framing.place_camera(cam, "persp", mins, maxs)
                bpy.context.view_layer.update()
                pts = [world_to_camera_view(sc, cam, Vector((x, y, z)))
                       for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
                for p in pts:
                    self.assertTrue(0 <= p.x <= 1 and 0 <= p.y <= 1 and p.z > 0, f"{shape}: corner outside {tuple(p)}")
                extent = max(max(p.x for p in pts) - min(p.x for p in pts),
                             max(p.y for p in pts) - min(p.y for p in pts))
                self.assertGreater(extent, 0.9, f"{shape}: fills only {extent:.2f} of the frame")
        finally:
            sc.render.resolution_x, sc.render.resolution_y = res
            bpy.data.objects.remove(cam)
            bpy.data.cameras.remove(data)

    def test_axis_views_look_at_center(self):
        expect = {"front": (1, -1), "back": (1, 1), "right": (0, 1), "left": (0, -1),
                  "top": (2, 1), "bottom": (2, -1)}
        for view, (axis, sign) in expect.items():
            loc, forward = self._camera(view)
            self.assertEqual(loc[axis] > 0, sign > 0, f"{view}: camera on the wrong side: {tuple(loc)}")
            self.assertAlmostEqual((forward - (-loc).normalized()).length, 0, places=4, msg=view)


class PreviewTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")
        ok("add_primitive", type="monkey", name="Suzanne")

    def test_default_is_four_view_sheet(self):
        res = ok("render_preview", size=256)
        w, h = png_size(res["image"])
        # 2x2 grid of 256px tiles plus gutters and label bars.
        self.assertGreater(w, 256 * 2)
        self.assertGreater(h, 256 * 2)
        self.assertEqual(res["views"], ["front", "right", "top", "persp"])

    def test_single_view(self):
        res = ok("render_preview", size=256, views=["front"])
        w, h = png_size(res["image"])
        self.assertGreaterEqual(w, 256)
        self.assertLess(w, 256 * 2)

    def test_scene_unchanged_after_preview(self):
        before_engine = bpy.context.scene.render.engine
        before_res = (bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y)
        before_cam = bpy.context.scene.camera
        before_objs = sorted(o.name for o in bpy.context.scene.objects)
        ok("render_preview", size=128, shading="rendered")
        self.assertEqual(bpy.context.scene.render.engine, before_engine)
        self.assertEqual((bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y), before_res)
        self.assertEqual(bpy.context.scene.camera, before_cam)
        self.assertEqual(sorted(o.name for o in bpy.context.scene.objects), before_objs)

    def test_preview_is_not_mutating(self):
        # A preview must not create an autosave.
        from gb import history

        seq_before = history.S["seq"]
        call("render_preview", _autosave=False, size=128, views=["front"])
        self.assertEqual(history.S["seq"], seq_before)

    def test_empty_scene_still_returns_image(self):
        ok("scene_new")
        res = ok("render_preview", size=128, views=["persp"])
        png_size(res["image"])
        self.assertTrue(any("no visible objects" in n.lower() for n in res.get("notes", [])))

    def test_rendered_shading_eevee(self):
        res = ok("render_preview", size=128, views=["persp"], shading="rendered")
        png_size(res["image"])

    def test_cycles_engine(self):
        res = ok("render_preview", size=128, views=["persp"], engine="cycles")
        png_size(res["image"])
        self.assertEqual(res["engine"], "CYCLES")


# Production files often keep assets in collections that are excluded from the view
# layer, switched on one at a time for rendering. Nested, like "Medical > Crutch".
_EXCLUDED_SCENE = """
group = bpy.data.collections.new("Group")
props = bpy.data.collections.new("Props")
bpy.context.scene.collection.children.link(group)
group.children.link(props)
bpy.ops.mesh.primitive_monkey_add(location=(0, 0, 0))
m = bpy.context.object
m.name = "Hidden"
for c in list(m.users_collection):
    c.objects.unlink(m)
props.objects.link(m)
bpy.context.view_layer.layer_collection.children["Group"].exclude = True
"""


class ExcludedCollectionTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")
        ok("run_python", code=_EXCLUDED_SCENE)

    def test_scene_info_says_excluded_objects_do_not_render(self):
        info = ok("scene_info")
        hidden = next(o for o in info["objects"] if o["name"] == "Hidden")["hidden"]
        self.assertTrue(hidden["viewport"])
        self.assertTrue(hidden["render"])
        self.assertIn("Group", hidden["reason"])
        group = next(c for c in info["collections"] if c["name"] == "Group")
        self.assertTrue(group["excluded"])
        self.assertTrue(any("Group" in n and "excluded" in n for n in info["notes"]))

    def test_preview_frames_only_visible_objects(self):
        ok("add_primitive", type="cube", name="Shown", size=1, location=[10, 0, 0])
        res = ok("render_preview", size=64, views=["front"])
        self.assertAlmostEqual(res["bbox"]["min"][0], 9.5, places=2)
        self.assertAlmostEqual(res["bbox"]["max"][0], 10.5, places=2)

    def test_preview_frames_visible_text_and_curves(self):
        ok("run_python", code=(
            "bpy.ops.object.text_add(location=(10, 0, 0))\n"
            "bpy.context.object.name = 'Label'\n"))
        res = ok("render_preview", size=64, views=["front"])
        self.assertGreater(res["bbox"]["min"][0], 9.0)
        self.assertFalse(any("no visible objects" in n.lower() for n in res.get("notes", [])))

    def test_preview_explains_when_nothing_is_visible(self):
        res = ok("render_preview", size=64, views=["front"])
        notes = " ".join(res.get("notes", []))
        self.assertIn("no visible objects", notes.lower())
        self.assertIn("Group", notes)
        self.assertIn("exclude", notes)


if __name__ == "__main__":
    unittest.main()
