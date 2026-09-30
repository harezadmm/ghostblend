"""paint: vertex painting and texture painting without a viewport.

Blender's paint strokes (bpy.ops.paint.vertex_paint / image_paint) need the
interactive viewport. Ghostblend paints the data directly: vertex colours in a
colour attribute, or texels of an image texture found through the mesh UVs.
Strokes are world-space points like sculpt; without points it fills the
selected faces.
"""
import numpy as np

import bpy

from . import brush as B
from . import materials, registry, util
from .selection import pick_faces, require_faces

TARGETS = ("vertex", "texture")
BLENDS = ("mix", "add", "multiply", "subtract", "lighten", "darken")


def _rgba(c):
    c = [float(x) for x in c]
    return np.array(c + [1.0] if len(c) == 3 else c[:4], dtype=np.float64)


def _blend(dst, src, w, mode):
    w = w[:, None]
    if mode == "add":
        out = dst + src * w
    elif mode == "subtract":
        out = dst - src * w
    elif mode == "multiply":
        out = dst * (1.0 - w + src * w)
    elif mode == "lighten":
        out = dst + (np.maximum(dst, src) - dst) * w
    elif mode == "darken":
        out = dst + (np.minimum(dst, src) - dst) * w
    else:
        out = dst * (1.0 - w) + src * w
    return np.clip(out, 0.0, 1.0)


def _selected_face_indices(obj, sel):
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        return {f.index for f in require_faces(pick_faces(bm, obj, sel), sel)}
    finally:
        bm.free()


def _stroke_stamps(obj, args, radius):
    local, r = B.to_local(obj, args["points"], radius)
    out = []
    for pts, _signs in _mirrored(local, args.get("symmetry")):
        out += B.stamps(pts, r, float(args.get("spacing", 0.25)))
    return np.array(out), r


def _mirrored(points, axes):
    B.check_axes(axes)
    copies = [[np.asarray(p, dtype=np.float64) for p in points]]
    for axis in axes or []:
        k = "xyz".index(axis)
        flip = np.ones(3)
        flip[k] = -1.0
        copies += [[q * flip for q in pts] for pts in copies]
    return [(c, None) for c in copies]


def _weights_for_positions(pos, stamp_pts, r, strength, kind):
    """Max brush weight over all stamps for each position (k, 3)."""
    w = np.zeros(len(pos))
    for p in stamp_pts:
        d = np.linalg.norm(pos - p, axis=1)
        np.maximum(w, B.falloff(d / r, kind) * (d < r) * strength, out=w)
    return w


def _ensure_material(obj):
    if not obj.data.materials:
        mat = bpy.data.materials.new(f"{obj.name}_Painted")
        obj.data.materials.append(mat)
    mat = obj.active_material or obj.data.materials[0]
    bsdf = materials._principled(mat, create=True)
    return mat, bsdf


def _link_color_attribute(obj, layer):
    mat, bsdf = _ensure_material(obj)
    sock = bsdf.inputs["Base Color"]
    if sock.is_linked:
        src = sock.links[0].from_node
        if src.type == "VERTEX_COLOR" and getattr(src, "layer_name", "") == layer:
            return None
        return f"{mat.name}'s base colour is already driven by a {src.type} node, so it was left as is."
    node = mat.node_tree.nodes.new("ShaderNodeVertexColor")
    node.layer_name = layer
    node.location = (bsdf.location.x - 300, bsdf.location.y)
    mat.node_tree.links.new(node.outputs["Color"], sock)
    return None


