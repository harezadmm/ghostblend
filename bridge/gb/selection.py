"""Face selection by criteria, for tools that act on part of a mesh.

Headless there is no mouse, so an agent selects faces by description instead:
by the direction they face, by a world-space box, by material slot, or by index.
All criteria given must match (they are combined with AND).
"""
import math

from mathutils import Vector

from . import util

SCHEMA_HELP = ("select: {all, facing:[x,y,z], angle_deg, box_min:[x,y,z], box_max:[x,y,z], "
               "material:int, indices:[int]}")


def pick_faces(bm, obj, sel):
    """Return the BMFaces of `bm` (a bmesh of `obj`) that match `sel`."""
    sel = sel or {}
    faces = list(bm.faces)
    if not sel or sel.get("all"):
        return faces
    mw = obj.matrix_world
    nmat = mw.to_3x3().inverted().transposed()
    out = faces
    if sel.get("facing") is not None:
        target = Vector(sel["facing"])
        if target.length < 1e-9:
            raise util.UserError("select.facing must be a non-zero direction, for example [0, 0, 1] for faces pointing up")
        target.normalize()
        limit = math.cos(math.radians(float(sel.get("angle_deg", 30))))
        out = [f for f in out if (nmat @ f.normal).normalized().dot(target) >= limit]
    if sel.get("box_min") is not None or sel.get("box_max") is not None:
        lo = Vector(sel.get("box_min", (-math.inf,) * 3))
        hi = Vector(sel.get("box_max", (math.inf,) * 3))
        def inside(f):
            c = mw @ f.calc_center_median()
            return all(lo[i] <= c[i] <= hi[i] for i in range(3))
        out = [f for f in out if inside(f)]
    if sel.get("material") is not None:
        m = int(sel["material"])
        out = [f for f in out if f.material_index == m]
    if sel.get("indices") is not None:
        wanted = {int(i) for i in sel["indices"]}
        out = [f for f in out if f.index in wanted]
    return out


def require_faces(faces, sel):
    if not faces:
        raise util.UserError("The selection matched no faces",
                             hint="Loosen it (bigger angle_deg or box), or pass select={'all': true}. " + SCHEMA_HELP)
    return faces
