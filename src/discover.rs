//! Locate a Blender executable and read its version.
//!
//! Search order: explicit `--blender` flag, `GHOSTBLEND_BLENDER`, `blender` on
//! PATH, the standard per-OS install locations (highest version wins).

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;

pub const ENV_VAR: &str = "GHOSTBLEND_BLENDER";

/// Minimum Blender version Ghostblend targets.
pub const MIN_VERSION: (u32, u32) = (4, 2);

#[derive(Debug, Clone)]
pub struct Found {
    pub path: PathBuf,
    /// Human-readable description of where the executable came from.
    pub source: &'static str,
}

#[derive(Debug, Clone, thiserror::Error)]
pub enum DiscoverError {
    #[error("Blender executable not found at {path} (set by {origin})")]
    Missing { path: PathBuf, origin: &'static str },
    #[error(
        "Blender was not found. Install Blender 4.2 or newer from blender.org, or pass \
         --blender <path to blender executable>, or set GHOSTBLEND_BLENDER."
    )]
    NotFound,
}

pub fn find_blender(explicit: Option<&Path>) -> Result<Found, DiscoverError> {
    if let Some(p) = explicit {
        return check(p.to_path_buf(), "--blender");
    }
    if let Some(v) = std::env::var_os(ENV_VAR).filter(|v| !v.is_empty()) {
        return check(PathBuf::from(v), ENV_VAR);
    }
    find_system_blender().ok_or(DiscoverError::NotFound)
}

/// An installed Blender on PATH or in a standard location. Versioned installs
/// older than [`MIN_VERSION`] are skipped so they never shadow the engine.
pub fn find_system_blender() -> Option<Found> {
    if let Ok(p) = which::which("blender") {
        return Some(Found { path: normalize_launcher(p), source: "PATH" });
    }
    for root in versioned_roots() {
        if let Some(p) = supported_versioned_install(&root) {
            return Some(Found { path: p, source: "standard install location" });
        }
    }
    fixed_candidates()
        .into_iter()
        .find(|p| p.is_file())
        .map(|path| Found { path, source: "standard install location" })
}

/// Like [`best_versioned_install`], but only if that version meets [`MIN_VERSION`].
pub fn supported_versioned_install(root: &Path) -> Option<PathBuf> {
    let exe = best_versioned_install(root)?;
    let dir = exe.parent()?.file_name()?.to_string_lossy().to_string();
    let v = parse_dir_version(&dir)?;
    let (major, minor) = (v.first().copied().unwrap_or(0), v.get(1).copied().unwrap_or(0));
    ((major, minor) >= MIN_VERSION).then_some(exe)
}

fn check(path: PathBuf, source: &'static str) -> Result<Found, DiscoverError> {
    let path = normalize_launcher(path);
    if path.is_file() {
        Ok(Found { path, source })
    } else {
        Err(DiscoverError::Missing { path, origin: source })
    }
}

/// On Windows, `blender-launcher.exe` detaches from the console and drops stdio.
/// Always use the sibling `blender.exe` instead.
pub fn normalize_launcher(path: PathBuf) -> PathBuf {
    let is_launcher = path
        .file_name()
        .and_then(|n| n.to_str())
        .is_some_and(|n| n.eq_ignore_ascii_case("blender-launcher.exe"));
    if is_launcher {
        let sibling = path.with_file_name("blender.exe");
        if sibling.is_file() {
            return sibling;
        }
    }
    path
}

/// Directories that contain one sub-folder per installed version,
/// such as `C:\Program Files\Blender Foundation\Blender 5.1`.
fn versioned_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    if cfg!(windows) {
        for var in ["ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"] {
            if let Some(pf) = std::env::var_os(var) {
                let root = PathBuf::from(pf).join("Blender Foundation");
                if !roots.contains(&root) {
                    roots.push(root);
                }
            }
        }
        if roots.is_empty() {
            roots.push(PathBuf::from(r"C:\Program Files\Blender Foundation"));
        }
    }
    roots
}

