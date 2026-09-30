"""render_preview: quick multi-view images returned to the agent inline.

Renders each requested view with a temporary camera, composes them into a
labelled contact sheet, and restores every setting it touched. Never mutates
the saved scene.
"""
import os
import time

import bpy
import numpy as np

from . import font, framing, history, registry, scene as scene_mod, util

_GRID = {1: (1, 1), 2: (2, 1), 3: (2, 2), 4: (2, 2)}
_GUTTER = 6
_LABEL_BAR = 22


def _engine_for(shading, engine):
    if engine in ("eevee", "rendered_eevee"):
        return "BLENDER_EEVEE"
    if engine == "cycles":
        return "CYCLES"
    if shading == "rendered":
        return "BLENDER_EEVEE"
    return "BLENDER_WORKBENCH"


def _snapshot():
    sc = bpy.context.scene
    r = sc.render
    s = {
        "engine": r.engine, "rx": r.resolution_x, "ry": r.resolution_y, "pct": r.resolution_percentage,
        "film": r.film_transparent, "filepath": r.filepath, "fmt": r.image_settings.file_format,
        "color_mode": r.image_settings.color_mode, "camera": sc.camera,
    }
    return s


def _restore(s):
    sc = bpy.context.scene
    r = sc.render
    r.engine = s["engine"]
    r.resolution_x, r.resolution_y, r.resolution_percentage = s["rx"], s["ry"], s["pct"]
    r.film_transparent = s["film"]
    r.filepath = s["filepath"]
    r.image_settings.file_format = s["fmt"]
    r.image_settings.color_mode = s["color_mode"]
    sc.camera = s["camera"]


def _configure_workbench(shading, wireframe):
    sh = bpy.context.scene.display.shading
    sh.light = "STUDIO"
    sh.color_type = "MATERIAL" if shading in ("textured", "rendered") else "OBJECT"
    sh.show_object_outline = False
    for attr in ("show_wireframes",):
        if hasattr(sh, attr):
            setattr(sh, attr, bool(wireframe))


def _load_tile(path, size):
    img = bpy.data.images.load(path, check_existing=False)
    try:
        arr = np.array(img.pixels[:], dtype=np.float32)
        w, h = img.size
        arr = arr.reshape(h, w, 4)[::-1]  # Blender stores bottom-up; flip to top-down
    finally:
        bpy.data.images.remove(img)
    return arr


