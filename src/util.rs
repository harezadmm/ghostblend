//! Small helpers shared across modules.

use std::path::{Path, PathBuf};

/// Prevent a console window from flashing up when a GUI MCP client
/// (for example Claude Desktop) starts Blender through us on Windows.
pub fn no_console_window(cmd: &mut tokio::process::Command) {
    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(windows))]
    {
        let _ = cmd;
    }
}

/// Resolve `p` against `base` unless it is already absolute.
pub fn resolve_path(base: &Path, p: &str) -> PathBuf {
    let path = PathBuf::from(p);
    if path.is_absolute() { path } else { base.join(path) }
}

/// Cut a string to at most `max` characters, noting how much was dropped.
pub fn truncate(s: &str, max: usize) -> String {
    let count = s.chars().count();
    if count <= max {
        return s.to_string();
    }
    let kept: String = s.chars().take(max).collect();
    format!("{kept}... [{} more characters]", count - max)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn truncate_keeps_short_strings() {
        assert_eq!(truncate("abc", 5), "abc");
    }

    #[test]
    fn truncate_notes_dropped_chars() {
        assert_eq!(truncate("abcdef", 3), "abc... [3 more characters]");
    }

    #[test]
    fn resolve_relative_and_absolute() {
        let base = if cfg!(windows) { Path::new(r"C:\work") } else { Path::new("/work") };
        assert_eq!(resolve_path(base, "out/a.glb"), base.join("out/a.glb"));
        let abs = if cfg!(windows) { r"D:\x\y.glb" } else { "/x/y.glb" };
        assert_eq!(resolve_path(base, abs), PathBuf::from(abs));
    }
}