fn fixed_candidates() -> Vec<PathBuf> {
    let mut v = Vec::new();
    if cfg!(windows) {
        v.push(PathBuf::from(r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"));
        v.push(PathBuf::from(r"C:\Program Files\Steam\steamapps\common\Blender\blender.exe"));
    } else if cfg!(target_os = "macos") {
        v.push(PathBuf::from("/Applications/Blender.app/Contents/MacOS/Blender"));
        if let Some(home) = dirs::home_dir() {
            v.push(home.join("Applications/Blender.app/Contents/MacOS/Blender"));
        }
    } else {
        for p in ["/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender", "/opt/blender/blender"] {
            v.push(PathBuf::from(p));
        }
    }
    v
}

/// Pick the highest-versioned `Blender X.Y[.Z]` folder under `root` that contains an executable.
pub fn best_versioned_install(root: &Path) -> Option<PathBuf> {
    let exe_name = if cfg!(windows) { "blender.exe" } else { "blender" };
    let mut best: Option<(Vec<u32>, PathBuf)> = None;
    for entry in std::fs::read_dir(root).ok()?.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        let Some(version) = parse_dir_version(&name) else { continue };
        let exe = entry.path().join(exe_name);
        if !exe.is_file() {
            continue;
        }
        if best.as_ref().is_none_or(|(v, _)| version > *v) {
            best = Some((version, exe));
        }
    }
    best.map(|(_, p)| p)
}

/// `"Blender 5.1"` -> `[5, 1]`; `"Blender 4.2 LTS"` -> `[4, 2]`; anything else -> `None`.
pub fn parse_dir_version(name: &str) -> Option<Vec<u32>> {
    let rest = name.strip_prefix("Blender ")?;
    let token = rest.split_whitespace().next()?;
    parse_numeric_version(token)
}

fn parse_numeric_version(token: &str) -> Option<Vec<u32>> {
    let parts: Option<Vec<u32>> = token.split('.').map(|p| p.parse::<u32>().ok()).collect();
    parts.filter(|v| !v.is_empty())
}

/// Extract `"5.1.1"` from `blender --version` output.
pub fn parse_version_output(output: &str) -> Option<String> {
    for line in output.lines() {
        let line = line.trim();
        if let Some(rest) = line.strip_prefix("Blender ") {
            let token = rest.split_whitespace().next()?;
            if parse_numeric_version(token).is_some() {
                return Some(token.to_string());
            }
        }
    }
    None
}

/// True when `version` (like `"5.1.1"`) is at least [`MIN_VERSION`].
pub fn meets_minimum(version: &str) -> bool {
    match parse_numeric_version(version) {
        Some(v) => {
            let major = v.first().copied().unwrap_or(0);
            let minor = v.get(1).copied().unwrap_or(0);
            (major, minor) >= MIN_VERSION
        }
        None => false,
    }
}

/// Run `blender --version` and parse the version number.
pub async fn blender_version(path: &Path) -> Option<String> {
    let mut cmd = tokio::process::Command::new(path);
    cmd.arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    crate::util::no_console_window(&mut cmd);
    let out = tokio::time::timeout(Duration::from_secs(60), cmd.output()).await.ok()?.ok()?;
    parse_version_output(&String::from_utf8_lossy(&out.stdout))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn make_install(root: &Path, dir: &str) -> PathBuf {
        let d = root.join(dir);
        fs::create_dir_all(&d).unwrap();
        let exe = d.join(if cfg!(windows) { "blender.exe" } else { "blender" });
        fs::write(&exe, b"").unwrap();
        exe
    }

    #[test]
    fn highest_version_folder_wins() {
        let tmp = tempfile::tempdir().unwrap();
        make_install(tmp.path(), "Blender 3.6");
        let best = make_install(tmp.path(), "Blender 5.1");
        make_install(tmp.path(), "Blender 4.5");
        fs::create_dir_all(tmp.path().join("Blender 9.9")).unwrap(); // no executable inside
        assert_eq!(best_versioned_install(tmp.path()), Some(best));
    }

    #[test]
    fn version_compare_is_numeric_not_lexical() {
        let tmp = tempfile::tempdir().unwrap();
        make_install(tmp.path(), "Blender 9.9");
        let best = make_install(tmp.path(), "Blender 10.0");
        assert_eq!(best_versioned_install(tmp.path()), Some(best));
    }

    #[test]
    fn old_only_install_is_not_supported() {
        let tmp = tempfile::tempdir().unwrap();
        make_install(tmp.path(), "Blender 3.6");
        assert!(best_versioned_install(tmp.path()).is_some());
        assert_eq!(supported_versioned_install(tmp.path()), None);
        let ok = make_install(tmp.path(), "Blender 4.2");
        assert_eq!(supported_versioned_install(tmp.path()), Some(ok));
    }

    #[test]
    fn launcher_maps_to_blender_exe() {
        let tmp = tempfile::tempdir().unwrap();
        let launcher = tmp.path().join("blender-launcher.exe");
        let exe = tmp.path().join("blender.exe");
        fs::write(&launcher, b"").unwrap();
        fs::write(&exe, b"").unwrap();
        assert_eq!(normalize_launcher(launcher), exe);
    }

    #[test]
    fn explicit_missing_path_names_the_path() {
        let err = find_blender(Some(Path::new("Z:/definitely/not/here/blender.exe"))).unwrap_err();
        let msg = err.to_string();
        assert!(msg.contains("definitely"), "{msg}");
        assert!(msg.contains("--blender"), "{msg}");
    }

    #[test]
    fn parses_version_output() {
        let out = "Blender 5.1.1 (hash b70da489d7f4 built 2026-04-14 01:37:22)\nbuild date: 2026-04-14\n";
        assert_eq!(parse_version_output(out).as_deref(), Some("5.1.1"));
        assert_eq!(parse_version_output("garbage"), None);
    }

    #[test]
    fn parses_dir_versions() {
        assert_eq!(parse_dir_version("Blender 5.1"), Some(vec![5, 1]));
        assert_eq!(parse_dir_version("Blender 4.2 LTS"), Some(vec![4, 2]));
        assert_eq!(parse_dir_version("Blender"), None);
        assert_eq!(parse_dir_version("Other 1.0"), None);
    }

    #[test]
    fn minimum_version_check() {
        assert!(meets_minimum("5.1.1"));
        assert!(meets_minimum("4.2.0"));
        assert!(!meets_minimum("4.1.9"));
        assert!(!meets_minimum("3.6"));
    }
}
