"""Bounding-box and camera framing math for preview rendering."""
import math

import bpy
from mathutils import Vector

# Direction each named view looks along, and the up axis string for to_track_quat.
_VIEWS = {
    "front":  (Vector((0, 1, 0)),  "Y"),
    "back":   (Vector((0, -1, 0)), "Y"),
    "right":  (Vector((-1, 0, 0)), "Y"),
    "left":   (Vector((1, 0, 0)),  "Y"),
    "top":    (Vector((0, 0, -1)), "Y"),
    "bottom": (Vector((0, 0, 1)),  "Y"),
}

AXIS_VIEWS = tuple(_VIEWS.keys())


def scene_bbox(objects):
    """World-space (min, max) over the given objects' bounding boxes."""
    mins, maxs = None, None
    for o in objects:
        if o.type in ("CAMERA", "LIGHT", "EMPTY"):
            continue
        mw = o.matrix_world
        for corner in o.bound_box:
            p = mw @ Vector(corner)
            if mins is None:
                mins, maxs = p.copy(), p.copy()
            else:
                mins = Vector((min(mins.x, p.x), min(mins.y, p.y), min(mins.z, p.z)))
                maxs = Vector((max(maxs.x, p.x), max(maxs.y, p.y), max(maxs.z, p.z)))
    if mins is None:
        return Vector((-1, -1, -1)), Vector((1, 1, 1)), False
    return mins, maxs, True


def _ortho_extent(size, look):
    """Half-extent visible for an orthographic view along `look`."""
    ax = look.normalized()
    # The two axes perpendicular to the view direction bound the image.
    if abs(ax.y) > 0.5:      # front/back: XZ plane
        return max(size.x, size.z)
    if abs(ax.x) > 0.5:      # left/right: YZ plane
        return max(size.y, size.z)
    return max(size.x, size.y)  # top/bottom: XY plane


def place_camera(cam_obj, view, mins, maxs):
    """Aim a camera object at the bounding box for the named view."""
    center = (mins + maxs) / 2
    size = maxs - mins
    radius = max(size.length / 2, 1e-3)
    cam = cam_obj.data

    if view == "persp":
        # Look from front-right-above (+X, -Y, +Z of the subject), like Blender's
        # default camera. `look` is the viewing direction, so it points the other way.
        look = Vector((-1, 1, -0.6)).normalized()
        cam.type = "PERSP"
        cam.lens = 50
        fov = 2 * math.atan(cam.sensor_width / (2 * cam.lens))
        dist = radius / math.sin(fov / 2) * 1.15
        cam_obj.location = center - look * dist
    else:
        look, _up = _VIEWS[view]
        cam.type = "ORTHO"
        cam.ortho_scale = _ortho_extent(size, look) * 1.12
        dist = radius * 3 + 1
        cam_obj.location = center - look * dist

    direction = center - cam_obj.location
    # The camera's local -Z looks forward and local Y is the top of the frame.
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    cam.clip_start = max(dist * 0.01, 0.001)
    cam.clip_end = dist * 4 + radius * 4 + 10
