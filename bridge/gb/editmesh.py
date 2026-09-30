"""edit_mesh: Blender's Edit Mode modelling operations, driven headless.

Most mesh operators work in background mode once the object is in Edit Mode
and the right faces are selected; the agent selects by description (see
selection.py). Operations that crash or need the interactive viewport in
background mode (loop cut, knife) are implemented with bmesh instead.
"""
import contextlib
import math

import bmesh
import bpy
from mathutils import Vector

from . import registry, util
from .selection import pick_faces, require_faces

OPERATIONS = ("extrude", "inset", "bevel", "subdivide", "loop_cut", "bisect", "spin", "merge", "delete",
              "dissolve", "flip_normals", "recalc_normals", "triangulate", "shade_smooth", "shade_flat",
              "unwrap", "mark_seams")

_UNWRAP = {"smart": "smart_project", "angle": "unwrap", "conformal": "unwrap", "cube": "cube_project",
           "cylinder": "cylinder_project", "sphere": "sphere_project", "lightmap": "lightmap_pack"}


def _counts(obj):
    me = obj.data
    return {"verts": len(me.vertices), "faces": len(me.polygons)}


@contextlib.contextmanager
def _edit(obj):
    util.ensure_object_mode()
    util.select_only([obj], active=obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    try:
        yield bmesh.from_edit_mesh(obj.data)
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')


def _deselect_all(bm):
    for seq in (bm.verts, bm.edges, bm.faces):
        for x in seq:
            x.select = False


def _select_faces(bm, obj, sel):
    if not bm.faces:
        # Curves-like meshes (a circle, an edge profile) have no faces: act on every vertex.
        if sel and not sel.get("all"):
            raise util.UserError(f"{obj.name!r} has no faces, so a face selection cannot match",
                                 hint="Omit select to act on all of its vertices")
        _deselect_all(bm)
        bpy.context.tool_settings.mesh_select_mode = (True, False, False)
        for v in bm.verts:
            v.select = True
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
        return []
    faces = require_faces(pick_faces(bm, obj, sel), sel)
    _deselect_all(bm)
    for f in faces:
        f.select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)
    return faces


def _select_edges(bm, obj, edges):
    bpy.context.tool_settings.mesh_select_mode = (False, True, False)
    _deselect_all(bm)
    for e in edges:
        e.select_set(True)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)


def _is_closed(bm):
    return all(e.is_manifold for e in bm.edges)


def _edges_of(faces, sharp_deg=None):
    edges = {e for f in faces for e in f.edges}
    if sharp_deg is not None:
        limit = math.radians(float(sharp_deg))
        edges = {e for e in edges if len(e.link_faces) == 2 and e.calc_face_angle(0.0) >= limit}
    return list(edges)


