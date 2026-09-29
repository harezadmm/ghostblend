"""modifier_add and modifier_apply."""
import bpy

from . import registry, scene, util

# Friendly names -> Blender modifier type enum.
_MOD_TYPES = {
    "subdivision": "SUBSURF", "subsurf": "SUBSURF", "mirror": "MIRROR", "array": "ARRAY",
    "boolean": "BOOLEAN", "bevel": "BEVEL", "solidify": "SOLIDIFY", "decimate": "DECIMATE",
    "triangulate": "TRIANGULATE", "weld": "WELD", "displace": "DISPLACE", "remesh": "REMESH",
    "wireframe": "WIREFRAME", "screw": "SCREW", "smooth": "SMOOTH", "edge_split": "EDGE_SPLIT",
}


def _modifiers_summary(obj):
    return [{"name": m.name, "type": m.type} for m in obj.modifiers]


@registry.command("modifier_add")
def modifier_add(args):
    obj = util.get_object(args["name"])
    if not hasattr(obj, "modifiers"):
        raise util.UserError(f"{obj.name!r} ({obj.type}) does not take modifiers")
    friendly = args["type"]
    mod_type = _MOD_TYPES.get(friendly.lower())
    if mod_type is None:
        raise util.UserError(f"Unknown modifier type {friendly!r}",
                             hint="One of: " + ", ".join(sorted(_MOD_TYPES)))
    mod = obj.modifiers.new(name=friendly.capitalize(), type=mod_type)
    params = args.get("params") or {}
    if mod_type == "SUBSURF" and "levels" not in params and "render_levels" not in params:
        params = {**params, "levels": 2}
    util.set_rna_props(mod, params, f"{friendly} modifier")

    if args.get("apply"):
        _apply_one(obj, mod)
        return {"object": obj.name, "applied": True, "modifiers": _modifiers_summary(obj),
                "mesh": scene.obj_summary(obj).get("mesh")}
    return {"object": obj.name, "added": mod.name, "modifiers": _modifiers_summary(obj)}


def _apply_one(obj, mod):
    util.ensure_object_mode()
    util.select_only([obj], active=obj)
    try:
        bpy.ops.object.modifier_apply(modifier=mod.name)
    except RuntimeError as e:
        raise util.UserError(f"Could not apply modifier {mod.name!r}: {e}")


@registry.command("modifier_apply")
def modifier_apply(args):
    obj = util.get_object(args["name"])
    which = args.get("modifier")
    if which is not None:
        mod = obj.modifiers.get(which)
        if mod is None:
            raise util.UserError(f"{obj.name!r} has no modifier named {which!r}",
                                 hint="Modifiers: " + ", ".join(m.name for m in obj.modifiers))
        _apply_one(obj, mod)
        applied = [which]
    else:
        applied = [m.name for m in obj.modifiers]
        for name in applied:
            mod = obj.modifiers.get(name)
            if mod is not None:
                _apply_one(obj, mod)
    return {"object": obj.name, "applied": applied, "modifiers": _modifiers_summary(obj),
            "mesh": scene.obj_summary(obj).get("mesh")}