def _paint_vertex(obj, args, color, strength, kind, mode):
    me = obj.data
    layer = args.get("layer", "Color")
    ca = me.color_attributes.get(layer)
    created = ca is None
    if created:
        ca = me.color_attributes.new(layer, "FLOAT_COLOR", "POINT")
        ca.data.foreach_set("color", np.ones(len(ca.data) * 4, dtype=np.float32))
    me.color_attributes.active_color = ca
    co = B.vertex_positions(me)
    if args.get("points"):
        stamp_pts, r = _stroke_stamps(obj, args, float(args.get("radius", 0.25)))
        w_vert = _weights_for_positions(co, stamp_pts, r, strength, kind)
    else:
        faces = _selected_face_indices(obj, args.get("select"))
        w_vert = np.zeros(len(co))
        for p in me.polygons:
            if p.index in faces:
                w_vert[list(p.vertices)] = strength
    if ca.domain == "CORNER":
        loop_vert = np.empty(len(me.loops), dtype=np.int64)
        me.loops.foreach_get("vertex_index", loop_vert)
        w = w_vert[loop_vert]
    elif ca.domain == "POINT":
        w = w_vert
    else:
        raise util.UserError(f"Colour attribute {layer!r} is on the {ca.domain} domain; paint needs POINT or CORNER")
    cur = np.empty(len(ca.data) * 4, dtype=np.float64)
    ca.data.foreach_get("color", cur)
    cur = cur.reshape(-1, 4)
    mask = w > 0
    cur[mask] = _blend(cur[mask], color, w[mask], mode)
    ca.data.foreach_set("color", cur.ravel().astype(np.float32))
    me.update()
    notes = []
    if args.get("link_material", True):
        n = _link_color_attribute(obj, layer)
        if n:
            notes.append(n)
    return {"layer": layer, "created_layer": created, "painted": int(mask.sum()), "domain": ca.domain}, notes


