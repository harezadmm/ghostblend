"""render_prepare and sys_info.

render_prepare snapshots the current scene into a job directory so the render
runs against exactly what the agent has built, in a separate Blender process.
"""
import os
import sys

import bpy

from . import history, registry, util


@registry.command("render_prepare")
def render_prepare(args):
    job_id = str(args.get("job_id") or "job")
    job_dir = os.path.join(history.S["dir"], "renders", job_id)
    os.makedirs(job_dir, exist_ok=True)
    scene_path = os.path.join(job_dir, "scene.blend")
    history.save_copy(scene_path)
    return {"scene": scene_path, "job_dir": job_dir,
            "has_camera": bpy.context.scene.camera is not None,
            "objects": len(bpy.context.scene.objects)}


def _available_engines(sc):
    """The static engine enum is incomplete under --factory-startup, so probe."""
    current = sc.render.engine
    found = []
    for eng in ("BLENDER_WORKBENCH", "BLENDER_EEVEE", "CYCLES"):
        try:
            sc.render.engine = eng
            found.append(eng)
        except (TypeError, ValueError):
            pass
    sc.render.engine = current
    return found


@registry.command("sys_info")
def sys_info(args):
    sc = bpy.context.scene
    engines = _available_engines(sc)
    devices = []
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.get_devices()
        devices = [{"name": d.name, "type": d.type} for d in prefs.devices]
    except Exception as e:  # Cycles add-on not present or no GPU
        devices = [{"error": str(e)}]
    return {
        "blender": bpy.app.version_string,
        "python": sys.version.split()[0],
        "engines": engines,
        "cycles_devices": devices,
        "resolution": [sc.render.resolution_x, sc.render.resolution_y],
    }
