"""api_describe and api_search: introspect Blender's bpy operators and types from RNA."""
import bpy

from . import registry, util
from .util import describe_prop

_SKIP_PROPS = {"rna_type"}


@registry.command("api_describe")
def api_describe(args):
    path = (args.get("path") or "").strip()
    if path.startswith("bpy.ops."):
        return _describe_op(path)
    if path.startswith("bpy.types."):
        return _describe_type(path)
    # Bare names: try as an operator first, then a type.
    for prefix in ("bpy.ops.", "bpy.types."):
        try:
            return api_describe({"path": prefix + path})
        except util.UserError:
            continue
    raise util.UserError(f"Cannot resolve {path!r}",
                         hint="Use a path like bpy.ops.mesh.primitive_cube_add or bpy.types.BevelModifier")


def _describe_op(path):
    parts = path[len("bpy.ops."):].split(".")
    op = bpy.ops
    for p in parts:
        op = getattr(op, p, None)
        if op is None:
            raise util.UserError(f"No operator {path!r}", kind="NotFound",
                                 hint="Try api_search to find operators")
    try:
        rna = op.get_rna_type()
    except Exception as e:
        raise util.UserError(f"{path!r} is not a callable operator: {e}", kind="NotFound")
    props = []
    for p in rna.properties:
        if p.identifier in _SKIP_PROPS:
            continue
        props.append({"name": p.identifier, "spec": describe_prop(p),
                      "required": not p.is_skip_save and not p.is_never_none if hasattr(p, "is_skip_save") else False})
    return {"kind": "operator", "path": path, "description": rna.description or "",
            "parameters": props}


def _describe_type(path):
    name = path[len("bpy.types."):]
    rna_type = getattr(bpy.types, name, None)
    if rna_type is None:
        raise util.UserError(f"No type bpy.types.{name}", kind="NotFound",
                             hint="Try api_search with kind='types'")
    props, readonly = [], []
    for p in rna_type.bl_rna.properties:
        if p.identifier in _SKIP_PROPS:
            continue
        entry = {"name": p.identifier, "spec": describe_prop(p)}
        (readonly if p.is_readonly else props).append(entry)
    return {"kind": "type", "path": path,
            "description": rna_type.bl_rna.description or "",
            "settable": props, "read_only": [r["name"] for r in readonly]}


@registry.command("api_search")
def api_search(args):
    query = (args.get("query") or "").strip().lower()
    if not query:
        raise util.UserError("Provide a search query, e.g. 'bevel'")
    kind = args.get("kind", "both")
    limit = int(args.get("limit", 30))

    ops_hits, type_hits = [], []
    if kind in ("ops", "both"):
        ops_hits = _search_ops(query, limit)
    if kind in ("types", "both"):
        type_hits = _search_types(query, limit)
    return {"query": query, "operators": ops_hits, "types": type_hits}


def _search_ops(query, limit):
    hits = []
    for category in dir(bpy.ops):
        if category.startswith("_"):
            continue
        cat = getattr(bpy.ops, category)
        try:
            names = dir(cat)
        except Exception:
            continue
        for name in names:
            if name.startswith("_"):
                continue
            full = f"bpy.ops.{category}.{name}"
            if query in name.lower() or query in full.lower():
                hits.append(full)
                if len(hits) >= limit:
                    return hits
    return hits


def _search_types(query, limit):
    hits = []
    for name in dir(bpy.types):
        if name.startswith("_"):
            continue
        if query in name.lower():
            hits.append(f"bpy.types.{name}")
            if len(hits) >= limit:
                break
    return hits
