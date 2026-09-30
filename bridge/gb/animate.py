"""animate: keyframes for any animatable property."""
import math

import bpy

from . import registry, util

INTERPOLATIONS = {"bezier": "BEZIER", "linear": "LINEAR", "constant": "CONSTANT"}


def fcurves_of(id_data):
    """F-curves of an ID's active action, for layered (4.4+) and legacy actions."""
    ad = getattr(id_data, "animation_data", None)
    if ad is None or ad.action is None:
        return None
    act = ad.action
    try:
        from bpy_extras import anim_utils

        bag = anim_utils.action_get_channelbag_for_slot(act, ad.action_slot)
        if bag is not None:
            return bag.fcurves
    except (ImportError, AttributeError):
        pass
    return getattr(act, "fcurves", None)


def _split(path):
    """'modifiers["Bevel"].width' -> ('modifiers["Bevel"]', 'width'), ignoring dots inside brackets."""
    depth = 0
    for i in range(len(path) - 1, -1, -1):
        ch = path[i]
        if ch == "]":
            depth += 1
        elif ch == "[":
            depth -= 1
        elif ch == "." and depth == 0:
            return path[:i], path[i + 1:]
    return "", path


def _resolve(obj, prop):
    if prop == "rotation_deg":
        return obj, "rotation_euler", lambda v: [math.radians(x) for x in v]
    head, attr = _split(prop)
    try:
        owner = obj.path_resolve(head) if head else obj
    except ValueError:
        raise util.UserError(f"Cannot find {head!r} on {obj.name!r}",
                             hint="Examples: location, rotation_deg, scale, data.energy, modifiers[\"Bevel\"].width")
    if not hasattr(owner, "bl_rna") or attr not in owner.bl_rna.properties:
        valid = [p.identifier for p in getattr(owner, "bl_rna", bpy.types.Object.bl_rna).properties
                 if p.is_animatable and not p.is_readonly][:40]
        raise util.UserError(f"{prop!r} is not an animatable property of {obj.name!r}",
                             hint="Animatable here: " + ", ".join(valid))
    return owner, attr, None


@registry.command("animate")
def animate(args):
    obj = util.get_object(args["object"])
    prop = args["property"]
    keys = args.get("keys") or []
    if not keys:
        raise util.UserError("Pass keys=[{frame, value}, ...] with at least one keyframe")
    interp = args.get("interpolation", "bezier")
    if interp not in INTERPOLATIONS:
        raise util.UserError(f"Unknown interpolation {interp!r}", hint="One of: " + ", ".join(INTERPOLATIONS))
    owner, attr, convert = _resolve(obj, prop)
    data_path = owner.path_from_id(attr)
    id_data = owner.id_data

    if args.get("clear"):
        fcs = fcurves_of(id_data)
        if fcs is not None:
            for fc in [f for f in fcs if f.data_path == data_path]:
                fcs.remove(fc)

    frames = []
    for k in keys:
        if "frame" not in k or "value" not in k:
            raise util.UserError("Each key needs a frame and a value, for example {\"frame\": 1, \"value\": [0, 0, 0]}")
        value = k["value"]
        value = convert(value) if convert else value
        try:
            setattr(owner, attr, value)
        except (TypeError, ValueError) as e:
            raise util.UserError(f"Cannot set {prop} to {k['value']!r}: {e}")
        frame = int(k["frame"])
        owner.keyframe_insert(attr, frame=frame)
        frames.append(frame)

    fcs = fcurves_of(id_data)
    touched = 0
    if fcs is not None:
        for fc in fcs:
            if fc.data_path == data_path:
                for kp in fc.keyframe_points:
                    kp.interpolation = INTERPOLATIONS[interp]
                fc.update()
                touched += 1

    sc = bpy.context.scene
    notes = []
    if args.get("frame_range"):
        sc.frame_start, sc.frame_end = int(args["frame_range"][0]), int(args["frame_range"][1])
    elif max(frames) > sc.frame_end or min(frames) < sc.frame_start:
        sc.frame_start = min(sc.frame_start, min(frames))
        sc.frame_end = max(sc.frame_end, max(frames))
        notes.append(f"The scene frame range was widened to {sc.frame_start}-{sc.frame_end} to include the keys.")
    sc.frame_set(sc.frame_current)
    result = {"object": obj.name, "property": prop, "data_path": data_path, "keys": len(frames),
              "frames": [min(frames), max(frames)], "interpolation": interp, "fcurves": touched,
              "frame_range": [sc.frame_start, sc.frame_end]}
    if notes:
        result["notes"] = notes
    return result
