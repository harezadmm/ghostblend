"""add_primitive, transform, object_delete, object_duplicate."""
import math

import bpy
from mathutils import Euler, Vector

from . import registry, scene, util
from .util import rv

_PRIMITIVE_OPS = {
    "cube": ("mesh.primitive_cube_add", "Cube"),
    "uv_sphere": ("mesh.primitive_uv_sphere_add", "Sphere"),
    "ico_sphere": ("mesh.primitive_ico_sphere_add", "Icosphere"),
    "cylinder": ("mesh.primitive_cylinder_add", "Cylinder"),
    "cone": ("mesh.primitive_cone_add", "Cone"),
    "torus": ("mesh.primitive_torus_add", "Torus"),
    "plane": ("mesh.primitive_plane_add", "Plane"),
    "circle": ("mesh.primitive_circle_add", "Circle"),
    "monkey": ("mesh.primitive_monkey_add", "Suzanne"),
    "grid": ("mesh.primitive_grid_add", "Grid"),
}


def _euler_from_deg(values):
    return Euler([math.radians(v) for v in values], "XYZ")


def look_at(obj, target):
    """Point an object's local -Z axis at `target`, keeping +Y up."""
    direction = Vector(target) - obj.location
    if direction.length < 1e-9:
        return
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


@registry.command("add_primitive")
def add_primitive(args):
    util.ensure_object_mode()
    kind = args["type"]
    loc = Vector(args.get("location", (0, 0, 0)))
    rot = _euler_from_deg(args.get("rotation_deg", (0, 0, 0)))

    if kind in _PRIMITIVE_OPS:
        op_path, default_name = _PRIMITIVE_OPS[kind]
        kwargs = {"location": loc, "rotation": rot}
        if "size" in args and kind in ("cube", "plane", "grid", "monkey", "circle"):
            kwargs["size" if kind != "circle" else "radius"] = float(args["size"])
        if "radius" in args and kind in ("uv_sphere", "ico_sphere", "cylinder", "cone", "circle", "torus"):
            key = "major_radius" if kind == "torus" else "radius"
            kwargs[key] = float(args["radius"])
        if "depth" in args and kind in ("cylinder", "cone"):
            kwargs["depth"] = float(args["depth"])
        if "segments" in args:
            seg = int(args["segments"])
            if kind == "uv_sphere":
                kwargs["segments"] = seg
            elif kind in ("cylinder", "cone", "circle"):
                kwargs["vertices"] = seg  # these operators call it "vertices"
            elif kind == "ico_sphere":
                kwargs["subdivisions"] = max(1, min(seg, 8))
        op = _resolve_op(op_path)
        op(**kwargs)
        obj = bpy.context.active_object
    elif kind == "empty":
        obj = bpy.data.objects.new(args.get("name", "Empty"), None)
        obj.location = loc
        obj.rotation_euler = rot
        bpy.context.scene.collection.objects.link(obj)
    elif kind == "camera":
        cam = bpy.data.cameras.new(args.get("name", "Camera"))
        obj = bpy.data.objects.new(args.get("name", "Camera"), cam)
        obj.location = loc
        obj.rotation_euler = rot
        bpy.context.scene.collection.objects.link(obj)
        if bpy.context.scene.camera is None:
            bpy.context.scene.camera = obj
    elif kind == "light":
        light = bpy.data.lights.new(args.get("name", "Light"), args.get("light_type", "POINT"))
        if "energy" in args:
            light.energy = float(args["energy"])
        obj = bpy.data.objects.new(args.get("name", "Light"), light)
        obj.location = loc
        obj.rotation_euler = rot
        bpy.context.scene.collection.objects.link(obj)
    else:
        raise util.UserError(f"Unknown primitive type {kind!r}")

    if "scale" in args:
        obj.scale = Vector(args["scale"])
    if args.get("name"):
        obj.name = args["name"]
    bpy.context.view_layer.objects.active = obj
    return scene.obj_summary(obj)


def _resolve_op(path):
    op = bpy.ops
    for part in path.split("."):
        op = getattr(op, part)
    return op


@registry.command("transform")
def transform(args):
    obj = util.get_object(args["name"])
    mode = args.get("mode", "set")
    delta = mode == "delta"
    if "location" in args:
        v = Vector(args["location"])
        obj.location = obj.location + v if delta else v
    if "rotation_deg" in args:
        e = _euler_from_deg(args["rotation_deg"])
        if delta:
            obj.rotation_euler = Euler([a + b for a, b in zip(obj.rotation_euler, e)], "XYZ")
        else:
            obj.rotation_euler = e
    if "scale" in args:
        v = Vector(args["scale"])
        obj.scale = Vector([a * b for a, b in zip(obj.scale, v)]) if delta else v

    if args.get("apply"):
        util.ensure_object_mode()
        util.select_only([obj], active=obj)
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    bpy.context.view_layer.update()
    return scene.obj_summary(obj)


@registry.command("object_delete")
def object_delete(args):
    names = args["names"]
    if isinstance(names, str):
        names = [names]
    util.ensure_object_mode()
    deleted, missing = [], []
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is None:
            missing.append(name)
            continue
        deleted.append(name)
        bpy.data.objects.remove(obj, do_unlink=True)
    result = {"deleted": deleted, "count": len(deleted)}
    if missing:
        result["missing"] = missing
    return result


@registry.command("object_duplicate")
def object_duplicate(args):
    src = util.get_object(args["name"])
    linked = bool(args.get("linked", False))
    new = src.copy()
    if not linked and src.data is not None:
        new.data = src.data.copy()
    for coll in src.users_collection:
        coll.objects.link(new)
    if not src.users_collection:
        bpy.context.scene.collection.objects.link(new)
    if args.get("new_name"):
        new.name = args["new_name"]
    if "location" in args:
        new.location = Vector(args["location"])
    bpy.context.view_layer.objects.active = new
    bpy.context.view_layer.update()
    return scene.obj_summary(new)
