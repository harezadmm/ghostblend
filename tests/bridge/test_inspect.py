import unittest

import bpy

from gbtest import err, ok, py


class ValidateTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")

    def test_clean_cube_is_ok(self):
        ok("add_primitive", type="cube", name="Clean")
        res = ok("validate", objects=["Clean"])
        self.assertTrue(res["ok"], res["reports"])

    def test_plane_reports_boundary_edges(self):
        ok("add_primitive", type="plane", name="Flat")
        res = ok("validate", objects=["Flat"], checks=["boundary_edges"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["reports"][0]["findings"]["boundary_edges"], 4)

    def test_duplicate_vertices_detected(self):
        ok("add_primitive", type="cube", name="Dbl")
        # Duplicate the whole mesh geometry in place -> coincident verts.
        py("import bmesh, bpy\n"
           "o = bpy.data.objects['Dbl']\n"
           "bm = bmesh.new(); bm.from_mesh(o.data)\n"
           "geom = list(bm.verts) + list(bm.edges) + list(bm.faces)\n"
           "bmesh.ops.duplicate(bm, geom=geom)\n"
           "bm.to_mesh(o.data); bm.free(); o.data.update()")
        res = ok("validate", objects=["Dbl"], checks=["duplicate_vertices"])
        self.assertGreaterEqual(res["reports"][0]["findings"].get("duplicate_vertices", 0), 8)

    def test_inconsistent_normals(self):
        ok("add_primitive", type="cube", name="Flip")
        py("import bmesh, bpy\n"
           "o = bpy.data.objects['Flip']\n"
           "bm = bmesh.new(); bm.from_mesh(o.data)\n"
           "bm.faces.ensure_lookup_table()\n"
           "bmesh.ops.reverse_faces(bm, faces=[bm.faces[0]])\n"
           "bm.to_mesh(o.data); bm.free(); o.data.update()")
        res = ok("validate", objects=["Flip"], checks=["inconsistent_normals"])
        self.assertGreaterEqual(res["reports"][0]["findings"].get("inconsistent_normals", 0), 1)

    def test_negative_and_unapplied_scale(self):
        ok("add_primitive", type="cube", name="Neg")
        ok("transform", name="Neg", scale=[-1, 2, 1])
        res = ok("validate", objects=["Neg"], checks=["negative_scale", "unapplied_scale"])
        f = res["reports"][0]["findings"]
        self.assertIn("negative_scale", f)
        self.assertIn("unapplied_scale", f)

    def test_missing_uvs(self):
        ok("add_primitive", type="cube", name="NoUV")
        py("me = bpy.data.objects['NoUV'].data\n"
           "while me.uv_layers:\n"
           "    me.uv_layers.remove(me.uv_layers[0])\n")
        res = ok("validate", objects=["NoUV"], checks=["missing_uvs"])
        self.assertTrue(res["reports"][0]["findings"].get("missing_uvs"))

    def test_unknown_check_errors(self):
        resp = err("validate", checks=["not_a_check"])
        self.assertIn("Available checks", resp["error"]["hint"])


class ApiTests(unittest.TestCase):
    def test_describe_operator(self):
        res = ok("api_describe", path="bpy.ops.mesh.primitive_cube_add")
        self.assertEqual(res["kind"], "operator")
        names = [p["name"] for p in res["parameters"]]
        self.assertIn("size", names)

    def test_describe_type(self):
        res = ok("api_describe", path="bpy.types.BevelModifier")
        self.assertEqual(res["kind"], "type")
        self.assertIn("width", [p["name"] for p in res["settable"]])

    def test_describe_bare_name(self):
        res = ok("api_describe", path="bpy.types.SubsurfModifier")
        self.assertIn("levels", [p["name"] for p in res["settable"]])

    def test_describe_unknown(self):
        resp = err("api_describe", path="bpy.types.NotARealType")
        self.assertEqual(resp["error"]["type"], "NotFound")

    def test_search_finds_bevel(self):
        res = ok("api_search", query="bevel")
        self.assertTrue(any("bevel" in o.lower() for o in res["operators"]))
        self.assertIn("bpy.types.BevelModifier", res["types"])

    def test_search_types_only(self):
        res = ok("api_search", query="decimate", kind="types")
        self.assertEqual(res["operators"], [])
        self.assertTrue(any("Decimate" in t for t in res["types"]))


if __name__ == "__main__":
    unittest.main()
