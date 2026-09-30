"""Brush maths shared by sculpt and paint.

Blender's own brush operators need the interactive viewport, so Ghostblend
applies brushes itself: strokes are world-space points, sampled into stamps,
each stamp affecting vertices within `radius` with a falloff curve.
"""
import numpy as np
from mathutils import Vector

from . import util

FALLOFFS = ("smooth", "sphere", "linear", "sharp", "constant")


def falloff(t, kind):
    """Weight in [0, 1] for normalised distance t in [0, 1] (0 at the centre)."""
    t = np.clip(t, 0.0, 1.0)
    if kind == "linear":
        return 1.0 - t
    if kind == "sharp":
        return (1.0 - t) ** 2
    if kind == "sphere":
        return np.sqrt(np.clip(1.0 - t * t, 0.0, 1.0))
    if kind == "constant":
        return np.ones_like(t)
    # smooth: 3t^2 - 2t^3 mirrored, like Blender's default curve
    s = 1.0 - t
    return s * s * (3.0 - 2.0 * s)


def check_falloff(kind):
    if kind not in FALLOFFS:
        raise util.UserError(f"Unknown falloff {kind!r}", hint="One of: " + ", ".join(FALLOFFS))


def stamps(points, radius, spacing):
    """Sample a polyline of points into stamps `spacing * radius` apart."""
    pts = [np.asarray(p, dtype=np.float64) for p in points]
    if len(pts) == 1:
        return pts
    step = max(radius * spacing, 1e-6)
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        seg = b - a
        n = max(1, int(np.ceil(np.linalg.norm(seg) / step)))
        for i in range(1, n + 1):
            out.append(a + seg * (i / n))
    return out


def mirror(points, axes):
    """Stroke copies mirrored across the object's local axis planes (x, y, z)."""
    copies = [list(points)]
    for axis in axes or []:
        idx = "xyz".index(axis)
        new = []
        for stroke in copies:
            m = []
            for p in stroke:
                q = np.array(p, dtype=np.float64)
                q[idx] = -q[idx]
                m.append(q)
            new.append(m)
        copies += new
    return copies


def check_axes(axes):
    for a in axes or []:
        if a not in ("x", "y", "z"):
            raise util.UserError(f"symmetry axes must be 'x', 'y' or 'z' (got {a!r})")


def to_local(obj, points, radius):
    """World-space stroke points and radius into the object's local space."""
    inv = obj.matrix_world.inverted()
    local = [np.array(inv @ Vector(p)) for p in points]
    scale = obj.matrix_world.to_scale()
    mean_scale = max((abs(scale.x) + abs(scale.y) + abs(scale.z)) / 3.0, 1e-9)
    return local, radius / mean_scale


def vertex_positions(me):
    co = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def vertex_normals(me):
    me.update()
    n = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertex_normals.foreach_get("vector", n)
    return n.reshape(-1, 3)


def edge_pairs(me):
    e = np.empty(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", e)
    return e.reshape(-1, 2)
