import math
import os
import unittest

import bpy
from mathutils import Vector

from gbtest import WORKDIR, call, err, names, ok, py


class PrimitiveTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")

    def test_each_primitive_type(self):
        cases = {
            "cube": "MESH", "uv_sphere": "MESH", "ico_sphere": "MESH", "cylinder": "MESH",
            "cone": "MESH", "torus": "MESH", "plane": "MESH", "circle": "MESH", "monkey": "MESH",
            "grid": "MESH", "empty": "EMPTY", "camera": "CAMERA", "light": "LIGHT",
        }
        for i, (kind, exp_type) in enumerate(cases.items()):
            res = ok("add_primitive", type=kind, name=f"P_{kind}", location=[i * 3, 0, 0])
            self.assertEqual(res["name"], f"P_{kind}", kind)
            self.assertEqual(res["type"], exp_type, kind)

    def test_primitive_params(self):
        res = ok("add_primitive", type="cylinder", name="Cyl", radius=2.0, depth=5.0, segments=12)
        obj = bpy.data.objects["Cyl"]
        # A radius-2 cylinder spans 4 units in X/Y; depth 5 in Z.
        self.assertAlmostEqual(obj.dimensions.x, 4.0, places=3)
        self.assertAlmostEqual(obj.dimensions.z, 5.0, places=3)

    def test_light_energy_and_type(self):
        ok("add_primitive", type="light", name="Sun", light_type="SUN", energy=3.5)
        self.assertEqual(bpy.data.objects["Sun"].data.type, "SUN")
        self.assertAlmostEqual(bpy.data.objects["Sun"].data.energy, 3.5, places=4)

    def test_camera_becomes_active_when_first(self):
        ok("add_primitive", type="camera", name="Cam")
        self.assertEqual(bpy.context.scene.camera.name, "Cam")

    def test_look_at_points_negative_z(self):
        from gb.objects import look_at

        ok("add_primitive", type="camera", name="Cam", location=[0, -5, 0])
        cam = bpy.data.objects["Cam"]
        look_at(cam, Vector((0, 0, 0)))
        bpy.context.view_layer.update()
        forward = cam.matrix_world.to_quaternion() @ Vector((0, 0, -1))
        aim = (Vector((0, 0, 0)) - cam.location).normalized()
        self.assertAlmostEqual((forward - aim).length, 0.0, places=4)


class TransformTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")
        ok("add_primitive", type="cube", name="C")

    def test_set_and_delta(self):
        ok("transform", name="C", location=[1, 2, 3])
        self.assertEqual(list(bpy.data.objects["C"].location), [1, 2, 3])
        ok("transform", name="C", location=[1, 0, 0], mode="delta")
        self.assertEqual(list(bpy.data.objects["C"].location), [2, 2, 3])

    def test_rotation_degrees(self):
        ok("transform", name="C", rotation_deg=[90, 0, 0])
        self.assertAlmostEqual(bpy.data.objects["C"].rotation_euler.x, math.radians(90), places=5)

    def test_apply_bakes_scale(self):
        ok("transform", name="C", scale=[2, 2, 2], apply=True)
        obj = bpy.data.objects["C"]
        self.assertAlmostEqual(obj.scale.x, 1.0, places=5)
        self.assertAlmostEqual(obj.dimensions.x, 4.0, places=4)


class DeleteDuplicateTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")
        ok("add_primitive", type="cube", name="A")

    def test_delete_reports_missing(self):
        res = ok("object_delete", names=["A", "Ghost"])
        self.assertEqual(res["deleted"], ["A"])
        self.assertEqual(res["missing"], ["Ghost"])
        self.assertNotIn("A", names())

    def test_duplicate_linked_shares_mesh(self):
        res = ok("object_duplicate", name="A", new_name="A_linked", linked=True)
        self.assertEqual(res["name"], "A_linked")
        self.assertIs(bpy.data.objects["A"].data, bpy.data.objects["A_linked"].data)

    def test_duplicate_unlinked_independent_mesh(self):
        ok("object_duplicate", name="A", new_name="A_copy", linked=False)
        self.assertIsNot(bpy.data.objects["A"].data, bpy.data.objects["A_copy"].data)


class ModifierTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")
        ok("add_primitive", type="cube", name="C")

    def test_bevel_with_width(self):
        res = ok("modifier_add", name="C", type="bevel", params={"width": 0.1, "segments": 3})
        self.assertIn("Bevel", [m["type"].title() if m["type"] == "BEVEL" else m["type"] for m in res["modifiers"]] or [])
        mod = bpy.data.objects["C"].modifiers[0]
        self.assertEqual(mod.type, "BEVEL")
        self.assertAlmostEqual(mod.width, 0.1, places=5)

    def test_boolean_by_object_name(self):
        ok("add_primitive", type="uv_sphere", name="Cutter", location=[0.5, 0, 0])
        ok("modifier_add", name="C", type="boolean", params={"object": "Cutter", "operation": "DIFFERENCE"})
        mod = bpy.data.objects["C"].modifiers[0]
        self.assertEqual(mod.type, "BOOLEAN")
        self.assertEqual(mod.object.name, "Cutter")

    def test_unknown_param_lists_valid(self):
        resp = err("modifier_add", name="C", type="bevel", params={"widht": 0.1})
        self.assertIn("width", resp["error"].get("hint", ""))

    def test_apply_changes_geometry(self):
        before = len(bpy.data.objects["C"].data.vertices)
        ok("modifier_add", name="C", type="subdivision", params={"levels": 2}, apply=True)
        after = len(bpy.data.objects["C"].data.vertices)
        self.assertGreater(after, before)
        self.assertEqual(len(bpy.data.objects["C"].modifiers), 0)


class MaterialTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")
        ok("add_primitive", type="cube", name="C")

    def test_principled_inputs_and_viewport(self):
        res = ok("material_set", object="C", name="Red", base_color=[1, 0, 0, 1],
                 metallic=0.3, roughness=0.7)
        self.assertTrue(res["created"])
        mat = bpy.data.objects["C"].material_slots[0].material
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        self.assertAlmostEqual(bsdf.inputs["Metallic"].default_value, 0.3, places=4)
        self.assertAlmostEqual(list(mat.diffuse_color)[0], 1.0, places=4)

    def test_emission(self):
        ok("material_set", object="C", name="Glow", emission_color=[0, 1, 0], emission_strength=5.0)
        mat = bpy.data.objects["C"].material_slots[0].material
        bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        self.assertAlmostEqual(bsdf.inputs["Emission Strength"].default_value, 5.0, places=4)


class ImportExportTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")

    def _roundtrip(self, ext):
        ok("scene_new")
        # A non-cubic box so the bounding-box check is meaningful on every axis.
        ok("add_primitive", type="cube", name="RT", scale=[1, 2, 3])
        ok("transform", name="RT", scale=[1, 2, 3], apply=True)
        before = Vector(bpy.data.objects["RT"].dimensions)
        path = os.path.join(WORKDIR, f"roundtrip/model{ext}")
        ok("export_model", path=path, selection=["RT"])
        self.assertTrue(os.path.isfile(path), f"{ext} was not written")
        ok("scene_new")
        self.assertEqual(names(), [])
        res = ok("import_model", path=path)
        self.assertGreaterEqual(res["count"], 1, f"{ext} imported nothing")
        meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        self.assertTrue(meshes, f"{ext} produced no mesh")
        # Formats split vertices differently, so compare shape, not vertex count.
        # glTF applies a +Y-up axis conversion, so match dimensions as a sorted set.
        after = sorted(meshes[0].dimensions)
        for a, b in zip(after, sorted(before)):
            self.assertAlmostEqual(a, b, places=2, msg=f"{ext} changed the shape: {after} vs {sorted(before)}")

    def test_roundtrip_glb(self):
        self._roundtrip(".glb")

    def test_roundtrip_obj(self):
        self._roundtrip(".obj")

    def test_roundtrip_stl(self):
        self._roundtrip(".stl")

    def test_roundtrip_ply(self):
        self._roundtrip(".ply")

    def test_roundtrip_fbx(self):
        self._roundtrip(".fbx")

    def test_unknown_extension_explains(self):
        resp = err("export_model", path=os.path.join(WORKDIR, "x.xyz"))
        self.assertIn("format", resp["error"].get("hint", "") + resp["error"]["message"])

    def test_paths_unicode_spaces(self):
        ok("add_primitive", type="cube", name="U")
        folder = os.path.join(WORKDIR, "Folder Ünïcode ñ")
        path = os.path.join(folder, "meja çember.glb")
        ok("export_model", path=path, selection=["U"])
        self.assertTrue(os.path.isfile(path))
        ok("scene_new")
        res = ok("import_model", path=path)
        self.assertGreaterEqual(res["count"], 1)


if __name__ == "__main__":
    unittest.main()
