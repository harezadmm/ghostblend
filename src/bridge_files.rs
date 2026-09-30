//! The Python bridge, embedded into the binary at compile time and written to
//! the session directory at start-up. Keeping it in-binary means the user
//! installs one executable with no side files.

/// (relative path under the session dir, file contents)
pub const FILES: &[(&str, &str)] = &[
    ("bridge/run.py", include_str!("../bridge/run.py")),
    ("bridge/render_job.py", include_str!("../bridge/render_job.py")),
    ("bridge/gb/__init__.py", include_str!("../bridge/gb/__init__.py")),
    ("bridge/gb/registry.py", include_str!("../bridge/gb/registry.py")),
    ("bridge/gb/runtime.py", include_str!("../bridge/gb/runtime.py")),
    ("bridge/gb/util.py", include_str!("../bridge/gb/util.py")),
    ("bridge/gb/history.py", include_str!("../bridge/gb/history.py")),
    ("bridge/gb/scene.py", include_str!("../bridge/gb/scene.py")),
    ("bridge/gb/objects.py", include_str!("../bridge/gb/objects.py")),
    ("bridge/gb/modifiers.py", include_str!("../bridge/gb/modifiers.py")),
    ("bridge/gb/materials.py", include_str!("../bridge/gb/materials.py")),
    ("bridge/gb/model_io.py", include_str!("../bridge/gb/model_io.py")),
    ("bridge/gb/framing.py", include_str!("../bridge/gb/framing.py")),
    ("bridge/gb/font.py", include_str!("../bridge/gb/font.py")),
    ("bridge/gb/preview.py", include_str!("../bridge/gb/preview.py")),
    ("bridge/gb/validate.py", include_str!("../bridge/gb/validate.py")),
    ("bridge/gb/pyexec.py", include_str!("../bridge/gb/pyexec.py")),
    ("bridge/gb/api.py", include_str!("../bridge/gb/api.py")),
    ("bridge/gb/render.py", include_str!("../bridge/gb/render.py")),
    ("bridge/gb/selection.py", include_str!("../bridge/gb/selection.py")),
    ("bridge/gb/brush.py", include_str!("../bridge/gb/brush.py")),
    ("bridge/gb/editmesh.py", include_str!("../bridge/gb/editmesh.py")),
    ("bridge/gb/sculpt.py", include_str!("../bridge/gb/sculpt.py")),
    ("bridge/gb/paint.py", include_str!("../bridge/gb/paint.py")),
    ("bridge/gb/lighting.py", include_str!("../bridge/gb/lighting.py")),
    ("bridge/gb/bake.py", include_str!("../bridge/gb/bake.py")),
    ("bridge/gb/animate.py", include_str!("../bridge/gb/animate.py")),
    ("bridge/gb/main.py", include_str!("../bridge/gb/main.py")),
];

/// Path of the worker entry script relative to the session directory.
pub const RUN_ENTRY: &str = "bridge/run.py";

/// Path of the render-job entry script relative to the session directory.
pub const RENDER_ENTRY: &str = "bridge/render_job.py";
