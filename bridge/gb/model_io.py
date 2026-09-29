"""import_model and export_model across glTF, FBX, OBJ, STL, PLY, USD and Alembic.

Because the worker runs with --factory-startup, the glTF and FBX add-ons start
disabled; we enable them on demand. OBJ, STL, PLY, USD and Alembic are built-in
C operators and need no add-on.
"""
import os

import addon_utils
import bpy

from . import registry, util

_EXT_FORMAT = {
    ".glb": "gltf", ".gltf": "gltf", ".fbx": "fbx", ".obj": "obj", ".stl": "stl",
    ".ply": "ply", ".usd": "usd", ".usda": "usd", ".usdc": "usd", ".usdz": "usd",
    ".abc": "abc",
}

_ADDON_FOR = {"gltf": "io_scene_gltf2", "fbx": "io_scene_fbx"}


def _detect_format(path, given):
    if given and given != "auto":
        return given
    ext = os.path.splitext(path)[1].lower()
    fmt = _EXT_FORMAT.get(ext)
    if fmt is None:
        raise util.UserError(f"Cannot tell the format of {path!r}",
                             hint="Use a known extension (.glb, .gltf, .fbx, .obj, .stl, .ply, .usd, .abc) or pass format")
    return fmt


def _ensure_addon(fmt):
    module = _ADDON_FOR.get(fmt)
    if module is None:
        return
    if not addon_utils.check(module)[1]:
        try:
            addon_utils.enable(module, default_set=False, persistent=True)
        except Exception as e:
            raise util.UserError(f"Could not enable the {fmt} add-on ({module}): {e}")


@registry.command("import_model")
def import_model(args):
    path = util.resolve_path(args["path"], must_exist=True, what="model")
    fmt = _detect_format(path, args.get("format"))
    _ensure_addon(fmt)
    options = args.get("options") or {}
    util.ensure_object_mode()
    before = set(bpy.context.scene.objects)

    if fmt == "gltf":
        util.check_operator_options(bpy.ops.import_scene.gltf, options, "gltf import")
        bpy.ops.import_scene.gltf(filepath=path, **options)
    elif fmt == "fbx":
        util.check_operator_options(bpy.ops.import_scene.fbx, options, "fbx import")
        bpy.ops.import_scene.fbx(filepath=path, **options)
    elif fmt == "obj":
        bpy.ops.wm.obj_import(filepath=path, **options)
    elif fmt == "stl":
        _import_stl(path, options)
    elif fmt == "ply":
        _import_ply(path, options)
    elif fmt == "usd":
        bpy.ops.wm.usd_import(filepath=path, **options)
    elif fmt == "abc":
        bpy.ops.wm.alembic_import(filepath=path, **options)

    new = [o for o in bpy.context.scene.objects if o not in before]
    total_verts = sum(len(o.data.vertices) for o in new if o.type == "MESH" and o.data)
    return {"format": fmt, "imported": [o.name for o in new], "count": len(new),
            "total_verts": total_verts}


def _import_stl(path, options):
    if hasattr(bpy.ops.wm, "stl_import"):
        bpy.ops.wm.stl_import(filepath=path, **options)
    else:  # older builds ship the Python add-on
        addon_utils.enable("io_mesh_stl", default_set=False)
        bpy.ops.import_mesh.stl(filepath=path, **options)


def _import_ply(path, options):
    if hasattr(bpy.ops.wm, "ply_import"):
        bpy.ops.wm.ply_import(filepath=path, **options)
    else:
        addon_utils.enable("io_mesh_ply", default_set=False)
        bpy.ops.import_mesh.ply(filepath=path, **options)


@registry.command("export_model")
def export_model(args):
    path = util.resolve_path(args["path"])
    fmt = _detect_format(path, args.get("format"))
    _ensure_addon(fmt)
    util.ensure_parent_dir(path)
    apply_mods = bool(args.get("apply_modifiers", True))
    options = args.get("options") or {}

    selection = args.get("selection")
    util.ensure_object_mode()
    if selection:
        objs = [util.get_object(n) for n in selection]
        util.select_only(objs, active=objs[0] if objs else None)
        use_selection = True
    else:
        objs = list(bpy.context.scene.objects)
        use_selection = False

    if fmt == "gltf":
        bpy.ops.export_scene.gltf(filepath=path, use_selection=use_selection,
                                  export_apply=apply_mods, **options)
    elif fmt == "fbx":
        bpy.ops.export_scene.fbx(filepath=path, use_selection=use_selection,
                                 use_mesh_modifiers=apply_mods, **options)
    elif fmt == "obj":
        bpy.ops.wm.obj_export(filepath=path, export_selected_objects=use_selection,
                              apply_modifiers=apply_mods, **options)
    elif fmt == "stl":
        _export_stl(path, use_selection, apply_mods, options)
    elif fmt == "ply":
        _export_ply(path, use_selection, apply_mods, options)
    elif fmt == "usd":
        bpy.ops.wm.usd_export(filepath=path, selected_objects_only=use_selection, **options)
    elif fmt == "abc":
        bpy.ops.wm.alembic_export(filepath=path, selected=use_selection, **options)

    size = os.path.getsize(path) if os.path.isfile(path) else None
    return {"format": fmt, "path": path, "bytes": size, "objects": len(objs)}


def _export_stl(path, use_selection, apply_mods, options):
    if hasattr(bpy.ops.wm, "stl_export"):
        bpy.ops.wm.stl_export(filepath=path, export_selected_objects=use_selection,
                              apply_modifiers=apply_mods, **options)
    else:
        addon_utils.enable("io_mesh_stl", default_set=False)
        bpy.ops.export_mesh.stl(filepath=path, use_selection=use_selection, **options)


def _export_ply(path, use_selection, apply_mods, options):
    if hasattr(bpy.ops.wm, "ply_export"):
        bpy.ops.wm.ply_export(filepath=path, export_selected_objects=use_selection,
                              apply_modifiers=apply_mods, **options)
    else:
        addon_utils.enable("io_mesh_ply", default_set=False)
        bpy.ops.export_mesh.ply(filepath=path, use_selection=use_selection, **options)