@registry.command("edit_mesh")
def edit_mesh(args):
    obj = util.get_object(args["object"])
    if obj.type != "MESH":
        raise util.UserError(f"{obj.name!r} is a {obj.type}, not a mesh",
                             hint="Convert it first with run_python: bpy.ops.object.convert(target='MESH')")
    op = args["operation"]
    if op not in OPERATIONS:
        raise util.UserError(f"Unknown operation {op!r}", hint="One of: " + ", ".join(OPERATIONS))
    sel = args.get("select")
    before = _counts(obj)
    notes = []

    if op == "loop_cut":
        picked = _loop_cut(obj, args, sel)
    elif op in ("shade_smooth", "shade_flat"):
        picked = _shade(obj, op, args)
    else:
        with _edit(obj) as bm:
            faces = _select_faces(bm, obj, sel)
            picked = len(faces)
            if op == "extrude":
                d = float(args.get("distance", 0.2))
                if args.get("individual"):
                    bpy.ops.mesh.extrude_faces_move(TRANSFORM_OT_shrink_fatten={"value": d})
                else:
                    bpy.ops.mesh.extrude_region_shrink_fatten(TRANSFORM_OT_shrink_fatten={"value": d})
            elif op == "inset":
                individual = bool(args.get("individual", False))
                if not individual and picked == len(bm.faces) and _is_closed(bm):
                    individual = True
                    notes.append("A region inset of a whole closed mesh changes nothing, so each face was inset individually.")
                bpy.ops.mesh.inset(thickness=float(args.get("thickness", 0.1)), depth=float(args.get("depth", 0.0)),
                                   use_individual=individual)
            elif op == "bevel":
                edges = _edges_of(faces, args.get("sharp_angle_deg"))
                if not edges:
                    raise util.UserError("No edges to bevel in the selection",
                                         hint="Lower sharp_angle_deg or widen the selection")
                affect = "VERTICES" if args.get("affect") == "vertices" else "EDGES"
                if affect == "EDGES":
                    _select_edges(bm, obj, edges)
                bpy.ops.mesh.bevel(offset=float(args.get("width", 0.05)), segments=int(args.get("segments", 3)),
                                   profile=float(args.get("profile", 0.5)), affect=affect)
            elif op == "subdivide":
                bpy.ops.mesh.subdivide(number_cuts=int(args.get("cuts", 1)),
                                       smoothness=float(args.get("smoothness", 0.0)))
            elif op == "bisect":
                clear = args.get("clear", "none")
                bpy.ops.mesh.bisect(plane_co=Vector(args.get("plane_point", (0, 0, 0))),
                                    plane_no=Vector(args.get("plane_normal", (0, 0, 1))),
                                    use_fill=bool(args.get("fill", False)),
                                    clear_inner=clear == "below", clear_outer=clear == "above")
            elif op == "spin":
                bpy.ops.mesh.spin(steps=int(args.get("steps", 12)), angle=math.radians(float(args.get("angle_deg", 360))),
                                  center=Vector(args.get("center", (0, 0, 0))), axis=Vector(args.get("axis", (0, 0, 1))))
            elif op == "merge":
                bpy.ops.mesh.remove_doubles(threshold=float(args.get("distance", 0.0001)))
            elif op == "delete":
                bpy.ops.mesh.delete(type='FACE')
            elif op == "dissolve":
                bpy.ops.mesh.dissolve_limited(angle_limit=math.radians(float(args.get("angle_deg", 5))))
            elif op == "flip_normals":
                bpy.ops.mesh.flip_normals()
            elif op == "recalc_normals":
                bpy.ops.mesh.normals_make_consistent(inside=False)
            elif op == "triangulate":
                bpy.ops.mesh.quads_convert_to_tris()
            elif op == "mark_seams":
                edges = _edges_of(faces, args.get("sharp_angle_deg", 60))
                _select_edges(bm, obj, edges)
                bpy.ops.mesh.mark_seam(clear=False)
            elif op == "unwrap":
                method = args.get("method", "smart")
                if method not in _UNWRAP:
                    raise util.UserError(f"Unknown unwrap method {method!r}", hint="One of: " + ", ".join(_UNWRAP))
                fn = getattr(bpy.ops.uv, _UNWRAP[method])
                margin = float(args.get("margin", 0.01))
                if method in ("angle", "conformal") and not any(e.seam for f in faces for e in f.edges):
                    # Angle-based unwrapping cannot flatten a closed shape without seams; Blender
                    # only prints a warning and leaves broken UVs. Cut seams at sharp edges first.
                    _select_edges(bm, obj, _edges_of(faces, 60))
                    bpy.ops.mesh.mark_seam(clear=False)
                    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
                    _select_faces(bm, obj, sel)
                    notes.append("There were no UV seams, so seams were marked on edges sharper than 60 degrees first.")
                if method == "smart":
                    fn(island_margin=margin)
                elif method in ("angle", "conformal"):
                    fn(method="ANGLE_BASED" if method == "angle" else "CONFORMAL", margin=margin)
                else:
                    fn()
    after = _counts(obj)
    result = {"object": obj.name, "operation": op, "faces_selected": picked, "before": before, "after": after,
              "dimensions": util.rv(obj.dimensions)}
    if op == "unwrap":
        result["uv_layers"] = [uv.name for uv in obj.data.uv_layers]
    if notes:
        result["notes"] = notes
    return result


def _loop_cut(obj, args, sel):
    """Loop cuts across the selection, perpendicular to `axis` (bmesh; the operator crashes headless)."""
    axis = {"x": Vector((1, 0, 0)), "y": Vector((0, 1, 0)), "z": Vector((0, 0, 1))}.get(str(args.get("axis", "z")).lower())
    if axis is None:
        raise util.UserError("loop_cut needs axis 'x', 'y' or 'z' (the cuts go across that axis)")
    cuts = int(args.get("cuts", 1))
    util.ensure_object_mode()
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        faces = require_faces(pick_faces(bm, obj, sel), sel)
        rot = obj.matrix_world.to_3x3()
        edges = []
        for e in {e for f in faces for e in f.edges}:
            d = rot @ (e.verts[1].co - e.verts[0].co)
            if d.length > 1e-9 and abs(d.normalized().dot(axis)) > 0.9:
                edges.append(e)
        if not edges:
            raise util.UserError(f"No edges run along the {args.get('axis', 'z')} axis in the selection",
                                 hint="Pick another axis; a loop cut splits the edges that run along it")
        bmesh.ops.subdivide_edges(bm, edges=edges, cuts=cuts, use_grid_fill=True)
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    obj.data.update()
    return len(faces)


def _shade(obj, op, args):
    util.ensure_object_mode()
    util.select_only([obj], active=obj)
    if op == "shade_flat":
        bpy.ops.object.shade_flat()
    elif args.get("angle_deg") is not None:
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(float(args["angle_deg"])))
    else:
        bpy.ops.object.shade_smooth()
    return len(obj.data.polygons)
