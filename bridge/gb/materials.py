"""material_set and material summaries (Principled BSDF based)."""
import bpy

from . import registry, util
from .util import r, rv

# Principled BSDF socket names across Blender 4.x / 5.x (first match wins).
_INPUTS = {
    "base_color": ["Base Color"],
    "metallic": ["Metallic"],
    "roughness": ["Roughness"],
    "ior": ["IOR"],
    "alpha": ["Alpha"],
    "transmission": ["Transmission Weight", "Transmission"],
    "emission_color": ["Emission Color", "Emission"],
    "emission_strength": ["Emission Strength"],
}


def _input(node, key):
    for name in _INPUTS[key]:
        sock = node.inputs.get(name)
        if sock is not None:
            return sock
    return None


def _principled(mat, create=False):
    if create and getattr(mat, "use_nodes", True) is False:
        try:
            mat.use_nodes = True
        except (AttributeError, TypeError):
            pass
    nt = mat.node_tree
    if nt is None:
        return None
    for n in nt.nodes:
        if n.type == "BSDF_PRINCIPLED":
            return n
    if not create:
        return None
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if out is None:
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        out.location = (300, 0)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return bsdf


def _rgba(c):
    c = [float(x) for x in c]
    return c + [1.0] if len(c) == 3 else c[:4]


def material_detail(mat):
    d = {"name": mat.name, "viewport_color": rv(mat.diffuse_color)}
    bsdf = _principled(mat)
    if bsdf is not None:
        for key in ("base_color", "metallic", "roughness", "alpha", "transmission", "ior",
                    "emission_color", "emission_strength"):
            sock = _input(bsdf, key)
            if sock is None:
                continue
            if sock.is_linked:
                d[key] = "linked to a node"
            else:
                v = sock.default_value
                d[key] = rv(v) if hasattr(v, "__len__") else r(v)
    if mat.node_tree is not None:
        images = [n.image.filepath or n.image.name for n in mat.node_tree.nodes
                  if n.type == "TEX_IMAGE" and n.image is not None]
        if images:
            d["textures"] = images
    d["users"] = mat.users
    return d


def _image_node_for_base_color(nt, bsdf):
    sock = _input(bsdf, "base_color")
    for link in nt.links:
        if link.to_socket == sock and link.from_node.type == "TEX_IMAGE":
            return link.from_node
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.location = (bsdf.location.x - 320, bsdf.location.y)
    nt.links.new(tex.outputs["Color"], sock)
    return tex


@registry.command("material_set")
def material_set(args):
    obj = util.get_object(args.get("object"))
    data = obj.data
    if data is None or not hasattr(data, "materials"):
        raise util.UserError(f"Object {obj.name!r} ({obj.type}) cannot hold materials",
                             hint="Materials work on meshes, curves, text, surfaces and metaballs")
    name = args.get("name")
    if name:
        mat = bpy.data.materials.get(name)
    else:
        # No name: edit the material already in the target slot instead of replacing it,
        # so earlier work on it (painted colours, textures) is kept.
        idx = 0 if args.get("slot") is None else int(args["slot"])
        mat = obj.material_slots[idx].material if idx < len(obj.material_slots) else None
    created = mat is None
    if mat is None:
        mat = bpy.data.materials.new(name or f"{obj.name}_Material")
    bsdf = _principled(mat, create=True)
    if bsdf is None:
        raise util.UserError("Could not create a node-based material for this Blender version")

    def put(key, value):
        sock = _input(bsdf, key)
        if sock is not None:
            sock.default_value = value

    if "base_color" in args:
        c = _rgba(args["base_color"])
        put("base_color", c)
        mat.diffuse_color = c
    if "metallic" in args:
        put("metallic", float(args["metallic"]))
        mat.metallic = float(args["metallic"])
    if "roughness" in args:
        put("roughness", float(args["roughness"]))
        mat.roughness = float(args["roughness"])
    if "ior" in args:
        put("ior", float(args["ior"]))
    if "transmission" in args:
        put("transmission", float(args["transmission"]))
    if "alpha" in args:
        a = float(args["alpha"])
        put("alpha", a)
        col = list(mat.diffuse_color)
        col[3] = a
        mat.diffuse_color = col
        if a < 1.0 and hasattr(mat, "blend_method") and not hasattr(mat, "surface_render_method"):
            mat.blend_method = "HASHED"
    if "emission_color" in args:
        put("emission_color", _rgba(args["emission_color"]))
        strength = _input(bsdf, "emission_strength")
        if "emission_strength" not in args and strength is not None and strength.default_value == 0.0:
            strength.default_value = 1.0
    if "emission_strength" in args:
        put("emission_strength", float(args["emission_strength"]))
    if args.get("texture_image_path"):
        path = util.resolve_path(args["texture_image_path"], must_exist=True, what="image")
        img = bpy.data.images.load(path, check_existing=True)
        tex = _image_node_for_base_color(mat.node_tree, bsdf)
        tex.image = img
        mat.node_tree.nodes.active = tex

    mats = data.materials
    slot = args.get("slot")
    if args.get("replace_all"):
        if len(mats) == 0:
            mats.append(mat)
        for s in obj.material_slots:
            s.material = mat
        slot_index = "all"
    else:
        slot_index = 0 if slot is None else int(slot)
        if slot_index == len(mats):
            mats.append(mat)
        elif 0 <= slot_index < len(mats):
            obj.material_slots[slot_index].material = mat
        else:
            raise util.UserError(f"slot {slot_index} is out of range; {obj.name!r} has {len(mats)} slots",
                                 hint=f"Use a slot from 0 to {len(mats)} ({len(mats)} appends a new slot)")
    return {"object": obj.name, "slot": slot_index, "created": created, "material": material_detail(mat)}
