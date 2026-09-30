"""Ghostblend render-job entry point.

Launched as:
    blender -b <scene.blend> --python render_job.py -- <json args>

Applies render overrides, ensures a camera and (for lit engines) a light, renders
a still or an animation, and prints a final `@@gbjob:` result line. A watchdog
thread exits the process the moment ghostblend closes our stdin, so a render can
never outlive the server.
"""
import json
import math
import os
import sys
import threading

import bpy

MARKER = "@@gbjob:"


def _emit(obj):
    sys.stderr.flush()
    sys.__stdout__.write("\n" + MARKER + json.dumps(obj) + "\n")
    sys.__stdout__.flush()


def _watchdog():
    try:
        while sys.stdin.buffer.readline():
            pass
    except Exception:
        pass
    os._exit(0)


def _ensure_camera(scene):
    if scene.camera is not None:
        return
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    from gb import framing  # noqa: E402
    from mathutils import Vector  # noqa: E402

    cam_data = bpy.data.cameras.new("gb_render_cam")
    cam = bpy.data.objects.new("gb_render_cam", cam_data)
    scene.collection.objects.link(cam)
    meshes = [o for o in scene.objects if o.type == "MESH"]
    mins, maxs, _ = framing.scene_bbox(meshes or list(scene.objects))
    framing.place_camera(cam, "persp", Vector(mins), Vector(maxs))
    scene.camera = cam


def _ensure_light(scene):
    if any(o.type == "LIGHT" for o in scene.objects):
        return
    data = bpy.data.lights.new("gb_render_sun", "SUN")
    data.energy = 3.0
    sun = bpy.data.objects.new("gb_render_sun", data)
    sun.rotation_euler = (0.9, 0.1, 0.6)
    scene.collection.objects.link(sun)


_ENGINES = {"workbench": "BLENDER_WORKBENCH", "eevee": "BLENDER_EEVEE", "cycles": "CYCLES"}
_EXT_FORMAT = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".exr": "exr", ".mp4": "mp4", ".mkv": "mp4"}


def _format_for(a):
    fmt = a.get("format")
    if fmt:
        return fmt
    return _EXT_FORMAT.get(os.path.splitext(a.get("output_path", ""))[1].lower(), "png")


def _set_format(scene, fmt, transparent):
    """Blender 5 chooses image, multilayer or video with media_type before file_format."""
    isf = scene.render.image_settings
    media = {"mp4": "VIDEO", "exr_multilayer": "MULTI_LAYER_IMAGE"}.get(fmt, "IMAGE")
    if hasattr(isf, "media_type"):
        isf.media_type = media
    if fmt == "mp4":
        isf.file_format = "FFMPEG"
        scene.render.ffmpeg.format = "MPEG4"
        scene.render.ffmpeg.codec = "H264"
        scene.render.ffmpeg.constant_rate_factor = "HIGH"
    elif fmt == "exr_multilayer":
        isf.file_format = "OPEN_EXR_MULTILAYER"
    elif fmt == "exr":
        isf.file_format = "OPEN_EXR"
    elif fmt == "jpeg":
        isf.file_format = "JPEG"
        isf.quality = 92
    else:
        isf.file_format = "PNG"
        isf.color_mode = "RGBA" if transparent else "RGB"


def _apply(scene, a):
    engine = a.get("engine", "auto")
    if engine in _ENGINES:
        scene.render.engine = _ENGINES[engine]
    if a.get("resolution"):
        scene.render.resolution_x, scene.render.resolution_y = int(a["resolution"][0]), int(a["resolution"][1])
    if a.get("percentage"):
        scene.render.resolution_percentage = int(a["percentage"])
    if a.get("transparent"):
        scene.render.film_transparent = True
    samples = a.get("samples")
    if samples:
        if scene.render.engine == "CYCLES":
            scene.cycles.samples = int(samples)
        elif scene.render.engine.startswith("BLENDER_EEVEE"):
            scene.eevee.taa_render_samples = int(samples)
    if scene.render.engine == "CYCLES":
        _setup_cycles_device(scene, a.get("device", "gpu"))
    if scene.render.engine != "BLENDER_WORKBENCH":
        _ensure_light(scene)
    _ensure_camera(scene)


def _setup_cycles_device(scene, device):
    if device == "cpu":
        scene.cycles.device = "CPU"
        return
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
            try:
                prefs.compute_device_type = backend
            except TypeError:
                continue
            prefs.get_devices()
            gpus = [d for d in prefs.devices if d.type == backend]
            if gpus:
                for d in prefs.devices:
                    d.use = d.type in (backend, "CPU")
                scene.cycles.device = "GPU"
                return
    except Exception:
        pass
    scene.cycles.device = "CPU"


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = json.loads(argv[0]) if argv else {}
    threading.Thread(target=_watchdog, daemon=True).start()

    scene = bpy.context.scene
    try:
        _apply(scene, args)
    except Exception as e:
        import traceback
        _emit({"ok": False, "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()[-2000:]})
        os._exit(0)

    out = args["output_path"]
    outputs = []
    fmt = _format_for(args)
    animation = args.get("frame_start") is not None and args.get("frame_end") is not None
    try:
        if fmt == "mp4" and not animation:
            raise ValueError("an mp4 video needs frame_start and frame_end")
        _set_format(scene, fmt, bool(args.get("transparent")))
        if animation:
            scene.frame_start = int(args["frame_start"])
            scene.frame_end = int(args["frame_end"])
            folder = out if (out.endswith(("/", "\\")) or os.path.isdir(out)) else (os.path.dirname(out) or ".")
            os.makedirs(folder, exist_ok=True)
            before = set(os.listdir(folder))
            scene.render.filepath = out
            bpy.ops.render.render(animation=True, write_still=True)
            files = sorted(os.path.join(folder, f) for f in set(os.listdir(folder)) - before)
            outputs.append({"animation": files[-1] if files else out, "files": files,
                            "frames": [scene.frame_start, scene.frame_end], "format": fmt})
        else:
            frame = int(args.get("frame", scene.frame_current))
            scene.frame_set(frame)
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            scene.render.filepath = out
            bpy.ops.render.render(write_still=True)
            # Blender may append the extension; report the real file.
            real = out if os.path.isfile(out) else scene.render.frame_path(frame=frame)
            outputs.append({"still": real, "frame": frame, "format": fmt})
        _emit({"ok": True, "engine": scene.render.engine, "outputs": outputs,
               "resolution": [scene.render.resolution_x, scene.render.resolution_y]})
    except Exception as e:
        import traceback
        _emit({"ok": False, "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()[-2000:]})
    os._exit(0)


main()
