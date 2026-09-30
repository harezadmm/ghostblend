"""bake: bake lighting, colour, normals and more into an image texture (Cycles)."""
import os
import time

import bpy

from . import history, materials, registry, util

TYPES = {"diffuse": "DIFFUSE", "combined": "COMBINED", "ao": "AO", "normal": "NORMAL", "emit": "EMIT",
         "roughness": "ROUGHNESS", "shadow": "SHADOW", "glossy": "GLOSSY", "position": "POSITION"}


def _cycles_device(scene, device):
    if device == "cpu":
        scene.cycles.device = "CPU"
        return "CPU"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
            try:
                prefs.compute_device_type = backend
            except TypeError:
                continue
            prefs.get_devices()
            if any(d.type == backend for d in prefs.devices):
                for d in prefs.devices:
                    d.use = d.type == backend
                scene.cycles.device = "GPU"
                return backend
    except (KeyError, AttributeError):
        pass
    scene.cycles.device = "CPU"
    return "CPU"


def _ensure_uvs(obj, notes):
    if obj.data.uv_layers:
        return
    util.select_only([obj], active=obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    notes.append("The mesh had no UVs, so it was UV-unwrapped with Smart UV Project.")


def _assign(mat, node, kind):
    nt = mat.node_tree
    bsdf = materials._principled(mat)
    if bsdf is None:
        return
    if kind in ("diffuse", "combined"):
        nt.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
    elif kind == "roughness":
        node.image.colorspace_settings.name = "Non-Color"
        nt.links.new(node.outputs["Color"], bsdf.inputs["Roughness"])
    elif kind == "normal":
        node.image.colorspace_settings.name = "Non-Color"
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.location = (node.location.x + 200, node.location.y - 200)
        nt.links.new(node.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    elif kind == "emit":
        nt.links.new(node.outputs["Color"], materials._input(bsdf, "emission_color"))


@registry.command("bake")
def bake(args):
    t0 = time.perf_counter()
    obj = util.get_object(args["object"])
    if obj.type != "MESH":
        raise util.UserError(f"{obj.name!r} is a {obj.type}; baking needs a mesh")
    kind = args.get("type", "diffuse")
    if kind not in TYPES:
        raise util.UserError(f"Unknown bake type {kind!r}", hint="One of: " + ", ".join(TYPES))
    util.ensure_object_mode()
    notes = []
    _ensure_uvs(obj, notes)
    if not obj.data.materials:
        obj.data.materials.append(bpy.data.materials.new(f"{obj.name}_Baked"))
        notes.append("The object had no material, so one was created to hold the bake target.")
    res = int(args.get("resolution", 1024))
    img = bpy.data.images.new(f"{obj.name}_{kind}", res, res, alpha=False, float_buffer=kind in ("normal", "position"))

    added = []
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            continue
        materials._principled(mat, create=True)
        node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = img
        node.name = "gb_bake_target"
        mat.node_tree.nodes.active = node
        added.append((mat, node))

    sc = bpy.context.scene
    snap = (sc.render.engine, sc.cycles.samples, sc.cycles.device)
    sources = [util.get_object(n) for n in args.get("source") or []]
    try:
        sc.render.engine = "CYCLES"
        sc.cycles.samples = int(args.get("samples", 16))
        device = _cycles_device(sc, args.get("device", "gpu"))
        util.select_only(sources + [obj], active=obj)
        kwargs = {"type": TYPES[kind], "margin": int(args.get("margin", 8)),
                  "use_selected_to_active": bool(sources)}
        if sources:
            kwargs["cage_extrusion"] = float(args.get("cage_extrusion", 0.05))
        if kind in ("diffuse", "glossy") and args.get("color_only", True):
            kwargs["pass_filter"] = {"COLOR"}
        bpy.ops.object.bake(**kwargs)
    finally:
        sc.render.engine, sc.cycles.samples, sc.cycles.device = snap

    if args.get("output_path"):
        path = util.resolve_path(args["output_path"])
    else:
        path = os.path.join(history.S["dir"], "renders", f"bake_{obj.name}_{kind}.png")
    util.ensure_parent_dir(path)
    img.filepath_raw = path
    img.file_format = "PNG" if not path.lower().endswith(".exr") else "OPEN_EXR"
    img.save()

    for mat, node in added:
        if args.get("assign"):
            _assign(mat, node, kind)
        else:
            mat.node_tree.nodes.remove(node)
    result = {"object": obj.name, "type": kind, "path": path, "resolution": [res, res],
              "device": device, "assigned": bool(args.get("assign")), "ms": int((time.perf_counter() - t0) * 1000)}
    if sources:
        result["from"] = [s.name for s in sources]
    if notes:
        result["notes"] = notes
    return result
