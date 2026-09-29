"""Shared helpers for the bridge: errors, rounding, JSON conversion, paths, lookups, RNA assignment."""
import difflib
import math
import os

import bpy

# Base directory for relative paths. main() replaces it with the --workdir value.
WORKDIR = os.getcwd()


class UserError(Exception):
    """An error the agent can fix by changing its request."""

    def __init__(self, message, hint=None, kind="InvalidArgument"):
        super().__init__(message)
        self.hint = hint
        self.kind = kind


# ---------------------------------------------------------------- numbers


def r(x, nd=4):
    """Round for compact, stable JSON (and never emit -0.0)."""
    try:
        v = round(float(x), nd)
    except (TypeError, ValueError):
        return x
    return 0.0 if v == 0 else v


def rv(seq, nd=4):
    return [r(x, nd) for x in seq]


def deg3(euler):
    return [r(math.degrees(a), 3) for a in euler]


# ---------------------------------------------------------------- JSON

_MAX_ITEMS = 1000
_MAX_DEPTH = 12


def json_default(o):
    """`default=` hook for json.dumps: turn Blender values into plain JSON."""
    return to_jsonable(o)


def to_jsonable(o, depth=0):
    if o is None or isinstance(o, (bool, int, str)):
        return o
    if isinstance(o, float):
        if math.isnan(o) or math.isinf(o):
            return str(o)
        return o
    if depth > _MAX_DEPTH:
        return repr(o)
    try:
        import mathutils

        if isinstance(o, mathutils.Matrix):
            return [rv(row, 6) for row in o]
        if isinstance(o, (mathutils.Vector, mathutils.Color, mathutils.Euler, mathutils.Quaternion)):
            return rv(o, 6)
    except ImportError:
        pass
    if isinstance(o, bpy.types.ID):
        return o.name
    if isinstance(o, dict):
        out = {}
        for i, (k, v) in enumerate(o.items()):
            if i >= _MAX_ITEMS:
                out["..."] = f"{len(o) - _MAX_ITEMS} more entries"
                break
            out[str(k)] = to_jsonable(v, depth + 1)
        return out
    if isinstance(o, (bytes, bytearray)):
        return bytes(o).decode("utf-8", "replace")
    if isinstance(o, (list, tuple, set, frozenset)) or type(o).__name__ in ("bpy_prop_array", "bpy_prop_collection"):
        items = []
        for i, v in enumerate(o):
            if i >= _MAX_ITEMS:
                items.append(f"... more items (showing {_MAX_ITEMS})")
                break
            items.append(to_jsonable(v, depth + 1))
        return items
    if isinstance(o, bpy.types.bpy_struct):
        name = getattr(o, "name", None)
        return name if isinstance(name, str) else repr(o)
    if hasattr(o, "tolist"):  # numpy arrays and scalars
        try:
            return to_jsonable(o.tolist(), depth + 1)
        except Exception:
            pass
    return repr(o)


# ---------------------------------------------------------------- paths


def resolve_path(p, must_exist=False, what="file"):
    """Absolute path for `p`: `//` is blend-relative, other relative paths use WORKDIR."""
    if not isinstance(p, str) or not p.strip():
        raise UserError(f"Expected a {what} path, got {p!r}")
    p = p.strip()
    if p.startswith("//"):
        path = bpy.path.abspath(p)
    else:
        path = os.path.expanduser(p)
        if not os.path.isabs(path):
            path = os.path.join(WORKDIR, path)
    path = os.path.normpath(path)
    if must_exist and not os.path.exists(path):
        raise UserError(f"{what.capitalize()} not found: {path}", kind="NotFound")
    return path


