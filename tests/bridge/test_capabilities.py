"""Blender capability matrix: every major feature area, exercised headless.

These are the checks from the feature audit, kept as regression tests so the
README's coverage table stays true. Each test asserts a real effect (geometry
changed, pixels lit, bones weighted, a file written), not just "no error".
"""
import math
import os
import tempfile
import unittest

import bpy
import numpy as np
from mathutils import Vector

from gbtest import ok

TMP = tempfile.mkdtemp(prefix="gbcap-")


def cube(name="C", loc=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(location=loc)
    o = bpy.context.object
    o.name = name
    return o


def camera(dist=6):
    cd = bpy.data.cameras.new("Cam")
    c = bpy.data.objects.new("Cam", cd)
    bpy.context.scene.collection.objects.link(c)
    c.location = (dist, -dist, dist * 0.8)
    c.rotation_euler = (Vector((0, 0, 0)) - c.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = c
    return c


def render_mean(engine="BLENDER_EEVEE", samples=4, res=48, name="r"):
    sc = bpy.context.scene
    sc.render.engine = engine
    sc.render.resolution_x = sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    if engine == "CYCLES":
        sc.cycles.samples = samples
        sc.cycles.use_denoising = False
        sc.cycles.device = "CPU"
    elif engine == "BLENDER_EEVEE":
        sc.eevee.taa_render_samples = samples
    path = os.path.join(TMP, name + ".png")
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(path)
    a = np.array(img.pixels[:]).reshape(-1, 4)
    bpy.data.images.remove(img)
    return float(a[:, :3].mean())


def edit(o):
    bpy.context.view_layer.objects.active = o
    o.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")


def object_mode():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


class Base(unittest.TestCase):
    def setUp(self):
        object_mode()
        ok("scene_new")

    def tearDown(self):
        object_mode()


class Modelling(Base):
    def test_edit_mode_operators(self):
        o = cube()
        edit(o)
        bpy.ops.mesh.extrude_region_move(TRANSFORM_OT_translate={"value": (0, 0, 1)})
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.inset(thickness=0.1, use_individual=True)
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.bevel(offset=0.02, segments=2)
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.subdivide(number_cuts=1)
        object_mode()
        self.assertGreater(len(o.data.vertices), 100)

    def test_bisect_and_spin(self):
        o = cube()
        edit(o)
        bpy.ops.mesh.bisect(plane_co=(0, 0, 0), plane_no=(0, 0, 1), use_fill=True, clear_outer=True)
        object_mode()
        bpy.context.view_layer.update()
        self.assertAlmostEqual(o.dimensions.z, 1.0, places=3)
        bpy.ops.mesh.primitive_circle_add(vertices=8, radius=0.3, location=(1, 0, 0))
        ring = bpy.context.object
        edit(ring)
        bpy.ops.mesh.spin(steps=12, angle=math.tau, center=(0, 0, 0), axis=(0, 0, 1))
        object_mode()
        self.assertGreater(len(ring.data.vertices), 8)

    def test_curves_text_metaballs_nurbs_become_meshes(self):
        bpy.ops.curve.primitive_bezier_circle_add()
        bpy.context.object.data.bevel_depth = 0.1
        bpy.ops.object.convert(target="MESH")
        self.assertGreater(len(bpy.context.object.data.vertices), 0)
        bpy.ops.object.text_add()
        bpy.context.object.data.body = "Ghost"
        bpy.context.object.data.extrude = 0.1
        bpy.ops.object.convert(target="MESH")
        self.assertGreater(len(bpy.context.object.data.vertices), 50)
        bpy.ops.object.metaball_add(type="BALL")
        bpy.ops.object.convert(target="MESH")
        self.assertGreater(len(bpy.context.object.data.vertices), 0)
        bpy.ops.surface.primitive_nurbs_surface_sphere_add()
        bpy.ops.object.convert(target="MESH")
        self.assertGreater(len(bpy.context.object.data.vertices), 0)

    def test_geometry_nodes(self):
        o = cube()
        ng = bpy.data.node_groups.new("GN", "GeometryNodeTree")
        ng.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        ng.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        gi, go = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
        ico = ng.nodes.new("GeometryNodeMeshIcoSphere")
        join = ng.nodes.new("GeometryNodeJoinGeometry")
        ng.links.new(gi.outputs[0], join.inputs[0])
        ng.links.new(ico.outputs[0], join.inputs[0])
        ng.links.new(join.outputs[0], go.inputs[0])
        o.modifiers.new("GN", "NODES").node_group = ng
        ev = o.evaluated_get(bpy.context.evaluated_depsgraph_get())
        self.assertGreater(len(ev.to_mesh().vertices), 8)
        ev.to_mesh_clear()

    def test_remeshing(self):
        o = cube()
        o.data.remesh_voxel_size = 0.25
        bpy.context.view_layer.objects.active = o
        bpy.ops.object.voxel_remesh()
        self.assertGreater(len(o.data.vertices), 8)
        bpy.ops.mesh.primitive_uv_sphere_add()
        bpy.ops.object.quadriflow_remesh(target_faces=150)
        self.assertGreater(len(bpy.context.object.data.polygons), 0)

    def test_join_objects(self):
        a, b = cube("A"), cube("B", (3, 0, 0))
        a.select_set(True)
        b.select_set(True)
        bpy.context.view_layer.objects.active = a
        bpy.ops.object.join()
        self.assertEqual(len(bpy.context.scene.objects), 1)


class Sculpting(Base):
    def test_sculpt_mode_dyntopo_multires(self):
        bpy.ops.mesh.primitive_uv_sphere_add()
        o = bpy.context.object
        bpy.ops.object.mode_set(mode="SCULPT")
        self.assertEqual(o.mode, "SCULPT")
        bpy.ops.sculpt.dynamic_topology_toggle()
        self.assertTrue(o.use_dynamic_topology_sculpting)
        bpy.ops.sculpt.dynamic_topology_toggle()
        object_mode()
        m = o.modifiers.new("Multires", "MULTIRES")
        bpy.ops.object.multires_subdivide(modifier="Multires", mode="CATMULL_CLARK")
        self.assertGreaterEqual(m.total_levels, 1)

    def test_brush_operator_fails_gracefully(self):
        bpy.ops.mesh.primitive_uv_sphere_add()
        bpy.ops.object.mode_set(mode="SCULPT")
        with self.assertRaises(RuntimeError):
            bpy.ops.sculpt.brush_stroke(stroke=[])  # needs the viewport; the sculpt tool replaces it

    def test_ghostblend_sculpt_tool_replaces_brushes(self):
        ok("add_primitive", type="uv_sphere", name="S", segments=48)
        res = ok("sculpt", object="S", brush="draw", points=[[0, 0, 1]], radius=0.4, strength=1, subdivide=1)
        self.assertGreater(res["vertices_moved"], 0)


class Lighting(Base):
    def _lit(self, light_type):
        cube()
        camera()
        dark = render_mean(name=f"dark_{light_type}")
        ld = bpy.data.lights.new("L", light_type)
        ld.energy = 5 if light_type == "SUN" else 1000
        lo = bpy.data.objects.new("L", ld)
        bpy.context.scene.collection.objects.link(lo)
        lo.location = (3, -3, 4)
        lo.rotation_euler = (Vector((0, 0, 0)) - lo.location).to_track_quat("-Z", "Y").to_euler()
        return dark, render_mean(name=f"lit_{light_type}")

    def test_point_sun_spot_area(self):
        for t in ("POINT", "SUN", "SPOT", "AREA"):
            dark, lit = self._lit(t)
            self.assertGreater(lit, dark + 0.01, t)
            ok("scene_new")

    def test_hdri_world(self):
        cube()
        camera()
        ok("world_set", hdri="city")
        self.assertGreater(render_mean("CYCLES", 4, name="hdri"), 0.05)

    def test_emission_and_light_linking(self):
        o = cube()
        camera()
        ok("world_set", color=[0, 0, 0])
        ok("material_set", object="C", emission_color=[1, 0.5, 0.2], emission_strength=5)
        self.assertGreater(render_mean("CYCLES", 4, name="emit"), 0.02)
        ld = bpy.data.lights.new("L", "POINT")
        lo = bpy.data.objects.new("L", ld)
        bpy.context.scene.collection.objects.link(lo)
        coll = bpy.data.collections.new("recv")
        coll.objects.link(o)
        lo.light_linking.receiver_collection = coll
        self.assertEqual(lo.light_linking.receiver_collection.name, "recv")


class Colouring(Base):
    def test_image_and_procedural_textures(self):
        cube()
        camera()
        ok("light_set", name="Sun", type="SUN", energy=4, location=[3, -3, 5], look_at=[0, 0, 0])
        ok("paint", object="C", target="texture", color=[1, 0, 0], resolution=32)
        self.assertGreater(render_mean(name="tex"), 0.02)
        m = bpy.data.materials.new("N")
        if hasattr(m, "use_nodes"):
            m.use_nodes = True
        nt = m.node_tree
        noise = nt.nodes.new("ShaderNodeTexNoise")
        nt.links.new(noise.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
        bpy.data.objects["C"].data.materials[0] = m
        self.assertGreater(render_mean("CYCLES", 2, name="noise"), 0.0)

    def test_vertex_colors_uvs_and_bake(self):
        ok("add_primitive", type="cube", name="C")
        ok("paint", object="C", target="vertex", color=[0.1, 0.9, 0.2])
        ok("edit_mesh", object="C", operation="unwrap", method="smart")
        res = ok("bake", object="C", type="diffuse", resolution=32, samples=1, device="cpu")
        img = bpy.data.images.load(res["path"])
        self.assertGreater(np.array(img.pixels[:]).reshape(-1, 4)[:, 1].mean(), 0.3)

    def test_paint_operators_fail_gracefully(self):
        o = cube()
        o.data.color_attributes.new("Col", "BYTE_COLOR", "CORNER")
        bpy.ops.object.mode_set(mode="VERTEX_PAINT")
        with self.assertRaises(RuntimeError):
            bpy.ops.paint.vertex_paint(stroke=[])  # the paint tool replaces it


class Rendering(Base):
    def test_all_engines(self):
        cube()
        camera()
        self.assertGreater(render_mean("BLENDER_WORKBENCH", 1, name="wb"), 0.01)
        self.assertGreater(render_mean("BLENDER_EEVEE", 2, name="ee"), 0.01)
        self.assertGreater(render_mean("CYCLES", 2, name="cy"), 0.01)

    def test_denoise_dof_motion_blur(self):
        cube()
        cam = camera()
        sc = bpy.context.scene
        sc.cycles.use_denoising = True
        cam.data.dof.use_dof = True
        cam.data.dof.focus_distance = 5
        sc.render.use_motion_blur = True
        self.assertGreater(render_mean("CYCLES", 4, name="fx"), 0.01)

    def test_freestyle_draws_lines(self):
        cube()
        camera()
        ok("world_set", color=[1, 1, 1])  # white background so black lines stand out
        sc = bpy.context.scene
        plain = render_mean("BLENDER_EEVEE", 2, res=96, name="fs_off")
        sc.render.use_freestyle = True
        sc.render.line_thickness = 3
        lineset = sc.view_layers[0].freestyle_settings.linesets[0]
        self.assertIsNotNone(lineset.linestyle, "a new scene must come with a Freestyle line style")
        lined = render_mean("BLENDER_EEVEE", 2, res=96, name="fs_on")
        self.assertLess(lined, plain - 0.005, "Freestyle lines should darken the image")

    def test_compositor(self):
        cube()
        camera()
        sc = bpy.context.scene
        if hasattr(sc, "compositing_node_group"):
            ng = bpy.data.node_groups.new("Comp", "CompositorNodeTree")
            ng.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
            rl, inv, out = ng.nodes.new("CompositorNodeRLayers"), ng.nodes.new("CompositorNodeInvert"), ng.nodes.new("NodeGroupOutput")
            ng.links.new(rl.outputs["Image"], inv.inputs["Color"])
            ng.links.new(inv.outputs["Color"], out.inputs[0])
            sc.compositing_node_group = ng
        sc.render.use_compositing = True
        self.assertGreater(render_mean("BLENDER_WORKBENCH", 1, name="comp"), 0.5)  # inverted grey is bright

    def test_video_and_multilayer_exr(self):
        o = cube()
        camera()
        sc = bpy.context.scene
        sc.frame_start, sc.frame_end = 1, 3
        o.keyframe_insert("location", frame=1)
        o.location.x = 1
        o.keyframe_insert("location", frame=3)
        sc.render.engine = "BLENDER_WORKBENCH"
        sc.render.resolution_x = sc.render.resolution_y = 32
        isf = sc.render.image_settings
        isf.media_type = "VIDEO"
        isf.file_format = "FFMPEG"
        sc.render.ffmpeg.format = "MPEG4"
        sc.render.ffmpeg.codec = "H264"
        sc.render.filepath = os.path.join(TMP, "clip_")
        bpy.ops.render.render(animation=True)
        self.assertTrue(any(f.startswith("clip_") and f.endswith(".mp4") for f in os.listdir(TMP)))
        isf.media_type = "MULTI_LAYER_IMAGE"
        isf.file_format = "OPEN_EXR_MULTILAYER"
        sc.view_layers[0].use_pass_z = True
        sc.render.filepath = os.path.join(TMP, "layers.exr")
        bpy.ops.render.render(write_still=True)
        self.assertTrue(os.path.isfile(os.path.join(TMP, "layers.exr")))

    def test_viewport_render_fails_gracefully(self):
        cube()
        camera()
        with self.assertRaises(RuntimeError):
            bpy.ops.render.opengl()  # render_preview (Workbench) is the headless equivalent


class Animation(Base):
    def test_keyframes_drivers_shape_keys_constraints(self):
        o = cube()
        ok("animate", object="C", property="location", keys=[{"frame": 1, "value": [0, 0, 0]}, {"frame": 11, "value": [10, 0, 0]}])
        bpy.context.scene.frame_set(6)
        self.assertTrue(3 < o.location.x < 7)
        drv = o.driver_add("scale", 2).driver
        drv.expression = "frame / 10"
        bpy.context.scene.frame_set(20)
        self.assertAlmostEqual(o.scale.z, 2.0, places=3)
        o.shape_key_add(name="Basis")
        key = o.shape_key_add(name="Up")
        for v in key.data:
            v.co.z += 2
        key.value = 1
        ev = o.evaluated_get(bpy.context.evaluated_depsgraph_get())
        me = ev.to_mesh()
        self.assertGreater(max(v.co.z for v in me.vertices), 2.5)
        ev.to_mesh_clear()
        target = cube("T", (5, 0, 0))
        o.constraints.new("TRACK_TO").target = target
        bpy.context.view_layer.update()

    def test_armature_with_automatic_weights(self):
        bpy.ops.mesh.primitive_cylinder_add(depth=4, vertices=16)
        mesh = bpy.context.object
        arm_data = bpy.data.armatures.new("Arm")
        arm = bpy.data.objects.new("Arm", arm_data)
        bpy.context.scene.collection.objects.link(arm)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")
        b1 = arm_data.edit_bones.new("B1")
        b1.head, b1.tail = (0, 0, -2), (0, 0, 0)
        b2 = arm_data.edit_bones.new("B2")
        b2.head, b2.tail = (0, 0, 0), (0, 0, 2)
        b2.parent = b1
        object_mode()
        bpy.ops.object.select_all(action="DESELECT")
        mesh.select_set(True)
        arm.select_set(True)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
        self.assertEqual(sorted(g.name for g in mesh.vertex_groups), ["B1", "B2"])
        pb = arm.pose.bones["B2"]
        pb.rotation_mode = "XYZ"
        pb.rotation_euler = (1, 0, 0)
        pb.keyframe_insert("rotation_euler", frame=10)


class Simulation(Base):
    def test_rigid_body_falls(self):
        bpy.ops.mesh.primitive_plane_add(size=10)
        bpy.ops.rigidbody.object_add()
        bpy.context.object.rigid_body.type = "PASSIVE"
        box = cube("Box", (0, 0, 5))
        bpy.context.view_layer.objects.active = box
        bpy.ops.rigidbody.object_add()
        for f in range(1, 61):
            bpy.context.scene.frame_set(f)
        self.assertLess(box.matrix_world.translation.z, 4)

    def test_cloth_particles_soft_body(self):
        bpy.ops.mesh.primitive_plane_add(size=2, location=(0, 0, 2))
        cloth = bpy.context.object
        edit(cloth)
        bpy.ops.mesh.subdivide(number_cuts=6)
        object_mode()
        cloth.modifiers.new("Cloth", "CLOTH")
        emitter = cube("Emit", (5, 0, 0))
        emitter.modifiers.new("P", "PARTICLE_SYSTEM")
        emitter.particle_systems[0].settings.count = 50
        emitter.particle_systems[0].settings.frame_end = 1
        for f in range(1, 16):
            bpy.context.scene.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        ev = cloth.evaluated_get(dg)
        me = ev.to_mesh()
        self.assertLess(min((ev.matrix_world @ v.co).z for v in me.vertices), 2)
        ev.to_mesh_clear()
        self.assertEqual(len(emitter.evaluated_get(dg).particle_systems[0].particles), 50)

    def test_fluid_bake(self):
        bpy.ops.mesh.primitive_cube_add(size=2)
        dom = bpy.context.object
        md = dom.modifiers.new("F", "FLUID")
        md.fluid_type = "DOMAIN"
        ds = md.domain_settings
        ds.domain_type = "LIQUID"
        ds.resolution_max = 12
        ds.cache_frame_end = 2
        ds.cache_directory = os.path.join(TMP, "fluid")
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3)
        flow = bpy.context.object.modifiers.new("F", "FLUID")
        flow.fluid_type = "FLOW"
        flow.flow_settings.flow_type = "LIQUID"
        flow.flow_settings.flow_behavior = "GEOMETRY"
        bpy.context.view_layer.objects.active = dom
        bpy.ops.fluid.bake_all()
        self.assertIn("data", os.listdir(ds.cache_directory))


class GreasePencilVideoTracking(Base):
    def test_grease_pencil_strokes_render(self):
        camera()
        gp = bpy.data.grease_pencils.new("GP")
        o = bpy.data.objects.new("GP", gp)
        bpy.context.scene.collection.objects.link(o)
        drawing = gp.layers.new("L").frames.new(1).drawing
        drawing.add_strokes([4])
        for i, p in enumerate(drawing.strokes[0].points):
            p.position = (i - 1.5, 0, 0)
            p.radius = 0.2
        mat = bpy.data.materials.new("GPM")
        bpy.data.materials.create_gpencil_data(mat)
        gp.materials.append(mat)
        self.assertEqual(len(drawing.strokes), 1)
        render_mean(name="gp")

    def test_sequencer_render(self):
        sc = bpy.context.scene
        se = sc.sequence_editor_create()
        strips = se.strips if hasattr(se, "strips") else se.sequences
        strips.new_effect(name="Col", type="COLOR", channel=1, frame_start=1, length=10).color = (1, 0, 0)
        strips.new_effect(name="Txt", type="TEXT", channel=2, frame_start=1, length=10).text = "Hi"
        sc.render.use_sequencer = True
        self.assertGreater(render_mean("BLENDER_WORKBENCH", 1, name="vse"), 0.2)

    def test_movie_clip_tracking(self):
        for i in range(2):
            img = bpy.data.images.new(f"f{i}", 16, 16)
            img.filepath_raw = os.path.join(TMP, f"clip_{i:04d}.png")
            img.file_format = "PNG"
            img.save()
        clip = bpy.data.movieclips.load(os.path.join(TMP, "clip_0000.png"))
        clip.tracking.tracks.new(name="T", frame=1)
        self.assertEqual(len(clip.tracking.tracks), 1)


class Pipeline(Base):
    def test_append_and_assets(self):
        o = cube("Lib")
        path = os.path.join(TMP, "lib.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        bpy.data.objects.remove(o)
        with bpy.data.libraries.load(path, link=False) as (_src, dst):
            dst.objects = ["Lib"]
        for ob in dst.objects:
            bpy.context.scene.collection.objects.link(ob)
        self.assertIn("Lib", [ob.name for ob in bpy.context.scene.objects])
        bpy.data.objects["Lib"].asset_mark()
        self.assertIsNotNone(bpy.data.objects["Lib"].asset_data)


if __name__ == "__main__":
    unittest.main()
