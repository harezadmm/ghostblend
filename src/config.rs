//! Runtime configuration assembled from command-line flags.

use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct Config {
    /// Explicit Blender executable (`--blender`); `None` means auto-discovery.
    pub blender: Option<PathBuf>,
    /// Exact session directory (`--session-dir`).
    pub session_dir: Option<PathBuf>,
    /// Session id to resume (`--resume`).
    pub resume: Option<String>,
    /// Delete the session directory on exit.
    pub ephemeral: bool,
    /// Base directory for relative paths given by the agent.
    pub workdir: PathBuf,
    /// Autosaves kept per session.
    pub autosave_keep: u32,
    /// Render jobs allowed to run at the same time.
    pub max_jobs: usize,
    /// Where the Blender engine comes from.
    pub runtime_mode: crate::runtime::RuntimeMode,
    /// Whether Ghostblend may download its engine automatically.
    pub allow_download: bool,
}