def _ensure_uvs(obj, notes):
    if obj.data.uv_layers:
        return
    util.ensure_object_mode()
    util.select_only([obj], active=obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    notes.append("The mesh had no UVs, so it was UV-unwrapped with Smart UV Project.")


def _ensure_image(obj, args, notes):
    mat, bsdf = _ensure_material(obj)
    sock = bsdf.inputs["Base Color"]
    if sock.is_linked and sock.links[0].from_node.type == "TEX_IMAGE" and sock.links[0].from_node.image:
        return sock.links[0].from_node.image
    res = int(args.get("resolution", 1024))
    base = list(sock.default_value)
    img = bpy.data.images.new(args.get("image") or f"{obj.name}_Paint", res, res, alpha=True)
    img.pixels.foreach_set(np.tile(np.array(base, dtype=np.float32), res * res))
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = img
    node.location = (bsdf.location.x - 320, bsdf.location.y)
    mat.node_tree.links.new(node.outputs["Color"], sock)
    mat.node_tree.nodes.active = node
    notes.append(f"Created a {res}x{res} texture {img.name!r} and connected it to {mat.name}'s base colour.")
    return img


def _loop_uvs(me):
    uvl = me.uv_layers.active
    uv = np.empty(len(me.loops) * 2, dtype=np.float64)
    try:
        uvl.uv.foreach_get("vector", uv)
    except AttributeError:
        uvl.data.foreach_get("uv", uv)
    return uv.reshape(-1, 2)


def _paint_texture(obj, args, color, strength, kind, mode):
    notes = []
    _ensure_uvs(obj, notes)
    img = _ensure_image(obj, args, notes)
    me = obj.data
    me.calc_loop_triangles()
    nt = len(me.loop_triangles)
    tri_loops = np.empty(nt * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("loops", tri_loops)
    tri_verts = np.empty(nt * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", tri_verts)
    tri_poly = np.empty(nt, dtype=np.int64)
    me.loop_triangles.foreach_get("polygon_index", tri_poly)
    co = B.vertex_positions(me)
    tri_co = co[tri_verts].reshape(nt, 3, 3)
    tri_uv = _loop_uvs(me)[tri_loops].reshape(nt, 3, 2)

    W, H = img.size
    raw = np.empty(W * H * 4, dtype=np.float32)  # Blender only fills float32 buffers
    img.pixels.foreach_get(raw)
    px = raw.astype(np.float64).reshape(H, W, 4)
    weight = np.zeros((H, W))

    if args.get("points"):
        stamp_pts, r = _stroke_stamps(obj, args, float(args.get("radius", 0.25)))
        centers = tri_co.mean(axis=1)
        reach = np.linalg.norm(tri_co - centers[:, None, :], axis=2).max(axis=1)
        near = np.zeros(nt, dtype=bool)
        for p in stamp_pts:
            near |= np.linalg.norm(centers - p, axis=1) < r + reach
        candidates = np.nonzero(near)[0]
    else:
        faces = _selected_face_indices(obj, args.get("select"))
        candidates = np.nonzero(np.isin(tri_poly, list(faces)))[0]
        stamp_pts, r = None, None

    size = np.array([W, H], dtype=np.float64)
    for t in candidates:
        uvp = tri_uv[t] * size
        x0, y0 = np.floor(uvp.min(axis=0)).astype(int)
        x1, y1 = np.ceil(uvp.max(axis=0)).astype(int)
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, W - 1), min(y1, H - 1)
        if x1 < x0 or y1 < y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        pts = np.stack([xs.ravel(), ys.ravel()], axis=1)
        a, b, c = uvp
        v0, v1 = b - a, c - a
        den = v0[0] * v1[1] - v1[0] * v0[1]
        if abs(den) < 1e-12:
            continue
        d = pts - a
        l1 = (d[:, 0] * v1[1] - v1[0] * d[:, 1]) / den
        l2 = (v0[0] * d[:, 1] - d[:, 0] * v0[1]) / den
        l0 = 1.0 - l1 - l2
        eps = 1.5 / max(np.linalg.norm(v0), np.linalg.norm(v1), 1.0)  # about one texel of slack at seams
        inside = (l0 >= -eps) & (l1 >= -eps) & (l2 >= -eps)
        if not inside.any():
            continue
        tx = (pts[inside, 0] - 0.5).astype(int)
        ty = (pts[inside, 1] - 0.5).astype(int)
        if stamp_pts is None:
            w = np.full(len(tx), strength)
        else:
            pos = (l0[inside, None] * tri_co[t, 0] + l1[inside, None] * tri_co[t, 1] + l2[inside, None] * tri_co[t, 2])
            w = _weights_for_positions(pos, stamp_pts, r, strength, kind)
        np.maximum.at(weight, (ty, tx), w)

    mask = weight > 0
    flat = px.reshape(-1, 4)
    wf = weight.ravel()
    sel = mask.ravel()
    flat[sel] = _blend(flat[sel], color, wf[sel], mode)
    img.pixels.foreach_set(flat.ravel().astype(np.float32))
    img.update()
    path = None
    if args.get("save_path"):
        path = util.resolve_path(args["save_path"])
        util.ensure_parent_dir(path)
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
    else:
        img.pack()  # keep the painted pixels inside the .blend (and every autosave)
    return {"image": img.name, "resolution": [W, H], "painted_texels": int(mask.sum()), "path": path}, notes


@registry.command("paint")
def paint(args):
    obj = util.get_object(args["object"])
    if obj.type != "MESH":
        raise util.UserError(f"{obj.name!r} is a {obj.type}; painting works on meshes")
    util.ensure_object_mode()
    target = args.get("target", "vertex")
    if target not in TARGETS:
        raise util.UserError(f"Unknown target {target!r}", hint="One of: " + ", ".join(TARGETS))
    mode = args.get("blend", "mix")
    if mode not in BLENDS:
        raise util.UserError(f"Unknown blend {mode!r}", hint="One of: " + ", ".join(BLENDS))
    kind = args.get("falloff", "smooth")
    B.check_falloff(kind)
    color = _rgba(args.get("color", (1, 1, 1, 1)))
    strength = float(args.get("strength", 1.0))
    if target == "vertex":
        info, notes = _paint_vertex(obj, args, color, strength, kind, mode)
    else:
        info, notes = _paint_texture(obj, args, color, strength, kind, mode)
    result = {"object": obj.name, "target": target, **info}
    if notes:
        result["notes"] = notes
    return result
