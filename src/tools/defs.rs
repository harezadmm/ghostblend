//! The 25 tool definitions. Descriptions are written for the agent: they state
//! units (meters, degrees, RGBA 0..1) and when to reach for each tool.

use std::time::Duration;

use serde_json::json;

use super::schema::*;
use super::{Kind, ToolSpec};

fn secs(s: u64) -> Duration {
    Duration::from_secs(s)
}

/// Worker-backed, mutating tool.
fn mut_tool(name: &'static str, title: &'static str, desc: &'static str, schema: serde_json::Value, timeout: u64) -> ToolSpec {
    ToolSpec { name, title, description: desc, schema, mutating: true, read_only: false, timeout: secs(timeout), kind: Kind::Worker(name) }
}

/// Worker-backed, read-only tool.
fn read_tool(name: &'static str, title: &'static str, desc: &'static str, schema: serde_json::Value, timeout: u64) -> ToolSpec {
    ToolSpec { name, title, description: desc, schema, mutating: false, read_only: true, timeout: secs(timeout), kind: Kind::Worker(name) }
}

pub fn all() -> Vec<ToolSpec> {
    vec![
        // ---- scene ----
        mut_tool("scene_new", "New scene",
            "Start a fresh scene. By default it is empty (no objects); set keep_defaults to true for Blender's default cube, camera and light.",
            object(&[("keep_defaults", boolean("Keep the default cube, camera and light instead of an empty scene."))], &[]), 60),
        mut_tool("scene_open", "Open .blend",
            "Open an existing .blend file, replacing the current scene. For glTF/FBX/OBJ/STL/PLY/USD use import_model instead.",
            object(&[("path", string("Path to a .blend file. Relative paths resolve against the working directory."))], &["path"]), 600),
        read_tool("scene_save", "Save .blend",
            "Save the scene to a .blend file. With no path, saves back to the file opened or last saved.",
            object(&[
                ("path", string("Destination .blend path. Optional if the scene already has a file.")),
                ("compress", boolean("Write a compressed .blend (smaller, slightly slower).")),
            ], &[]), 120),
        read_tool("scene_info", "Scene overview",
            "Summarise the scene: every object with its transform and mesh counts, plus camera, lights, world, frame range, render engine, resolution and units. Start here to see what exists.",
            object(&[("limit", integer_range("Maximum objects to list in full (default 200).", 1, 100000))], &[]), 60),
        read_tool("object_info", "Object detail",
            "Full detail for one object: modifiers with parameters, materials, world-space bounding box, mesh statistics, custom properties and constraints.",
            object(&[("name", string("Object name (see scene_info)."))], &["name"]), 60),

        // ---- build ----
        mut_tool("add_primitive", "Add primitive",
            "Add a mesh primitive, empty, camera or light. Location in meters, rotation in degrees, uniform or per-axis scale.",
            object(&[
                ("type", enum_str("What to add.", &["cube","uv_sphere","ico_sphere","cylinder","cone","torus","plane","circle","monkey","grid","empty","camera","light"])),
                ("name", string("Name for the new object.")),
                ("location", vec_n("World position [x, y, z] in meters.", 3)),
                ("rotation_deg", vec_n("Euler rotation [x, y, z] in degrees.", 3)),
                ("scale", vec_n("Per-axis scale [x, y, z].", 3)),
                ("size", number("Base size in meters (cube, plane, grid, monkey).")),
                ("radius", number("Radius in meters (sphere, cylinder, cone, circle, torus).")),
                ("depth", number("Height in meters (cylinder, cone).")),
                ("segments", integer_range("Segment/vertex count where it applies.", 3, 4096)),
                ("light_type", enum_str("Light kind when type is light.", &["POINT","SUN","SPOT","AREA"])),
                ("energy", number("Light power (watts for point/spot/area, irradiance for sun).")),
            ], &["type"]), 60),
        mut_tool("transform", "Move / rotate / scale",
            "Set or offset an object's location (meters), rotation (degrees) and scale. mode 'set' assigns, 'delta' adds. Set apply to bake the transform into the mesh.",
            object(&[
                ("name", string("Object to transform.")),
                ("location", vec_n("Location [x, y, z] in meters.", 3)),
                ("rotation_deg", vec_n("Euler rotation [x, y, z] in degrees.", 3)),
                ("scale", vec_n("Scale [x, y, z].", 3)),
                ("mode", enum_str("'set' replaces values, 'delta' adds to them (default set).", &["set","delta"])),
                ("apply", boolean("Apply the transform into the mesh data, resetting the object transform.")),
            ], &["name"]), 120),
        mut_tool("object_delete", "Delete objects",
            "Delete one or more objects by name. Missing names are reported, not fatal.",
            object(&[("names", string_array("Names of objects to delete."))], &["names"]), 60),
        mut_tool("object_duplicate", "Duplicate object",
            "Duplicate an object. linked=true shares mesh data (instances); false makes an independent copy.",
            object(&[
                ("name", string("Object to duplicate.")),
                ("new_name", string("Name for the copy.")),
                ("linked", boolean("Share mesh data with the original (default false).")),
                ("location", vec_n("Optional world position for the copy [x, y, z].", 3)),
            ], &["name"]), 120),

        // ---- modifiers / materials ----
        mut_tool("modifier_add", "Add modifier",
            "Add a modifier to a mesh and set its parameters. Pass params as a map validated against Blender's modifier settings; set apply to bake it immediately.",
            object(&[
                ("name", string("Object to modify.")),
                ("type", enum_str("Modifier type.", &["subdivision","mirror","array","boolean","bevel","solidify","decimate","triangulate","weld","displace","remesh","wireframe","screw","smooth","edge_split"])),
                ("params", object_free("Modifier settings, e.g. {\"width\": 0.1} for bevel or {\"object\": \"Cutter\"} for boolean.")),
                ("apply", boolean("Apply the modifier into the mesh right away (default false).")),
            ], &["name","type"]), 300),
        mut_tool("modifier_apply", "Apply modifiers",
            "Apply (bake) a named modifier, or all modifiers, into the mesh.",
            object(&[
                ("name", string("Object whose modifiers to apply.")),
                ("modifier", string("Modifier name to apply. Omit to apply all.")),
            ], &["name"]), 300),
        mut_tool("material_set", "Set material",
            "Create or update a Principled BSDF material and assign it. Colors are RGBA floats 0..1. Assigns to slot 0 by default; set replace_all to paint every slot.",
            object(&[
                ("object", string("Object to receive the material.")),
                ("name", string("Material name. Reuses an existing material of this name, otherwise creates it.")),
                ("base_color", number_array("Base color, RGB or RGBA floats 0..1.")),
                ("metallic", number_range("Metallic 0..1.", 0.0, 1.0)),
                ("roughness", number_range("Roughness 0..1.", 0.0, 1.0)),
                ("ior", number("Index of refraction (default 1.45).")),
                ("alpha", number_range("Opacity 0..1 (below 1 enables transparency).", 0.0, 1.0)),
                ("transmission", number_range("Transmission 0..1 for glass-like surfaces.", 0.0, 1.0)),
                ("emission_color", number_array("Emission color, RGB or RGBA floats 0..1.")),
                ("emission_strength", number("Emission strength.")),
                ("texture_image_path", string("Image file to plug into base color.")),
                ("slot", integer_range("Material slot index (default 0; equal to the slot count appends).", 0, 32767)),
                ("replace_all", boolean("Assign this material to every slot on the object.")),
            ], &["object"]), 120),

        // ---- import / export ----
        mut_tool("import_model", "Import model",
            "Import a 3D file into the scene. Format is detected from the extension; supported: glTF/GLB, FBX, OBJ, STL, PLY, USD, Alembic.",
            object(&[
                ("path", string("Path to the model file.")),
                ("format", enum_str("Force a format instead of detecting by extension.", &["auto","gltf","fbx","obj","stl","ply","usd","abc"])),
                ("options", object_free("Extra importer options passed through to Blender.")),
            ], &["path"]), 600),
        read_tool("export_model", "Export model",
            "Export the scene or selected objects to a 3D file. Format follows the extension. Applies modifiers by default.",
            object(&[
                ("path", string("Destination file. The extension picks the format (.glb/.gltf/.fbx/.obj/.stl/.ply/.usd/.abc).")),
                ("selection", string_array("Object names to export. Omit to export the whole scene.")),
                ("apply_modifiers", boolean("Apply modifiers on export (default true).")),
                ("options", object_free("Extra exporter options passed through to Blender.")),
            ], &["path"]), 600),

        // ---- preview / render ----
        ToolSpec { name: "render_preview", title: "Preview image",
            description: "Render quick preview images of the scene and return them inline. Defaults to a 2x2 contact sheet (front, right, top, perspective) so you can see your work. Fast Workbench shading by default; use shading 'rendered' for lighting and materials. This does not change the scene.",
            schema: object(&[
                ("views", json!({"type":"array","description":"Views to render: any of persp, front, back, left, right, top, bottom, camera. Default is front, right, top, persp.","items":{"type":"string","enum":["persp","front","back","left","right","top","bottom","camera"]}})),
                ("size", integer_range("Pixel size of each view (default 512).", 64, 2048)),
                ("engine", enum_str("Render engine (default auto: Workbench).", &["auto","workbench","eevee","cycles"])),
                ("objects", string_array("Frame only these objects. Omit to frame everything visible.")),
                ("shading", enum_str("Workbench shading: solid, textured, or rendered (uses EEVEE/Cycles).", &["solid","textured","rendered"])),
                ("wireframe", boolean("Overlay wireframe.")),
                ("transparent", boolean("Transparent background.")),
            ], &[]),
            mutating: false, read_only: true, timeout: secs(300), kind: Kind::Preview },
        ToolSpec { name: "render", title: "Render",
            description: "Render the scene to an image or animation with the final engine and settings. Returns a job id; poll job_status. Set wait=true to block for quick stills.",
            schema: object(&[
                ("output_path", string("Output file (still) or directory/pattern (animation).")),
                ("frame", integer("Single frame to render (default the current frame).")),
                ("frame_start", integer("First frame of an animation range.")),
                ("frame_end", integer("Last frame of an animation range.")),
                ("engine", enum_str("Render engine (default the scene's).", &["auto","workbench","eevee","cycles"])),
                ("samples", integer_range("Render samples (quality).", 1, 16384)),
                ("resolution", vec_n("Output resolution [width, height] in pixels.", 2)),
                ("percentage", integer_range("Resolution percentage 1..100.", 1, 100)),
                ("transparent", boolean("Transparent background (film transparent).")),
                ("device", enum_str("Cycles device (default gpu when available).", &["gpu","cpu"])),
                ("wait", boolean("Block until the render finishes (only for short stills).")),
            ], &["output_path"]),
            mutating: false, read_only: true, timeout: secs(30), kind: Kind::Render },
        ToolSpec { name: "job_status", title: "Render job status",
            description: "Check a render job: state, progress percent, recent log lines and output files. Optionally return the finished image inline.",
            schema: object(&[
                ("job_id", string("Job id returned by render.")),
                ("include_image", boolean("Return the finished image inline when the job is done.")),
            ], &["job_id"]),
            mutating: false, read_only: true, timeout: secs(30), kind: Kind::JobStatus },
        ToolSpec { name: "job_cancel", title: "Cancel render job",
            description: "Cancel a running or queued render job.",
            schema: object(&[("job_id", string("Job id to cancel."))], &["job_id"]),
            mutating: false, read_only: true, timeout: secs(30), kind: Kind::JobCancel },

        // ---- history ----
        read_tool("checkpoint_save", "Save checkpoint",
            "Save a named checkpoint of the whole scene you can return to later.",
            object(&[("label", string("A short label for this checkpoint."))], &[]), 120),
        read_tool("checkpoint_list", "List checkpoints",
            "List saved checkpoints and the recent autosave history (one autosave per mutating command).",
            object(&[], &[]), 30),
        mut_tool("checkpoint_restore", "Restore checkpoint",
            "Restore the scene from a checkpoint id, an autosave sequence number, or by stepping back N autosaves (steps=1 undoes the last change).",
            object(&[
                ("checkpoint", string("Checkpoint id from checkpoint_save/list.")),
                ("autosave_seq", integer("Autosave sequence number from checkpoint_list.")),
                ("steps", integer_range("Undo this many mutating commands (1 = last change).", 1, 100000)),
            ], &[]), 120),

        // ---- inspect ----
        read_tool("validate", "Validate meshes",
            "Check objects for common problems: non-manifold or boundary edges, loose vertices, duplicate vertices, n-gons, zero-area faces, inconsistent normals, unapplied or negative scale, missing UVs, and missing texture files.",
            object(&[
                ("objects", string_array("Objects to check. Omit to check all meshes.")),
                ("checks", string_array("Limit to specific checks by name. Omit to run all.")),
            ], &[]), 600),
        mut_tool("run_python", "Run Python",
            "Run Blender Python (bpy) in the live scene for anything the other tools do not cover. Assign to a variable named result to return JSON. This executes code on the user's machine.",
            object(&[
                ("code", string("Python source. bpy, bmesh, mathutils, math and Vector are in scope.")),
                ("timeout_s", integer_range("Seconds before the code is interrupted (default 60, max 600).", 1, 600)),
            ], &["code"]), 60),
        read_tool("api_describe", "Describe bpy API",
            "Describe a bpy operator or type from Blender's own RNA: its properties, types, defaults, enum values and ranges. Use before run_python to get names right.",
            object(&[("path", string("e.g. bpy.ops.mesh.primitive_cube_add or bpy.types.BevelModifier."))], &["path"]), 60),
        read_tool("api_search", "Search bpy API",
            "Search Blender operators and types by keyword.",
            object(&[
                ("query", string("Search text, e.g. 'bevel' or 'decimate'.")),
                ("kind", enum_str("Limit to operators or types (default both).", &["ops","types","both"])),
                ("limit", integer_range("Maximum results (default 30).", 1, 200)),
            ], &["query"]), 60),
    ]
}
