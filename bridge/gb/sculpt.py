"""sculpt: brush sculpting without a viewport.

Blender's sculpt brushes (bpy.ops.sculpt.brush_stroke) need the interactive
viewport, and its mesh filter crashes in background mode. Ghostblend applies
the classic brushes itself on vertex positions: a stroke is a list of
world-space points; each stamp moves vertices within `radius` by the brush
rule, weighted by a falloff curve. Voxel remesh and subdivision provide the
resolution sculpting needs, like Dyntopo or Multires would.
"""
import numpy as np
from mathutils import Vector

import bpy

from . import brush as B
from . import registry, util

BRUSHES = ("draw", "clay", "inflate", "smooth", "flatten", "pinch", "grab", "crease", "noise")
FILTER_BRUSHES = ("smooth", "inflate", "noise")
_STEP = 0.15  # displacement per full-strength stamp, as a fraction of the radius


def _neighbor_average(co, edges):
    n = len(co)
    a, b = edges[:, 0], edges[:, 1]
    cnt = np.bincount(a, minlength=n) + np.bincount(b, minlength=n)
    cnt = np.where(cnt == 0, 1, cnt).astype(np.float64)
    out = np.empty_like(co)
    for k in range(3):
        out[:, k] = (np.bincount(a, weights=co[b, k], minlength=n) + np.bincount(b, weights=co[a, k], minlength=n)) / cnt
    return out


def _area_normal(normals, w, fallback):
    n = (normals * w[:, None]).sum(axis=0)
    length = np.linalg.norm(n)
    if length < 1e-12:
        length = np.linalg.norm(fallback)
        return fallback / length if length > 1e-12 else np.array([0.0, 0.0, 1.0])
    return n / length


def _stamp(co, normals, edges, p, r, w_all, idx, brush, sign, direction, rng):
    w = w_all
    if brush == "draw":
        n = _area_normal(normals[idx], w, p - co[idx].mean(axis=0))
        co[idx] += np.outer(w * r * _STEP * sign, n)
    elif brush == "inflate":
        co[idx] += normals[idx] * (w * r * _STEP * sign)[:, None]
    elif brush in ("flatten", "clay"):
        n = _area_normal(normals[idx], w, np.array([0.0, 0.0, 1.0]))
        centroid = (co[idx] * w[:, None]).sum(axis=0) / max(w.sum(), 1e-12)
        plane = centroid + n * (r * 0.1 * sign if brush == "clay" else 0.0)
        dist = (co[idx] - plane) @ n
        if brush == "clay":
            dist = np.where(dist * sign < 0, dist, 0.0)  # only build up towards the plane
        co[idx] -= np.outer(dist * w, n)
    elif brush == "smooth":
        avg = _neighbor_average(co, edges)
        co[idx] += (avg[idx] - co[idx]) * w[:, None]
    elif brush in ("pinch", "crease"):
        n = _area_normal(normals[idx], w, np.array([0.0, 0.0, 1.0]))
        v = p - co[idx]
        v -= np.outer(v @ n, n)
        co[idx] += v * (w * 0.5 * sign)[:, None]
        if brush == "crease":
            co[idx] -= np.outer(w * r * _STEP * 0.6 * sign, n)
    elif brush == "grab":
        co[idx] += np.outer(w, direction)
    elif brush == "noise":
        co[idx] += normals[idx] * (rng.uniform(-1.0, 1.0, len(idx)) * w * r * _STEP)[:, None]


def _mirrored(points, axes):
    """(points, sign vector) pairs for the stroke and its mirror copies."""
    copies = [([np.asarray(p, dtype=np.float64) for p in points], np.ones(3))]
    for axis in axes or []:
        k = "xyz".index(axis)
        extra = []
        for pts, signs in copies:
            flip = np.ones(3)
            flip[k] = -1.0
            extra.append(([q * flip for q in pts], signs * flip))
        copies += extra
    return copies


def _pre_steps(obj, args, notes):
    if args.get("remesh"):
        size = float(args["remesh"])
        if size <= 0:
            raise util.UserError("remesh is the voxel size in meters and must be positive, for example 0.02")
        util.select_only([obj], active=obj)
        obj.data.remesh_voxel_size = size
        bpy.ops.object.voxel_remesh()
        notes.append(f"Voxel remeshed at {size} m: {len(obj.data.vertices)} vertices.")
    if args.get("subdivide"):
        levels = int(args["subdivide"])
        util.select_only([obj], active=obj)
        mod = obj.modifiers.new("gb_sculpt_subdiv", "SUBSURF")
        mod.levels = mod.render_levels = levels
        bpy.ops.object.modifier_move_to_index(modifier=mod.name, index=0)
        bpy.ops.object.modifier_apply(modifier=mod.name)
        notes.append(f"Subdivided {levels} level(s): {len(obj.data.vertices)} vertices.")


