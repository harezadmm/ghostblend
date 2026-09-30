"""world_set and light_set: environment lighting and lamps."""
import math
import os

import bpy
from mathutils import Vector

from . import registry, scene, util
from .util import r, rv

_LIGHT_TYPES = ("POINT", "SUN", "SPOT", "AREA")
_DEFAULT_ENERGY = {"POINT": 1000.0, "SPOT": 1000.0, "AREA": 500.0, "SUN": 3.0}


def bundled_hdris():
    """The HDRIs that ship with Blender (datafiles/studiolights/world), by name."""
    folder = bpy.utils.system_resource("DATAFILES", path="studiolights/world")
    if not folder or not os.path.isdir(folder):
        return {}
    return {os.path.splitext(f)[0]: os.path.join(folder, f) for f in sorted(os.listdir(folder))
            if f.lower().endswith((".exr", ".hdr"))}


def _world_nodes(world):
    if getattr(world, "use_nodes", True) is False:
        try:
            world.use_nodes = True
        except (AttributeError, TypeError):
            pass
    nt = world.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_WORLD"), None) or nt.nodes.new("ShaderNodeOutputWorld")
    bg = next((n for n in nt.nodes if n.type == "BACKGROUND"), None)
    if bg is None:
        bg = nt.nodes.new("ShaderNodeBackground")
        nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    return nt, bg


def _env_chain(nt, bg):
    """Environment Texture <- Mapping <- Texture Coordinate, feeding the background colour."""
    env = next((n for n in nt.nodes if n.type == "TEX_ENVIRONMENT"), None)
    if env is None:
        env = nt.nodes.new("ShaderNodeTexEnvironment")
        env.location = (bg.location.x - 300, bg.location.y)
    mapping = next((n for n in nt.nodes if n.type == "MAPPING"), None)
    if mapping is None:
        mapping = nt.nodes.new("ShaderNodeMapping")
        mapping.location = (env.location.x - 220, env.location.y)
        coord = nt.nodes.new("ShaderNodeTexCoord")
        coord.location = (mapping.location.x - 200, mapping.location.y)
        nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
        nt.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    return env, mapping


def world_summary(world):
    d = {"name": world.name, "viewport_color": rv(world.color)}
    nt = getattr(world, "node_tree", None)
    if nt is not None:
        bg = next((n for n in nt.nodes if n.type == "BACKGROUND"), None)
        if bg is not None:
            d["strength"] = r(bg.inputs["Strength"].default_value)
            if bg.inputs["Color"].is_linked:
                src = bg.inputs["Color"].links[0].from_node
                if src.type == "TEX_ENVIRONMENT" and src.image:
                    d["hdri"] = src.image.filepath
                    mapping = next((n for n in nt.nodes if n.type == "MAPPING"), None)
                    if mapping is not None:
                        d["rotation_deg"] = r(math.degrees(mapping.inputs["Rotation"].default_value[2]), 2)
            else:
                d["color"] = rv(bg.inputs["Color"].default_value)
    return d


@registry.command("world_set")
def world_set(args):
    sc = bpy.context.scene
    world = sc.world
    if world is None:
        world = bpy.data.worlds.new("World")
        sc.world = world
    nt, bg = _world_nodes(world)
    if args.get("hdri"):
        name = args["hdri"]
        hdris = bundled_hdris()
        if name in hdris:
            path = hdris[name]
        else:
            if os.sep not in name and "/" not in name and not os.path.splitext(name)[1]:
                raise util.UserError(f"No bundled HDRI named {name!r}",
                                     hint="Bundled HDRIs: " + ", ".join(hdris) + ". Or pass a path to your own .hdr/.exr")
            path = util.resolve_path(name, must_exist=True, what="HDRI")
        env, _ = _env_chain(nt, bg)
        env.image = bpy.data.images.load(path, check_existing=True)
    elif args.get("color") is not None:
        for link in list(bg.inputs["Color"].links):
            nt.links.remove(link)
    if args.get("color") is not None:
        c = [float(x) for x in args["color"]][:3]
        bg.inputs["Color"].default_value = c + [1.0]
        world.color = c
    if args.get("strength") is not None:
        bg.inputs["Strength"].default_value = float(args["strength"])
    if args.get("rotation_deg") is not None:
        if not bg.inputs["Color"].is_linked:
            raise util.UserError("rotation_deg turns an HDRI; this world has no HDRI", hint="Pass hdri as well")
        _, mapping = _env_chain(nt, bg)
        mapping.inputs["Rotation"].default_value[2] = math.radians(float(args["rotation_deg"]))
    d = world_summary(world)
    d["bundled_hdris"] = list(bundled_hdris())
    return d


@registry.command("light_set")
def light_set(args):
    name = args["name"]
    obj = bpy.data.objects.get(name)
    ltype = args.get("type")
    if ltype is not None and ltype not in _LIGHT_TYPES:
        raise util.UserError(f"Unknown light type {ltype!r}", hint="One of: " + ", ".join(_LIGHT_TYPES))
    created = obj is None
    if created:
        ltype = ltype or "POINT"
        data = bpy.data.lights.new(name, ltype)
        data.energy = _DEFAULT_ENERGY[ltype]
        obj = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(obj)
    elif obj.type != "LIGHT":
        raise util.UserError(f"{name!r} is a {obj.type}, not a light", hint="Use another name to create a new light")
    elif ltype is not None and obj.data.type != ltype:
        obj.data.type = ltype
    li = obj.data
    if args.get("energy") is not None:
        li.energy = float(args["energy"])
    if args.get("color") is not None:
        li.color = [float(x) for x in args["color"]][:3]
    if args.get("size") is not None:
        if li.type == "AREA":
            li.size = float(args["size"])
        elif li.type in ("POINT", "SPOT"):
            li.shadow_soft_size = float(args["size"])
    if args.get("angle_deg") is not None and li.type == "SUN":
        li.angle = math.radians(float(args["angle_deg"]))
    if li.type == "SPOT":
        if args.get("spot_angle_deg") is not None:
            li.spot_size = math.radians(float(args["spot_angle_deg"]))
        if args.get("spot_blend") is not None:
            li.spot_blend = float(args["spot_blend"])
    if args.get("shadow") is not None:
        li.use_shadow = bool(args["shadow"])
    if args.get("location") is not None:
        obj.location = Vector(args["location"])
    if args.get("rotation_deg") is not None:
        obj.rotation_euler = [math.radians(a) for a in args["rotation_deg"]]
    if args.get("look_at") is not None:
        direction = Vector(args["look_at"]) - obj.location
        if direction.length > 1e-9:
            obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()
    d = scene.object_detail(obj)
    d["created"] = created
    return d
