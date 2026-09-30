"""Tests for the modelling, sculpting, painting, lighting, baking and animation tools."""
import math
import os
import unittest

import bpy
import numpy as np
from mathutils import Vector

from gbtest import WORKDIR, err, ok, py


def obj(name):
    return bpy.data.objects[name]


def zmax(o):
    bpy.context.view_layer.update()
    return max((o.matrix_world @ v.co).z for v in o.data.vertices)


class EditMeshTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")
        ok("add_primitive", type="cube", name="C")

    def test_extrude_top_face(self):
        res = ok("edit_mesh", object="C", operation="extrude", distance=1.0, select={"facing": [0, 0, 1]})
        self.assertEqual(res["faces_selected"], 1)
        self.assertEqual(res["after"]["verts"], 12)
        self.assertAlmostEqual(res["dimensions"][2], 3.0, places=3)

    def test_inset_top_face(self):
        res = ok("edit_mesh", object="C", operation="inset", thickness=0.2, select={"facing": [0, 0, 1]})
        self.assertEqual(res["after"]["faces"], 10)

    def test_inset_whole_closed_mesh_goes_individual(self):
        res = ok("edit_mesh", object="C", operation="inset", thickness=0.2)
        self.assertEqual(res["after"]["faces"], 30)
        self.assertTrue(res.get("notes"))

    def test_bevel_all_edges(self):
        res = ok("edit_mesh", object="C", operation="bevel", width=0.1, segments=2)
        self.assertGreater(res["after"]["verts"], 8)

    def test_bevel_sharp_filter_with_no_match(self):
        resp = err("edit_mesh", object="C", operation="bevel", sharp_angle_deg=120)
        self.assertIn("No edges", resp["error"]["message"])

    def test_subdivide(self):
        self.assertEqual(ok("edit_mesh", object="C", operation="subdivide", cuts=1)["after"]["verts"], 26)

    def test_loop_cut_is_safe_and_splits(self):
        res = ok("edit_mesh", object="C", operation="loop_cut", axis="z", cuts=2)
        self.assertEqual(res["after"]["verts"], 16)

    def test_bisect_clears_top_half(self):
        res = ok("edit_mesh", object="C", operation="bisect", plane_point=[0, 0, 0], plane_normal=[0, 0, 1],
                 clear="above", fill=True)
        self.assertAlmostEqual(res["dimensions"][2], 1.0, places=3)

    def test_spin(self):
        ok("add_primitive", type="circle", name="Ring", radius=0.3, segments=8, location=[1, 0, 0])
        res = ok("edit_mesh", object="Ring", operation="spin", angle_deg=360, steps=12)
        self.assertGreater(res["after"]["verts"], 8)

    def test_merge_removes_doubles(self):
        py("import bmesh\no = bpy.data.objects['C']; bm = bmesh.new(); bm.from_mesh(o.data)\n"
           "bmesh.ops.duplicate(bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces))\n"
           "bm.to_mesh(o.data); bm.free()")
        self.assertEqual(len(obj("C").data.vertices), 16)
        ok("edit_mesh", object="C", operation="merge", distance=0.001)
        self.assertEqual(len(obj("C").data.vertices), 8)

    def test_delete_top_face(self):
        self.assertEqual(ok("edit_mesh", object="C", operation="delete", select={"facing": [0, 0, 1]})["after"]["faces"], 5)

    def test_triangulate(self):
        self.assertEqual(ok("edit_mesh", object="C", operation="triangulate")["after"]["faces"], 12)

    def test_flip_and_recalc_normals(self):
        ok("edit_mesh", object="C", operation="flip_normals")
        top = max(obj("C").data.polygons, key=lambda p: p.center.z)
        self.assertLess(top.normal.z, 0)
        ok("edit_mesh", object="C", operation="recalc_normals")
        top = max(obj("C").data.polygons, key=lambda p: p.center.z)
        self.assertGreater(top.normal.z, 0)

    def test_shading(self):
        ok("edit_mesh", object="C", operation="shade_smooth")
        self.assertTrue(all(p.use_smooth for p in obj("C").data.polygons))
        ok("edit_mesh", object="C", operation="shade_flat")
        self.assertFalse(any(p.use_smooth for p in obj("C").data.polygons))

    def test_unwrap_methods(self):
        def uv_area():
            me = obj("C").data
            uv = me.uv_layers.active.uv
            total = 0.0
            for p in me.polygons:
                pts = [uv[i].vector for i in p.loop_indices]
                total += abs(sum(a.x * b.y - b.x * a.y for a, b in zip(pts, pts[1:] + pts[:1]))) / 2
            return total
        for method in ("smart", "cube", "angle", "conformal"):
            ok("scene_new")
            ok("add_primitive", type="cube", name="C")
            res = ok("edit_mesh", object="C", operation="unwrap", method=method)
            self.assertTrue(res["uv_layers"], method)
            self.assertGreater(uv_area(), 0.05, f"{method} left degenerate UVs")
            if method in ("angle", "conformal"):
                self.assertTrue(res.get("notes"), "seams should be added automatically")

    def test_box_selection(self):
        res = ok("edit_mesh", object="C", operation="extrude", distance=0.5,
                 select={"box_min": [-5, -5, 0.5], "box_max": [5, 5, 5]})
        self.assertEqual(res["faces_selected"], 1)

    def test_empty_selection_explains(self):
        resp = err("edit_mesh", object="C", operation="extrude", select={"box_min": [50, 50, 50]})
        self.assertIn("select", resp["error"]["hint"])

    def test_non_mesh_rejected(self):
        ok("add_primitive", type="empty", name="E")
        self.assertIn("not a mesh", err("edit_mesh", object="E", operation="subdivide")["error"]["message"])


class SculptTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")
        ok("add_primitive", type="uv_sphere", name="S", radius=1.0, segments=64)
        ok("sculpt", object="S", subdivide=1)

    def test_draw_raises_surface(self):
        before = zmax(obj("S"))
        res = ok("sculpt", object="S", brush="draw", points=[[0, 0, 1]], radius=0.4, strength=1.0, iterations=3)
        self.assertGreater(res["vertices_moved"], 10)
        self.assertGreater(zmax(obj("S")), before + 0.05)

    def test_invert_draw_dents(self):
        before = zmax(obj("S"))
        ok("sculpt", object="S", brush="draw", points=[[0, 0, 1]], radius=0.4, strength=1.0, invert=True, iterations=3)
        self.assertLess(zmax(obj("S")), before - 0.05)

    def test_stroke_along_path(self):
        res = ok("sculpt", object="S", brush="clay", points=[[-0.5, 0, 0.85], [0.5, 0, 0.85]], radius=0.3)
        self.assertGreater(res["stamps"], 3)
        self.assertGreater(res["vertices_moved"], 10)

    def test_inflate_filter_grows_mesh(self):
        before = obj("S").dimensions.x
        ok("sculpt", object="S", brush="inflate", strength=1.0)
        bpy.context.view_layer.update()
        self.assertGreater(obj("S").dimensions.x, before)

    def test_smooth_reduces_noise(self):
        def roughness():
            co = np.array([v.co[:] for v in obj("S").data.vertices])
            return float(np.linalg.norm(co, axis=1).std())
        ok("sculpt", object="S", brush="noise", strength=1.0, seed=3)
        noisy = roughness()
        ok("sculpt", object="S", brush="smooth", strength=1.0, iterations=5)
        self.assertLess(roughness(), noisy)

    def test_grab_moves_area(self):
        before = zmax(obj("S"))
        ok("sculpt", object="S", brush="grab", points=[[0, 0, 1]], radius=0.5, strength=1.0, direction=[0, 0, 0.5])
        self.assertAlmostEqual(zmax(obj("S")), before + 0.5, delta=0.05)

    def test_flatten_lowers_the_dome(self):
        before = zmax(obj("S"))
        ok("sculpt", object="S", brush="flatten", points=[[0, 0, 1]], radius=0.6, strength=1.0, iterations=3)
        self.assertLess(zmax(obj("S")), before)

    def test_symmetry_mirrors_the_stroke(self):
        me = obj("S").data
        left = min(range(len(me.vertices)), key=lambda i: (me.vertices[i].co - Vector((-0.7, 0, 0.7))).length)
        start = me.vertices[left].co.copy()
        ok("sculpt", object="S", brush="draw", points=[[0.7, 0, 0.7]], radius=0.3, strength=1.0, symmetry=["x"])
        self.assertGreater((me.vertices[left].co - start).length, 1e-3)

    def test_remesh_changes_topology(self):
        res = ok("sculpt", object="S", remesh=0.1)
        self.assertTrue(res["notes"])
        self.assertNotEqual(res["mesh"]["verts"], 0)

    def test_grab_needs_direction(self):
        self.assertIn("direction", err("sculpt", object="S", brush="grab", points=[[0, 0, 1]])["error"]["message"])

    def test_stroke_brush_needs_points(self):
        self.assertIn("points", err("sculpt", object="S", brush="pinch")["error"]["message"])

    def test_coarse_mesh_note(self):
        ok("add_primitive", type="cube", name="Coarse")
        res = ok("sculpt", object="Coarse", brush="draw", points=[[0, 0, 1]], radius=0.5)
        self.assertTrue(any("coarse" in n for n in res.get("notes", [])))


class PaintTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")
        ok("add_primitive", type="uv_sphere", name="P", segments=32)

    def test_vertex_fill_and_material_link(self):
        res = ok("paint", object="P", target="vertex", color=[1, 0, 0])
        self.assertTrue(res["created_layer"])
        ca = obj("P").data.color_attributes["Color"]
        cols = np.empty(len(ca.data) * 4)
        ca.data.foreach_get("color", cols)
        self.assertTrue(np.allclose(cols.reshape(-1, 4)[:, :3], [1, 0, 0]))
        mat = obj("P").data.materials[0]
        link = mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].links[0]
        self.assertEqual(link.from_node.type, "VERTEX_COLOR")

    def test_vertex_stroke_is_local(self):
        res = ok("paint", object="P", target="vertex", color=[0, 0, 1], points=[[0, 0, 1]], radius=0.3)
        self.assertGreater(res["painted"], 0)
        self.assertLess(res["painted"], len(obj("P").data.vertices))

    def test_texture_fill_top_faces(self):
        res = ok("paint", object="P", target="texture", color=[0, 1, 0], resolution=128,
                 select={"facing": [0, 0, 1], "angle_deg": 40})
        self.assertGreater(res["painted_texels"], 0)
        img = bpy.data.images[res["image"]]
        self.assertIsNotNone(img.packed_file)
        px = np.array(img.pixels[:]).reshape(-1, 4)
        self.assertTrue((px[:, 1] > 0.99).any() and (px[:, 1] < 0.9).any())

    def test_texture_stroke_saves_png(self):
        path = os.path.join(WORKDIR, "paint", "sphere.png")
        res = ok("paint", object="P", target="texture", color=[1, 1, 0], points=[[0, -1, 0]], radius=0.5,
                 resolution=128, save_path=path)
        self.assertTrue(os.path.isfile(path))
        self.assertGreater(res["painted_texels"], 0)

    def test_blend_multiply_darkens(self):
        ok("paint", object="P", target="vertex", color=[1, 1, 1])
        ok("paint", object="P", target="vertex", color=[0.5, 0.5, 0.5], blend="multiply")
        ca = obj("P").data.color_attributes["Color"]
        self.assertAlmostEqual(ca.data[0].color[0], 0.5, places=4)

    def test_material_edit_keeps_painted_colours(self):
        ok("paint", object="P", target="vertex", color=[0, 1, 0])
        painted = obj("P").data.materials[0]
        res = ok("material_set", object="P", roughness=0.3)
        self.assertFalse(res["created"], "material_set without a name must edit the current material")
        self.assertIs(obj("P").data.materials[0], painted)
        link = painted.node_tree.nodes["Principled BSDF"].inputs["Base Color"].links[0]
        self.assertEqual(link.from_node.type, "VERTEX_COLOR")

    def test_unknown_target(self):
        self.assertIn("vertex", err("paint", object="P", target="walls")["error"]["hint"])


class LightingTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")

    def test_world_color_and_strength(self):
        res = ok("world_set", color=[0.2, 0.3, 0.4], strength=2.0)
        self.assertEqual(res["color"][:3], [0.2, 0.3, 0.4])
        self.assertEqual(res["strength"], 2.0)
        self.assertIn("studio", res["bundled_hdris"])

    def test_bundled_hdri_with_rotation(self):
        res = ok("world_set", hdri="studio", rotation_deg=90)
        self.assertIn("studio", res["hdri"])
        self.assertAlmostEqual(res["rotation_deg"], 90, places=1)

    def test_unknown_hdri_lists_bundled(self):
        self.assertIn("sunset", err("world_set", hdri="mars")["error"]["hint"])

    def test_rotation_without_hdri(self):
        self.assertIn("hdri", err("world_set", rotation_deg=45)["error"]["hint"])

    def test_color_replaces_hdri(self):
        ok("world_set", hdri="forest")
        res = ok("world_set", color=[1, 1, 1])
        self.assertNotIn("hdri", res)

    def test_create_area_light_aimed(self):
        res = ok("light_set", name="Key", type="AREA", energy=800, size=2, location=[3, -3, 4], look_at=[0, 0, 0])
        self.assertTrue(res["created"])
        li = obj("Key")
        self.assertEqual(li.data.type, "AREA")
        forward = li.matrix_world.to_quaternion() @ Vector((0, 0, -1))
        self.assertAlmostEqual((forward - (-li.location).normalized()).length, 0, places=3)

    def test_update_existing_light(self):
        ok("light_set", name="Fill", energy=100)
        res = ok("light_set", name="Fill", energy=250, color=[1, 0.5, 0.2], type="SPOT", spot_angle_deg=30)
        self.assertFalse(res["created"])
        self.assertEqual(obj("Fill").data.type, "SPOT")
        self.assertAlmostEqual(obj("Fill").data.energy, 250)
        self.assertAlmostEqual(math.degrees(obj("Fill").data.spot_size), 30, places=3)

    def test_light_name_taken_by_mesh(self):
        ok("add_primitive", type="cube", name="Box")
        self.assertIn("not a light", err("light_set", name="Box")["error"]["message"])


class BakeTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")

    def test_diffuse_bake_writes_png(self):
        ok("add_primitive", type="cube", name="B")
        ok("material_set", object="B", base_color=[0.1, 0.8, 0.2])
        path = os.path.join(WORKDIR, "bakes", "b.png")
        res = ok("bake", object="B", type="diffuse", resolution=64, samples=2, output_path=path, device="cpu")
        self.assertTrue(os.path.isfile(res["path"]))
        img = bpy.data.images.load(path)
        g = np.array(img.pixels[:]).reshape(-1, 4)[:, 1].mean()
        self.assertGreater(g, 0.3)

    def test_normal_bake_high_to_low_and_assign(self):
        ok("add_primitive", type="uv_sphere", name="High", segments=64)
        ok("sculpt", object="High", brush="noise", strength=1.0)
        ok("add_primitive", type="uv_sphere", name="Low", segments=16)
        res = ok("bake", object="Low", type="normal", source=["High"], resolution=64, samples=1, assign=True, device="cpu")
        self.assertEqual(res["from"], ["High"])
        bsdf = obj("Low").data.materials[0].node_tree.nodes["Principled BSDF"]
        self.assertTrue(bsdf.inputs["Normal"].is_linked)


class AnimateTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")
        ok("add_primitive", type="cube", name="A")

    def test_location_linear(self):
        ok("animate", object="A", property="location", interpolation="linear",
           keys=[{"frame": 1, "value": [0, 0, 0]}, {"frame": 11, "value": [10, 0, 0]}])
        bpy.context.scene.frame_set(6)
        self.assertAlmostEqual(obj("A").location.x, 5.0, places=3)

    def test_rotation_degrees(self):
        ok("animate", object="A", property="rotation_deg",
           keys=[{"frame": 1, "value": [0, 0, 0]}, {"frame": 10, "value": [0, 0, 90]}])
        bpy.context.scene.frame_set(10)
        self.assertAlmostEqual(math.degrees(obj("A").rotation_euler.z), 90, places=3)

    def test_data_path_on_light(self):
        ok("light_set", name="L", energy=10)
        res = ok("animate", object="L", property="data.energy", keys=[{"frame": 1, "value": 10}, {"frame": 20, "value": 500}])
        self.assertEqual(res["data_path"], "energy")
        bpy.context.scene.frame_set(20)
        self.assertAlmostEqual(obj("L").data.energy, 500)

    def test_modifier_property(self):
        ok("modifier_add", name="A", type="bevel", params={"width": 0.1})
        res = ok("animate", object="A", property='modifiers["Bevel"].width',
                 keys=[{"frame": 1, "value": 0.0}, {"frame": 10, "value": 0.3}])
        self.assertEqual(res["data_path"], 'modifiers["Bevel"].width')
        self.assertEqual(res["fcurves"], 1)

    def test_frame_range_widened(self):
        res = ok("animate", object="A", property="scale", keys=[{"frame": 1, "value": [1, 1, 1]}, {"frame": 400, "value": [2, 2, 2]}])
        self.assertEqual(res["frame_range"][1], 400)
        self.assertTrue(res.get("notes"))

    def test_clear_replaces_keys(self):
        ok("animate", object="A", property="location", keys=[{"frame": 1, "value": [0, 0, 0]}, {"frame": 5, "value": [1, 0, 0]}])
        ok("animate", object="A", property="location", clear=True, keys=[{"frame": 30, "value": [3, 0, 0]}])
        from gb.animate import fcurves_of
        frames = sorted({kp.co.x for fc in fcurves_of(obj("A")) for kp in fc.keyframe_points})
        self.assertEqual(frames, [30.0])

    def test_bad_property(self):
        self.assertIn("animatable", err("animate", object="A", property="nonsense", keys=[{"frame": 1, "value": 1}])["error"]["message"])


class HeadlessSafetyTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")
        ok("add_primitive", type="cube", name="C")

    def test_loopcut_operator_is_blocked_not_crashing(self):
        resp = err("run_python", code="bpy.ops.mesh.loopcut_slide(MESH_OT_loopcut={'number_cuts': 1})")
        self.assertEqual(resp["error"]["type"], "HeadlessUnsupported")
        self.assertIn("loop_cut", resp["error"]["hint"])
        self.assertTrue(ok("ping")["pong"])

    def test_mesh_filter_is_blocked(self):
        resp = err("run_python", code="bpy.ops.sculpt.mesh_filter(type='INFLATE')")
        self.assertEqual(resp["error"]["type"], "HeadlessUnsupported")

    def test_viewport_operator_gets_a_hint(self):
        resp = err("run_python", code="bpy.context.view_layer.objects.active = bpy.data.objects['C']\n"
                                      "bpy.ops.object.mode_set(mode='SCULPT')\n"
                                      "bpy.ops.sculpt.brush_stroke(stroke=[])")
        self.assertIn("sculpt tool", resp["error"].get("hint", ""))


if __name__ == "__main__":
    unittest.main()
