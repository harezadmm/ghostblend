import os
import threading
import time
import unittest

import bpy

from gb import history, main as gbmain, runtime
from gbtest import WORKDIR, call, err, names, ok, py


class CoreTests(unittest.TestCase):
    def setUp(self):
        ok("scene_new")

    def test_unknown_command_lists_known_ones(self):
        resp = err("does_not_exist")
        self.assertEqual(resp["error"]["type"], "UnknownCommand")
        self.assertIn("scene_info", resp["error"]["hint"])

    def test_scene_new_is_empty(self):
        info = ok("scene_info")
        self.assertEqual(info["objects_total"], 0)
        self.assertIsNotNone(info["world"])

    def test_scene_new_keep_defaults(self):
        ok("scene_new", keep_defaults=True)
        self.assertIn("Cube", names())
        self.assertEqual(ok("scene_info")["camera"], "Camera")

    def test_scene_info_lists_rounded_transforms(self):
        py("bpy.ops.mesh.primitive_cube_add(location=(1.234567, 0, 0))")
        info = ok("scene_info")
        cube = info["objects"][0]
        self.assertEqual(cube["name"], "Cube")
        self.assertEqual(cube["location"], [1.2346, 0.0, 0.0])
        self.assertEqual(cube["mesh"], {"verts": 8, "faces": 6})

    def test_object_info_missing_name_suggests(self):
        py("bpy.ops.mesh.primitive_cube_add()")
        resp = err("object_info", name="Cub")
        self.assertEqual(resp["error"]["type"], "NotFound")
        self.assertIn("Cube", resp["error"]["hint"])

    def test_autosave_ring_keeps_ten(self):
        for i in range(12):
            py(f"bpy.ops.mesh.primitive_cube_add(location=({i}, 0, 0))")
        seqs = history.existing_seqs()
        self.assertEqual(len(seqs), 10)
        self.assertEqual(seqs[-1], history.S["seq"])
        self.assertTrue(os.path.isfile(os.path.join(history.S["dir"], "autosave", "index.jsonl")))

    def test_mutating_call_reports_autosave(self):
        resp = call("run_python", code="bpy.ops.mesh.primitive_cube_add()")
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["autosave"]["seq"], history.S["seq"])
        self.assertTrue(os.path.isfile(resp["autosave"]["path"]))

    def test_restore_steps_undoes_last_change(self):
        py("bpy.ops.mesh.primitive_cube_add(); bpy.context.object.name = 'A'")
        py("bpy.ops.mesh.primitive_cube_add(); bpy.context.object.name = 'B'")
        self.assertEqual(names(), ["A", "B"])
        ok("checkpoint_restore", steps=1)
        self.assertEqual(names(), ["A"])

    def test_failed_call_rolls_back(self):
        py("bpy.ops.mesh.primitive_cube_add(); bpy.context.object.name = 'Keep'")
        resp = call("run_python", code="bpy.ops.mesh.primitive_uv_sphere_add()\nraise ValueError('boom')\n")
        self.assertFalse(resp["ok"])
        self.assertTrue(resp["rolled_back"])
        self.assertEqual(resp["error"]["type"], "ValueError")
        self.assertEqual(names(), ["Keep"])

    def test_non_atomic_failure_keeps_partial_state(self):
        resp = call("run_python", _atomic=False, code="bpy.ops.mesh.primitive_uv_sphere_add()\nraise ValueError('x')")
        self.assertFalse(resp["ok"])
        self.assertNotIn("rolled_back", resp)
        self.assertEqual(names(), ["Sphere"])

    def test_run_python_result_and_stdout(self):
        out = ok("run_python", code="print('hello')\nresult = {'x': 1, 'v': Vector((1, 2, 3)), 'obj': None}")
        self.assertEqual(out["result"], {"x": 1, "v": [1.0, 2.0, 3.0], "obj": None})
        self.assertEqual(out["stdout"], "hello\n")

    def test_run_python_id_results_become_names(self):
        py("bpy.ops.mesh.primitive_cube_add()")
        self.assertEqual(py("result = list(bpy.data.objects)"), ["Cube"])

    def test_run_python_traceback_points_at_user_line(self):
        resp = err("run_python", code="a = 1\nb = undefined_name\n")
        e = resp["error"]
        self.assertEqual(e["type"], "NameError")
        self.assertIn("line 2", e["traceback"])
        self.assertIn("b = undefined_name", e["traceback"])
        self.assertNotIn("pyexec.py", e["traceback"])

    def test_run_python_syntax_error(self):
        resp = err("run_python", code="def (:\n")
        self.assertEqual(resp["error"]["type"], "SyntaxError")
        self.assertIn("line 1", resp["error"]["message"])

    def test_run_python_fake_marker_stays_in_stdout(self):
        out = ok("run_python", code="print('@@bhm:{\"id\": 1, \"ok\": true}')")
        self.assertIn("@@bhm:", out["stdout"])

    def test_run_python_output_cap(self):
        out = ok("run_python", code="print('x' * 1000000)")
        self.assertLess(len(out["stdout"]), 25000)
        self.assertIn("truncated", out["stdout"])

    def test_run_python_huge_result_is_capped(self):
        out = ok("run_python", code="result = 'y' * 500000")
        self.assertIn("truncated_json", out["result"])

    def test_sys_exit_does_not_kill_worker(self):
        resp = err("run_python", code="import sys\nsys.exit(3)")
        self.assertEqual(resp["error"]["type"], "SystemExit")
        self.assertTrue(ok("ping")["pong"])

    def test_interrupt_stops_python_loop(self):
        rid = 424242
        timer = threading.Timer(0.5, lambda: gbmain._control({"control": "interrupt", "id": rid}))
        timer.start()
        t0 = time.time()
        resp = gbmain.process({"id": rid, "cmd": "run_python", "autosave": True, "atomic": True,
                               "args": {"code": "while True:\n    pass\n"}})
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["type"], "Interrupted")
        self.assertLess(time.time() - t0, 5)
        self.assertTrue(ok("ping")["pong"])

    def test_interrupt_ignored_when_nothing_runs(self):
        self.assertFalse(runtime.request_interrupt(12345))

    def test_save_open_roundtrip_in_unicode_folder(self):
        py("bpy.ops.mesh.primitive_cube_add(); bpy.context.object.name = 'Kursi'")
        saved = ok("scene_save", path="scenes/kursi ü.blend")
        self.assertTrue(saved["path"].startswith(WORKDIR))
        self.assertTrue(os.path.isfile(saved["path"]))
        self.assertFalse(ok("scene_info")["unsaved_changes"])
        ok("scene_new")
        self.assertEqual(names(), [])
        opened = ok("scene_open", path=saved["path"])
        self.assertEqual(opened["file"], saved["path"])
        self.assertEqual(names(), ["Kursi"])
        self.assertFalse(opened["unsaved_changes"])

    def test_save_without_path_on_new_scene_explains(self):
        resp = err("scene_save")
        self.assertIn("path", resp["error"]["hint"])

    def test_open_missing_file(self):
        resp = err("scene_open", path="nope/missing.blend")
        self.assertEqual(resp["error"]["type"], "NotFound")

    def test_checkpoints(self):
        py("bpy.ops.mesh.primitive_cube_add(); bpy.context.object.name = 'Base'")
        c = ok("checkpoint_save", label="before sphere")
        py("bpy.ops.mesh.primitive_uv_sphere_add()")
        listing = ok("checkpoint_list")
        self.assertIn(c["id"], [x["id"] for x in listing["checkpoints"]])
        self.assertEqual(listing["autosaves"][0]["cmd"], "run_python")
        ok("checkpoint_restore", checkpoint=c["id"])
        self.assertEqual(names(), ["Base"])

    def test_checkpoint_restore_needs_exactly_one_selector(self):
        resp = err("checkpoint_restore")
        self.assertIn("exactly one", resp["error"]["message"])


if __name__ == "__main__":
    unittest.main()
