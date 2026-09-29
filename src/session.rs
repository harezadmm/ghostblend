//! Session directory: where the embedded bridge, autosaves, previews and
//! renders live for one `ghostblend` process.

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use uuid::Uuid;

use crate::bridge_files;

pub struct Session {
    pub dir: PathBuf,
    pub id: String,
    pub ephemeral: bool,
}

impl Session {
    /// Create or resume a session directory and write the embedded bridge into it.
    pub fn open(
        explicit_dir: Option<&Path>,
        resume_id: Option<&str>,
        ephemeral: bool,
    ) -> Result<Session> {
        let (dir, id) = match (explicit_dir, resume_id) {
            (Some(d), _) => {
                let id = d
                    .file_name()
                    .and_then(|s| s.to_str())
                    .unwrap_or("custom")
                    .to_string();
                (d.to_path_buf(), id)
            }
            (None, Some(id)) => (sessions_root()?.join(id), id.to_string()),
            (None, None) => {
                let id = short_id();
                (sessions_root()?.join(&id), id)
            }
        };
        std::fs::create_dir_all(&dir)
            .with_context(|| format!("creating session directory {}", dir.display()))?;
        for sub in ["autosave", "checkpoints", "previews", "renders", "bridge/gb"] {
            std::fs::create_dir_all(dir.join(sub))?;
        }
        write_bridge(&dir)?;
        Ok(Session { dir, id, ephemeral })
    }

    pub fn run_entry(&self) -> PathBuf {
        self.dir.join(bridge_files::RUN_ENTRY)
    }

    pub fn render_entry(&self) -> PathBuf {
        self.dir.join(bridge_files::RENDER_ENTRY)
    }

    pub fn has_autosave(&self) -> bool {
        std::fs::read_dir(self.dir.join("autosave"))
            .map(|mut rd| {
                rd.any(|e| {
                    e.ok()
                        .and_then(|e| e.file_name().into_string().ok())
                        .is_some_and(|n| n.ends_with(".blend"))
                })
            })
            .unwrap_or(false)
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        if self.ephemeral {
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }
}

/// Root that holds one sub-directory per session.
pub fn sessions_root() -> Result<PathBuf> {
    Ok(crate::runtime::default_data_root().join("s"))
}

/// A short id keeps the session path well under the Windows script-path limit.
fn short_id() -> String {
    Uuid::new_v4().simple().to_string()[..8].to_string()
}

fn write_bridge(dir: &Path) -> Result<()> {
    for (rel, contents) in bridge_files::FILES {
        let path = dir.join(rel);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        // Rewrite only when changed, so a resumed session keeps its mtimes stable.
        let up_to_date = std::fs::read_to_string(&path)
            .map(|existing| existing == *contents)
            .unwrap_or(false);
        if !up_to_date {
            std::fs::write(&path, contents)
                .with_context(|| format!("writing bridge file {}", path.display()))?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn creates_dirs_and_writes_bridge() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("sess");
        let s = Session::open(Some(&dir), None, false).unwrap();
        assert!(s.run_entry().is_file());
        assert!(dir.join("bridge/gb/main.py").is_file());
        assert!(dir.join("autosave").is_dir());
        assert!(!s.has_autosave());
    }

    #[test]
    fn short_id_is_eight_chars() {
        assert_eq!(short_id().len(), 8);
    }

    #[test]
    fn ephemeral_session_is_removed_on_drop() {
        let tmp = tempfile::tempdir().unwrap();
        let dir = tmp.path().join("ephem");
        {
            let _s = Session::open(Some(&dir), None, true).unwrap();
            assert!(dir.is_dir());
        }
        assert!(!dir.exists());
    }
}
