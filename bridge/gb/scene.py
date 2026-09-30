"""Scene-level commands and the object/scene summaries used across the bridge."""
import os

import bpy
from mathutils import Vector

from . import history, registry, util
from .util import deg3, r, rv


def reset_scene(empty=True):
    """Factory scene. `empty=True` removes the default cube, camera and light."""
    bpy.ops.wm.read_factory_settings(use_empty=empty)
    sc = bpy.context.scene
    if sc.world is None:
        world = bpy.data.worlds.new("World")
        world.color = (0.05, 0.05, 0.05)
        sc.world = world
    # An empty factory scene keeps its Freestyle line sets but drops their line
    # style, which makes Freestyle fail silently at render time. Give them one.
    for vl in sc.view_layers:
        for lineset in vl.freestyle_settings.linesets:
            if lineset.linestyle is None:
                lineset.linestyle = bpy.data.linestyles.get("LineStyle") or bpy.data.linestyles.new("LineStyle")


def rotation_deg(o):
    if o.rotation_mode == "QUATERNION":
        return deg3(o.rotation_quaternion.to_euler())
    if o.rotation_mode == "AXIS_ANGLE":
        return deg3(o.matrix_basis.to_euler())
    return deg3(o.rotation_euler)


def world_bbox(o):
    mw = o.matrix_world
    pts = [mw @ Vector(c) for c in o.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


def layer_state():
    """How the active view layer treats each collection, keyed by name_full.

    Each entry has `exclude` (its own outliner checkbox), `excluded_by` (the
    outermost excluded collection above or at it, or None) and `renders`. An
    excluded collection takes its objects, and all nested collections, out of both
    the viewport and renders; production files often switch assets on this way.
    """
    state = {}

    def walk(lc, path, excluder, renders):
        for ch in lc.children:
            coll = ch.collection
            here = path + [coll.name]
            exc = excluder or (coll.name if ch.exclude else None)
            ren = exc is None and renders and not coll.hide_render
            prev = state.get(coll.name_full)
            if prev is None or (prev["excluded_by"] and not exc):
                state[coll.name_full] = {"exclude": ch.exclude, "excluded_by": exc, "renders": ren, "path": here}
            elif ren:
                prev["renders"] = True
            walk(ch, here, exc, ren)

    root = bpy.context.view_layer.layer_collection
    state[root.collection.name_full] = {"exclude": False, "excluded_by": None, "renders": True, "path": []}
    walk(root, [], None, True)
    return state


def object_layer(o, state):
    """Whether `o` is in the view layer and renders, merged over all its collections."""
    entries = [state[c.name_full] for c in o.users_collection if c.name_full in state]
    included = any(e["excluded_by"] is None for e in entries)
    excluded_by = None if included else next((e["excluded_by"] for e in entries), None)
    return {"excluded_by": excluded_by,
            "renders": not o.hide_render and any(e["renders"] for e in entries)}


def excluded_note(state, objects):
    """One sentence on objects kept out by excluded collections, or None."""
    hidden = [o for o in objects if object_layer(o, state)["excluded_by"]]
    if not hidden:
        return None
    colls = [e for e in state.values() if e["exclude"]]
    names = ", ".join(repr(e["path"][-1]) for e in colls[:12])
    if len(colls) > 12:
        names += f" and {len(colls) - 12} more"
    first = "".join(f".children[{n!r}]" for n in colls[0]["path"])
    return (f"{len(hidden)} objects are in collections excluded from the view layer ({names}), "
            "so they do not render or show in previews. To include one, set exclude = False on its "
            f"layer collection, e.g. bpy.context.view_layer.layer_collection{first}.exclude = False "
            "(a nested collection also needs its excluded parents included).")


def obj_summary(o, state=None):
    d = {
        "name": o.name,
        "type": o.type,
        "location": rv(o.location),
        "rotation_deg": rotation_deg(o),
        "scale": rv(o.scale),
        "dimensions": rv(o.dimensions),
    }
    if o.parent:
        d["parent"] = o.parent.name
    layer = object_layer(o, state if state is not None else layer_state())
    hidden_viewport = o.hide_viewport or not o.visible_get()
    hidden_render = not layer["renders"]
    if hidden_viewport or hidden_render:
        d["hidden"] = {"viewport": hidden_viewport, "render": hidden_render}
        if layer["excluded_by"]:
            d["hidden"]["reason"] = f"collection {layer['excluded_by']!r} is excluded from the view layer"
        elif hidden_render and not o.hide_render:
            d["hidden"]["reason"] = "its collection is disabled in renders"
    if o.type == "MESH" and o.data is not None:
        d["mesh"] = {"verts": len(o.data.vertices), "faces": len(o.data.polygons)}
    mats = [s.material.name if s.material else None for s in o.material_slots]
    if mats:
        d["materials"] = mats
    if len(o.modifiers):
        d["modifiers"] = [{"name": m.name, "type": m.type} for m in o.modifiers]
    if o.type == "LIGHT":
        d["light"] = {"type": o.data.type, "energy": r(o.data.energy), "color": rv(o.data.color)}
    elif o.type == "CAMERA":
        d["camera"] = {"type": o.data.type, "lens": r(o.data.lens),
                       "active": bpy.context.scene.camera == o}
    return d


def _collection_tree(coll, state, depth=0):
    out = []
    for child in coll.children:
        node = {"name": child.name, "objects": len(child.objects)}
        entry = state.get(child.name_full)
        if entry is not None and entry["exclude"]:
            node["excluded"] = True
        if child.hide_render:
            node["hide_render"] = True
        if depth < 4 and len(child.children):
            node["children"] = _collection_tree(child, state, depth + 1)
        out.append(node)
    return out


def _world_summary(world):
    if world is None:
        return None
    d = {"name": world.name, "color": rv(world.color)}
    nt = getattr(world, "node_tree", None)
    if nt is not None and getattr(world, "use_nodes", True):
        for n in nt.nodes:
            if n.type == "BACKGROUND":
                d["background_color"] = rv(n.inputs[0].default_value)
                d["strength"] = r(n.inputs[1].default_value)
            elif n.type == "TEX_ENVIRONMENT" and n.image is not None:
                d["hdri"] = n.image.filepath or n.image.name
    return d


def missing_files():
    missing = []
    for img in bpy.data.images:
        if img.packed_file is not None or img.source not in ("FILE", "SEQUENCE", "TILED") or not img.filepath:
            continue
        path = bpy.path.abspath(img.filepath)
        if not os.path.exists(path):
            missing.append({"image": img.name, "path": path})
    for lib in bpy.data.libraries:
        path = bpy.path.abspath(lib.filepath)
        if not os.path.exists(path):
            missing.append({"library": lib.name, "path": path})
    return missing


def scene_summary(limit=200):
    sc = bpy.context.scene
    objs = list(sc.objects)
    state = layer_state()
    d = {
        "file": history.S["logical_path"],
        "unsaved_changes": history.unsaved_changes(),
        "scene": sc.name,
        "objects_total": len(objs),
        "objects": [obj_summary(o, state) for o in objs[:limit]],
    }
    if len(objs) > limit:
        d["objects_truncated"] = f"showing {limit} of {len(objs)}; pass a higher limit or use object_info"
    d["camera"] = sc.camera.name if sc.camera else None
    d["render"] = {
        "engine": sc.render.engine,
        "resolution": [sc.render.resolution_x, sc.render.resolution_y],
        "percentage": sc.render.resolution_percentage,
        "film_transparent": sc.render.film_transparent,
    }
    d["frame"] = {"current": sc.frame_current, "start": sc.frame_start, "end": sc.frame_end,
                  "fps": sc.render.fps}
    d["world"] = _world_summary(sc.world)
    us = sc.unit_settings
    d["units"] = {"system": us.system, "scale_length": r(us.scale_length), "length_unit": us.length_unit}
    tree = _collection_tree(sc.collection, state)
    if tree:
        d["collections"] = tree
    note = excluded_note(state, objs)
    if note:
        d["notes"] = [note]
    d["datablocks"] = {
        "meshes": len(bpy.data.meshes), "materials": len(bpy.data.materials),
        "images": len(bpy.data.images), "node_groups": len(bpy.data.node_groups),
        "actions": len(bpy.data.actions),
    }
    return d


def _modifier_detail(m):
    d = {"name": m.name, "type": m.type, "show_viewport": m.show_viewport, "show_render": m.show_render}
    skip = {"rna_type", "name", "type", "show_viewport", "show_render", "show_expanded", "show_in_editmode",
            "show_on_cage", "is_active", "is_override_data_editable", "use_pin_to_last", "persistent_uid",
            "execution_time", "use_apply_on_spline", "show_expanded_ui"}
    params = {}
    for p in m.bl_rna.properties:
        if p.identifier in skip or len(params) >= 40:
            continue
        try:
            v = getattr(m, p.identifier)
        except AttributeError:
            continue
        if p.type == "POINTER":
            if isinstance(v, bpy.types.ID):
                params[p.identifier] = v.name
            continue
        if p.type == "COLLECTION":
            continue
        if getattr(p, "array_length", 0):
            if p.array_length <= 4:
                params[p.identifier] = util.to_jsonable(list(v))
            continue
        params[p.identifier] = r(v) if isinstance(v, float) else util.to_jsonable(v)
    d["params"] = params
    return d


def object_detail(o):
    from . import materials

    d = obj_summary(o)
    mn, mx = world_bbox(o)
    d["world_bbox"] = {"min": rv(mn), "max": rv(mx), "center": rv((mn + mx) / 2), "size": rv(mx - mn)}
    d["rotation_mode"] = o.rotation_mode
    d["collections"] = [c.name for c in o.users_collection]
    if o.children:
        d["children"] = [c.name for c in o.children]
    if len(o.modifiers):
        d["modifiers"] = [_modifier_detail(m) for m in o.modifiers]
    if o.type == "MESH" and o.data is not None:
        me = o.data
        mesh = util.mesh_counts(me)
        mesh["data_name"] = me.name
        mesh["users"] = me.users
        mesh["uv_layers"] = [uv.name for uv in me.uv_layers]
        if o.vertex_groups:
            mesh["vertex_groups"] = [vg.name for vg in o.vertex_groups]
        if me.shape_keys:
            mesh["shape_keys"] = [k.name for k in me.shape_keys.key_blocks]
        if len(o.modifiers):
            mesh["evaluated"] = util.evaluated_mesh_counts(o)
        d["mesh"] = mesh
    if o.material_slots:
        d["materials"] = [materials.material_detail(s.material) if s.material else None for s in o.material_slots]
    if len(o.constraints):
        d["constraints"] = [{"name": c.name, "type": c.type} for c in o.constraints]
    custom = {k: util.to_jsonable(o[k]) for k in o.keys() if not k.startswith("_")}
    if custom:
        d["custom_properties"] = custom
    if o.type == "CAMERA":
        cam = o.data
        d["camera"] = {"type": cam.type, "lens": r(cam.lens), "sensor_width": r(cam.sensor_width),
                       "ortho_scale": r(cam.ortho_scale), "clip": [r(cam.clip_start), r(cam.clip_end)],
                       "active": bpy.context.scene.camera == o}
    elif o.type == "LIGHT":
        li = o.data
        d["light"] = {"type": li.type, "energy": r(li.energy), "color": rv(li.color)}
        if li.type == "AREA":
            d["light"]["size"] = r(li.size)
        elif li.type == "SPOT":
            d["light"]["spot_angle_deg"] = r(li.spot_size * 57.29577951308232, 2)
        elif li.type == "SUN":
            d["light"]["angle_deg"] = r(li.angle * 57.29577951308232, 2)
    if o.animation_data and o.animation_data.action:
        d["action"] = o.animation_data.action.name
    return d


# ---------------------------------------------------------------- commands


@registry.command("ping")
def ping(args):
    return {"pong": True, "seq": history.S["seq"], "blender": bpy.app.version_string}


@registry.command("scene_new")
def scene_new(args):
    reset_scene(empty=not args.get("keep_defaults", False))
    history.S["logical_path"] = None
    history.S["saved_seq"] = None
    return scene_summary()


@registry.command("scene_open")
def scene_open(args):
    path = util.resolve_path(args.get("path"), must_exist=True, what=".blend file")
    if not path.lower().endswith(".blend"):
        raise util.UserError(f"Not a .blend file: {path}",
                             hint="Use import_model for glTF, FBX, OBJ, STL, PLY, USD or Alembic files")
    history.open_blend(path)
    history.S["logical_path"] = path
    history.S["saved_seq"] = history.S["seq"] + 1  # the autosave that follows this command
    d = scene_summary()
    d["unsaved_changes"] = False  # the scene is exactly the file that was just opened
    missing = missing_files()
    if missing:
        d["missing_files"] = missing[:50]
    return d


@registry.command("scene_save")
def scene_save(args):
    if args.get("path"):
        path = util.resolve_path(args["path"])
    elif history.S["logical_path"]:
        path = history.S["logical_path"]
    else:
        raise util.UserError("This scene has never been saved, so there is no default path",
                             hint="Pass path, for example scenes/model.blend")
    if not path.lower().endswith(".blend"):
        path += ".blend"
    util.ensure_parent_dir(path)
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=bool(args.get("compress", False)),
                                relative_remap=True, check_existing=False)
    history.S["logical_path"] = path
    history.S["saved_seq"] = history.S["seq"]
    return {"path": path, "bytes": os.path.getsize(path)}


@registry.command("scene_info")
def scene_info(args):
    return scene_summary(limit=int(args.get("limit", 200)))


@registry.command("object_info")
def object_info(args):
    return object_detail(util.get_object(args.get("name")))