@registry.command("sculpt")
def sculpt(args):
    obj = util.get_object(args["object"])
    if obj.type != "MESH":
        raise util.UserError(f"{obj.name!r} is a {obj.type}; sculpting works on meshes")
    util.ensure_object_mode()
    notes = []
    _pre_steps(obj, args, notes)

    brush = args.get("brush")
    if brush is None:
        if not (args.get("remesh") or args.get("subdivide")):
            raise util.UserError("Pass a brush, or remesh / subdivide to add detail first",
                                 hint="Brushes: " + ", ".join(BRUSHES))
        return {"object": obj.name, "mesh": util.mesh_counts(obj.data), "notes": notes}
    if brush not in BRUSHES:
        raise util.UserError(f"Unknown brush {brush!r}", hint="One of: " + ", ".join(BRUSHES))
    kind = args.get("falloff", "smooth")
    B.check_falloff(kind)
    axes = args.get("symmetry") or []
    B.check_axes(axes)

    me = obj.data
    co = B.vertex_positions(me)
    start = co.copy()
    edges = B.edge_pairs(me)
    strength = float(args.get("strength", 0.5))
    sign = -1.0 if args.get("invert") else 1.0
    radius = float(args.get("radius", 0.25))
    rng = np.random.default_rng(int(args.get("seed", 0)))
    iterations = max(1, int(args.get("iterations", 1)))
    points = args.get("points")
    stamp_count = 0

    def refresh_normals():
        me.vertices.foreach_set("co", co.ravel())
        return B.vertex_normals(me)

    normals = B.vertex_normals(me)
    if points:
        local, r = B.to_local(obj, points, radius)
        direction = np.zeros(3)
        if brush == "grab":
            if args.get("direction") is None:
                raise util.UserError("The grab brush needs direction=[x, y, z]: how far to move the grabbed area, in meters")
            direction = np.array(obj.matrix_world.inverted().to_3x3() @ Vector(args["direction"]))
        for _ in range(iterations):
            for pts, signs in _mirrored(local, axes):
                seq = pts[:1] if brush == "grab" else B.stamps(pts, r, float(args.get("spacing", 0.25)))
                for i, p in enumerate(seq):
                    d = np.linalg.norm(co - p, axis=1)
                    idx = np.nonzero(d < r)[0]
                    if idx.size:
                        w = B.falloff(d[idx] / r, kind) * strength
                        _stamp(co, normals, edges, p, r, w, idx, brush, sign, direction * signs, rng)
                    stamp_count += 1
                    if i % 8 == 7:
                        normals = refresh_normals()
                normals = refresh_normals()
        if len(me.vertices) < 2000:
            notes.append(f"The mesh has only {len(me.vertices)} vertices, so strokes look coarse. "
                         "Add detail with subdivide=2 or remesh=0.02 first.")
    else:
        if brush not in FILTER_BRUSHES:
            raise util.UserError(f"The {brush} brush needs points=[[x, y, z], ...] to know where to stroke",
                                 hint="Without points, only " + ", ".join(FILTER_BRUSHES) + " work, applied to the whole mesh")
        idx = np.arange(len(co))
        span = float(np.linalg.norm(co.max(axis=0) - co.min(axis=0))) or 1.0
        for _ in range(iterations):
            w = np.full(len(idx), strength)
            _stamp(co, normals, edges, co.mean(axis=0), span * 0.1, w, idx, brush, sign, np.zeros(3), rng)
            stamp_count += 1
            normals = refresh_normals()

    me.vertices.foreach_set("co", co.ravel())
    me.update()
    bpy.context.view_layer.update()
    moved = np.linalg.norm(co - start, axis=1)
    result = {"object": obj.name, "brush": brush, "stamps": stamp_count,
              "vertices_moved": int((moved > 1e-7).sum()), "max_displacement": util.r(float(moved.max()) if len(moved) else 0.0),
              "mesh": util.mesh_counts(me), "dimensions": util.rv(obj.dimensions)}
    if notes:
        result["notes"] = notes
    return result