def _compose(tiles, labels, size, transparent):
    n = len(tiles)
    cols, rows = _GRID.get(n, (2, (n + 1) // 2))
    cell_w = size + _GUTTER
    cell_h = size + _LABEL_BAR + _GUTTER
    cw = cols * cell_w + _GUTTER
    ch = rows * cell_h + _GUTTER
    bg = 0.0 if transparent else 0.12
    canvas = np.full((ch, cw, 4), (bg, bg, bg, 0.0 if transparent else 1.0), dtype=np.float32)
    flat = canvas.reshape(-1)
    for i, (tile, label) in enumerate(zip(tiles, labels)):
        r, c = divmod(i, cols)
        x0 = _GUTTER + c * cell_w
        y0 = _GUTTER + r * cell_h
        canvas[y0 + _LABEL_BAR:y0 + _LABEL_BAR + size, x0:x0 + size] = tile
        font.draw_text(flat, cw, ch, label, x0 + 4, y0 + 4, scale=2, color=[0.9, 0.95, 1.0, 1.0])
    return canvas


def _save_canvas(canvas, path):
    ch, cw, _ = canvas.shape
    out = bpy.data.images.new("gb_preview", cw, ch, alpha=True)
    try:
        out.pixels = canvas[::-1].reshape(-1).tolist()  # back to bottom-up for Blender
        out.file_format = "PNG"
        out.filepath_raw = path
        out.save()
    finally:
        bpy.data.images.remove(out)


# Object types that draw geometry in a render; cameras, lights and empties do not.
_GEOMETRY = {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME",
             "GREASEPENCIL", "GPENCIL"}


def _rendered_geometry(state):
    """Objects the preview will actually draw: render visibility, not viewport visibility."""
    return [o for o in bpy.context.scene.objects
            if o.type in _GEOMETRY and scene_mod.object_layer(o, state)["renders"]]


@registry.command("render_preview")
def render_preview(args):
    t0 = time.perf_counter()
    sc = bpy.context.scene
    size = int(args.get("size", 512))
    views = args.get("views") or ["front", "right", "top", "persp"]
    shading = args.get("shading", "solid")
    engine = args.get("engine", "auto")
    transparent = bool(args.get("transparent", False))
    wireframe = bool(args.get("wireframe", False))

    state = scene_mod.layer_state()
    notes = []
    if args.get("objects"):
        targets = [util.get_object(n) for n in args["objects"]]
        unseen = [o.name for o in targets if not scene_mod.object_layer(o, state)["renders"]]
        if unseen:
            notes.append(f"Hidden in renders, so not in the image: {', '.join(unseen)}. "
                         "scene_info shows why.")
    else:
        targets = _rendered_geometry(state)
    mins, maxs, had = framing.scene_bbox(targets)

    snap = _snapshot()
    preview_dir = os.path.join(history.S["dir"], "previews")
    os.makedirs(preview_dir, exist_ok=True)

    temp_objs = []
    temp_light = None
    used_engine = _engine_for(shading, engine)
    try:
        sc.render.engine = used_engine
        sc.render.resolution_x = size
        sc.render.resolution_y = size
        sc.render.resolution_percentage = 100
        sc.render.film_transparent = transparent
        sc.render.image_settings.file_format = "PNG"
        sc.render.image_settings.color_mode = "RGBA"
        if sc.render.engine == "BLENDER_WORKBENCH":
            _configure_workbench(shading, wireframe)
        elif shading == "rendered" and not any(o.type == "LIGHT" for o in sc.objects):
            light_data = bpy.data.lights.new("gb_preview_sun", "SUN")
            light_data.energy = 3.0
            temp_light = bpy.data.objects.new("gb_preview_sun", light_data)
            temp_light.rotation_euler = (0.9, 0.1, 0.6)
            sc.collection.objects.link(temp_light)

        tiles, labels = [], []
        for view in views:
            cam_data = bpy.data.cameras.new(f"gb_cam_{view}")
            cam_obj = bpy.data.objects.new(f"gb_cam_{view}", cam_data)
            sc.collection.objects.link(cam_obj)
            temp_objs.append(cam_obj)
            if view == "camera":
                if snap["camera"] is None:
                    notes.append("No scene camera; used a perspective view instead.")
                    framing.place_camera(cam_obj, "persp", mins, maxs)
                else:
                    sc.camera = snap["camera"]
                    cam_obj = snap["camera"]
            else:
                framing.place_camera(cam_obj, view, mins, maxs)
            sc.camera = cam_obj
            path = os.path.join(preview_dir, f"view_{view}_{int(time.time()*1000)}.png")
            sc.render.filepath = path
            bpy.ops.render.render(write_still=True)
            tiles.append(_load_tile(path, size))
            labels.append(view.upper())

        if len(tiles) == 1:
            out_path = os.path.join(preview_dir, f"preview_{int(time.time()*1000)}.png")
            _save_canvas(_compose(tiles, labels, size, transparent), out_path)
        else:
            out_path = os.path.join(preview_dir, f"preview_{int(time.time()*1000)}.png")
            _save_canvas(_compose(tiles, labels, size, transparent), out_path)
    finally:
        for o in temp_objs:
            if o.name.startswith("gb_cam_"):
                data = o.data
                bpy.data.objects.remove(o, do_unlink=True)
                if data and data.users == 0:
                    bpy.data.cameras.remove(data)
        if temp_light is not None:
            d = temp_light.data
            bpy.data.objects.remove(temp_light, do_unlink=True)
            bpy.data.lights.remove(d)
        _restore(snap)

    if not had:
        why = scene_mod.excluded_note(state, list(sc.objects))
        notes.append("The scene has no visible objects to frame; showing an empty view."
                     + (" " + why if why else ""))
    result = {
        "image": out_path,
        "views": views,
        "size": size,
        "engine": used_engine,
        "bbox": {"min": [round(v, 3) for v in mins], "max": [round(v, 3) for v in maxs]},
        "ms": int((time.perf_counter() - t0) * 1000),
    }
    if notes:
        result["notes"] = notes
    return result
