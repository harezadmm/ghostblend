"""validate: check meshes for common problems before export or printing."""
import os

import bmesh
import bpy

from . import registry, util

ALL_CHECKS = [
    "non_manifold", "boundary_edges", "loose_vertices", "duplicate_vertices", "ngons",
    "zero_area_faces", "inconsistent_normals", "unapplied_scale", "negative_scale",
    "unapplied_rotation", "missing_uvs", "missing_textures", "far_from_origin",
]

_FAR = 1000.0
_EPS_AREA = 1e-9
_EPS_DOUBLE = 1e-5


def _mesh_checks(obj, checks):
    findings = {}
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        if "non_manifold" in checks:
            n = sum(1 for e in bm.edges if not e.is_manifold and not e.is_boundary)
            if n:
                findings["non_manifold"] = n
        if "boundary_edges" in checks:
            n = sum(1 for e in bm.edges if e.is_boundary)
            if n:
                findings["boundary_edges"] = n
        if "loose_vertices" in checks:
            n = sum(1 for v in bm.verts if not v.link_edges)
            if n:
                findings["loose_vertices"] = n
        if "duplicate_vertices" in checks and bm.verts:
            res = bmesh.ops.find_doubles(bm, verts=bm.verts, dist=_EPS_DOUBLE)
            n = len(res.get("targetmap", {}))
            if n:
                findings["duplicate_vertices"] = n
        if "ngons" in checks:
            n = sum(1 for f in bm.faces if len(f.verts) > 4)
            if n:
                findings["ngons"] = n
        if "zero_area_faces" in checks:
            n = sum(1 for f in bm.faces if f.calc_area() < _EPS_AREA)
            if n:
                findings["zero_area_faces"] = n
        if "inconsistent_normals" in checks and bm.faces:
            before = [f.normal.copy() for f in bm.faces]
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
            flipped = sum(1 for f, nrm in zip(bm.faces, before) if f.normal.dot(nrm) < 0)
            if flipped:
                findings["inconsistent_normals"] = flipped
    finally:
        bm.free()
    return findings


def _object_checks(obj, checks):
    findings = {}
    if "unapplied_scale" in checks and any(abs(s - 1.0) > 1e-4 for s in obj.scale):
        findings["unapplied_scale"] = [round(s, 4) for s in obj.scale]
    if "negative_scale" in checks and (obj.scale.x * obj.scale.y * obj.scale.z) < 0:
        findings["negative_scale"] = [round(s, 4) for s in obj.scale]
    if "unapplied_rotation" in checks and any(abs(r) > 1e-4 for r in obj.rotation_euler):
        findings["unapplied_rotation"] = True
    if "far_from_origin" in checks and obj.location.length > _FAR:
        findings["far_from_origin"] = round(obj.location.length, 2)
    if obj.type == "MESH":
        if "missing_uvs" in checks and len(obj.data.uv_layers) == 0:
            findings["missing_uvs"] = True
    if "missing_textures" in checks:
        missing = _missing_textures(obj)
        if missing:
            findings["missing_textures"] = missing
    return findings


def _missing_textures(obj):
    missing = []
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or mat.node_tree is None:
            continue
        for node in mat.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image and node.image.source == "FILE":
                if node.image.packed_file:
                    continue
                path = bpy.path.abspath(node.image.filepath)
                if node.image.filepath and not os.path.exists(path):
                    missing.append({"material": mat.name, "path": path})
    return missing


@registry.command("validate")
def validate(args):
    requested = args.get("checks")
    if requested:
        unknown = [c for c in requested if c not in ALL_CHECKS]
        if unknown:
            raise util.UserError(f"Unknown check(s): {', '.join(unknown)}",
                                 hint="Available checks: " + ", ".join(ALL_CHECKS))
        checks = set(requested)
    else:
        checks = set(ALL_CHECKS)

    if args.get("objects"):
        objs = [util.get_object(n) for n in args["objects"]]
    else:
        objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        if not objs:
            objs = list(bpy.context.scene.objects)

    reports = []
    all_ok = True
    for obj in objs:
        findings = {}
        if obj.type == "MESH":
            findings.update(_mesh_checks(obj, checks))
        findings.update(_object_checks(obj, checks))
        ok = not findings
        all_ok = all_ok and ok
        reports.append({"object": obj.name, "type": obj.type, "ok": ok, "findings": findings})

    problems = [r for r in reports if not r["ok"]]
    return {
        "ok": all_ok,
        "checked": len(reports),
        "objects_with_problems": len(problems),
        "reports": reports,
        "checks_run": sorted(checks),
    }