def ensure_parent_dir(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


# ---------------------------------------------------------------- lookups


def suggest(name, candidates, n=5):
    return difflib.get_close_matches(str(name), list(candidates), n=n, cutoff=0.4)


def get_object(name):
    obj = bpy.data.objects.get(name) if isinstance(name, str) else None
    if obj is None:
        names = [o.name for o in bpy.context.scene.objects]
        close = suggest(name, names)
        hint = ("Did you mean: " + ", ".join(close)) if close else (
            "Objects in the scene: " + ", ".join(names[:30]) if names else "The scene has no objects.")
        raise UserError(f"No object named {name!r}", hint=hint, kind="NotFound")
    return obj


def ensure_object_mode():
    obj = bpy.context.view_layer.objects.active
    if obj is not None and obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def select_only(objs, active=None):
    vl = bpy.context.view_layer
    for o in vl.objects:
        if o.select_get():
            o.select_set(False)
    for o in objs:
        try:
            o.select_set(True)
        except RuntimeError:
            pass
    vl.objects.active = active if active is not None else (objs[0] if objs else None)


def mesh_counts(me):
    tris = sum(len(p.vertices) - 2 for p in me.polygons)
    return {"verts": len(me.vertices), "edges": len(me.edges), "faces": len(me.polygons), "tris": tris}


def evaluated_mesh_counts(obj):
    """Vertex/face counts after modifiers."""
    deps = bpy.context.evaluated_depsgraph_get()
    eo = obj.evaluated_get(deps)
    try:
        me = eo.to_mesh()
    except RuntimeError:
        return None
    try:
        return mesh_counts(me) if me is not None else None
    finally:
        eo.to_mesh_clear()


# ---------------------------------------------------------------- RNA assignment

_ID_COLLECTIONS = {
    "Object": "objects", "Collection": "collections", "Material": "materials", "Image": "images",
    "Texture": "textures", "NodeTree": "node_groups", "Mesh": "meshes", "Curve": "curves",
    "World": "worlds", "Camera": "cameras", "Light": "lights", "Action": "actions",
    "VectorFont": "fonts", "Text": "texts", "Scene": "scenes",
}


def describe_prop(prop):
    parts = [f"{prop.identifier}: {prop.type}"]
    if getattr(prop, "array_length", 0):
        parts.append(f"array[{prop.array_length}]")
    if prop.type == "ENUM":
        items = [i.identifier for i in prop.enum_items]
        if items:
            parts.append("one of " + ", ".join(items))
    elif prop.type in ("INT", "FLOAT"):
        parts.append(f"range {r(prop.hard_min)}..{r(prop.hard_max)}")
    elif prop.type == "POINTER":
        parts.append(f"name of a {prop.fixed_type.identifier}")
    if prop.description:
        parts.append(f"({prop.description})")
    return " ".join(parts)


def _coerce(prop, value, key, what):
    t = prop.type
    if t == "POINTER":
        if value is None:
            return None
        fixed = prop.fixed_type.identifier
        coll_name = None
        for base, coll in _ID_COLLECTIONS.items():
            if fixed == base or _is_subclass_rna(prop.fixed_type, base):
                coll_name = coll
                break
        if coll_name and isinstance(value, str):
            coll = getattr(bpy.data, coll_name)
            found = coll.get(value)
            if found is None:
                close = suggest(value, coll.keys())
                raise UserError(f"{what}.{key}: no {fixed} named {value!r}",
                                hint=("Did you mean: " + ", ".join(close)) if close else None, kind="NotFound")
            return found
        return value
    if t == "ENUM":
        items = [i.identifier for i in prop.enum_items]
        if prop.is_enum_flag:
            vals = value if isinstance(value, (list, tuple, set)) else [value]
            return {_match_enum(v, items, key, what) for v in vals}
        return _match_enum(value, items, key, what)
    length = getattr(prop, "array_length", 0)
    if length and isinstance(value, (list, tuple)):
        if len(value) != length:
            raise UserError(f"{what}.{key} needs {length} values, got {len(value)}", hint=describe_prop(prop))
        conv = {"BOOLEAN": bool, "INT": int, "FLOAT": float}.get(t, lambda x: x)
        return [conv(v) for v in value]
    if t == "BOOLEAN":
        return bool(value)
    if t == "INT":
        return int(value)
    if t == "FLOAT":
        return float(value)
    if t == "STRING":
        return str(value)
    return value


def _is_subclass_rna(struct, base_name):
    s = struct
    while s is not None:
        if s.identifier == base_name:
            return True
        s = s.base
    return False


def _match_enum(value, items, key, what):
    if not items:  # dynamic enum: let Blender validate
        return value
    if isinstance(value, str):
        for it in items:
            if it.upper() == value.upper():
                return it
    raise UserError(f"{what}.{key} must be one of: {', '.join(items)} (got {value!r})")


def set_rna_props(struct, params, what):
    """Assign `params` to an RNA struct with helpful errors for unknown names and bad values."""
    props = struct.bl_rna.properties
    for key, value in (params or {}).items():
        if key == "rna_type" or key not in props:
            valid = sorted(p.identifier for p in props if p.identifier != "rna_type" and not p.is_readonly)
            close = suggest(key, valid)
            hint = ("Did you mean: " + ", ".join(close) + ". ") if close else ""
            raise UserError(f"Unknown parameter {key!r} for {what}", hint=hint + "Valid parameters: " + ", ".join(valid))
        prop = props[key]
        if prop.is_readonly:
            raise UserError(f"{what}.{key} is read-only")
        coerced = _coerce(prop, value, key, what)
        try:
            setattr(struct, key, coerced)
        except (TypeError, ValueError, AttributeError) as e:
            raise UserError(f"Cannot set {what}.{key} = {value!r}: {e}", hint=describe_prop(prop))


def operator_props(op):
    """RNA properties accepted by a bpy.ops operator, excluding internals."""
    return {p.identifier: p for p in op.get_rna_type().properties if p.identifier != "rna_type"}


def check_operator_options(op, options, what):
    props = operator_props(op)
    for key in options or {}:
        if key not in props:
            close = suggest(key, props)
            hint = ("Did you mean: " + ", ".join(close) + ". ") if close else ""
            raise UserError(f"Unknown option {key!r} for {what}", hint=hint + "Valid options: " + ", ".join(sorted(props)))
