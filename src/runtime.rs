//! Ghostblend's own Blender engine.
//!
//! Ghostblend does not require a separately installed Blender. The first time
//! it needs one, it provisions the official portable Blender build into its
//! data directory, verifies it against a pinned SHA-256, and runs it headless.
//! From the user's side there is one product; Blender is the engine inside it.
//!
//! Resolution order:
//! 1. `--blender` / `GHOSTBLEND_BLENDER` (explicit; never falls through)
//! 2. a `blender/` folder next to the ghostblend executable (offline bundle)
//! 3. the managed engine in `<data>/rt/<version>/`
//! 4. a system install of Blender 4.2+ (skipped with `--runtime managed`)
//! 5. download the managed engine (in the background; calls report progress)

use std::io::{BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use sha2::{Digest, Sha256};
use tokio::io::AsyncWriteExt;

/// The Blender release Ghostblend ships as its engine (the bridge is tested on 5.1).
pub const ENGINE_VERSION: &str = "5.1.2";
pub const ENGINE_BASE_URL: &str = "https://download.blender.org/release/Blender5.1/";
const READY_MARKER: &str = ".ghostblend-ready";
const DOWNLOAD_ATTEMPTS: u32 = 4;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArchiveKind {
    Zip,
    TarXz,
}

/// One downloadable engine build.
#[derive(Debug, Clone)]
pub struct Artifact {
    pub file: String,
    pub sha256: String,
    pub kind: ArchiveKind,
    /// Executable path relative to the engine directory.
    pub exe: String,
}

/// The official build for this OS/CPU, with its hash from blender.org's
/// `blender-5.1.2.sha256`. `None` where auto-provisioning is not supported.
pub fn host_artifact() -> Option<Artifact> {
    let (file, sha, kind, exe) = if cfg!(all(windows, target_arch = "x86_64")) {
        ("blender-5.1.2-windows-x64.zip",
         "345bedea7b0acf7cc9666423d8553f9129622aea34ded65c23e8cb70f83f14ff", ArchiveKind::Zip, "blender.exe")
    } else if cfg!(all(windows, target_arch = "aarch64")) {
        ("blender-5.1.2-windows-arm64.zip",
         "91d39ca72ed4862724eb4398ccfc11913e0650654175efed72023efd7748960e", ArchiveKind::Zip, "blender.exe")
    } else if cfg!(all(target_os = "linux", target_arch = "x86_64")) {
        ("blender-5.1.2-linux-x64.tar.xz",
         "aaccb355f50183979b698bcce7467103a76261b5fa59f4972295842662a285fb", ArchiveKind::TarXz, "blender")
    } else {
        return None;
    };
    Some(Artifact { file: file.into(), sha256: sha.into(), kind, exe: exe.into() })
}

/// Where Ghostblend keeps sessions and its engine: `GHOSTBLEND_HOME`, else the
/// per-user local data directory.
pub fn default_data_root() -> PathBuf {
    if let Some(home) = std::env::var_os("GHOSTBLEND_HOME").filter(|v| !v.is_empty()) {
        return PathBuf::from(home);
    }
    dirs::data_local_dir()
        .or_else(dirs::data_dir)
        .unwrap_or_else(std::env::temp_dir)
        .join("ghostblend")
}

/// A `blender/` folder next to the running executable (an offline bundle).
pub fn default_bundled_dir() -> Option<PathBuf> {
    std::env::current_exe().ok()?.parent().map(|d| d.join("blender"))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum RuntimeMode {
    /// Bundled or managed engine, else an installed Blender, else download.
    Auto,
    /// Always use Ghostblend's own engine (download it if needed).
    Managed,
    /// Only use an installed Blender.
    System,
}

pub struct RuntimeOptions {
    pub explicit: Option<PathBuf>,
    pub explicit_origin: &'static str,
    pub mode: RuntimeMode,
    pub allow_download: bool,
    pub data_root: PathBuf,
    pub bundled_dir: Option<PathBuf>,
    pub system_lookup: fn() -> Option<PathBuf>,
    pub artifact: Option<Artifact>,
    pub base_url: String,
    pub idle_timeout: Duration,
}

impl RuntimeOptions {
    /// Production defaults; `explicit` comes from `--blender` or `GHOSTBLEND_BLENDER`.
    pub fn standard(explicit: Option<PathBuf>, explicit_origin: &'static str, mode: RuntimeMode, allow_download: bool) -> Self {
        Self {
            explicit,
            explicit_origin,
            mode,
            allow_download,
            data_root: default_data_root(),
            bundled_dir: default_bundled_dir(),
            system_lookup: || crate::discover::find_system_blender().map(|f| f.path),
            artifact: host_artifact(),
            base_url: ENGINE_BASE_URL.to_string(),
            idle_timeout: Duration::from_secs(60),
        }
    }
}

/// Build the runtime for a CLI configuration. The explicit engine is
/// `--blender`, else `GHOSTBLEND_BLENDER`.
pub fn from_config(cfg: &crate::config::Config, mode: RuntimeMode, allow_download: bool) -> Arc<Runtime> {
    let (explicit, origin) = match (&cfg.blender, std::env::var_os(crate::discover::ENV_VAR).filter(|v| !v.is_empty())) {
        (Some(p), _) => (Some(p.clone()), "--blender"),
        (None, Some(v)) => (Some(PathBuf::from(v)), crate::discover::ENV_VAR),
        (None, None) => (None, "--blender"),
    };
    Runtime::new(RuntimeOptions::standard(explicit, origin, mode, allow_download))
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Resolution {
    Ready { path: PathBuf, source: String },
    /// The engine is being provisioned; the text says how far along it is.
    Preparing(String),
    Unavailable(String),
}

#[derive(Debug, Clone)]
enum Stage {
    Idle,
    Downloading,
    Verifying,
    Extracting,
    Ready(PathBuf),
    Failed(String),
}

#[derive(Default)]
pub struct Progress {
    pub done: AtomicU64,
    pub total: AtomicU64,
}

pub struct Runtime {
    opts: RuntimeOptions,
    stage: Mutex<Stage>,
    pub progress: Arc<Progress>,
    running: AtomicBool,
}

impl Runtime {
    pub fn new(opts: RuntimeOptions) -> Arc<Runtime> {
        Arc::new(Runtime { opts, stage: Mutex::new(Stage::Idle), progress: Arc::new(Progress::default()), running: AtomicBool::new(false) })
    }

    /// A runtime pinned to one executable (tests, and `--blender`).
    pub fn fixed(path: PathBuf) -> Arc<Runtime> {
        let mut o = RuntimeOptions::standard(Some(path), "--blender", RuntimeMode::System, false);
        o.bundled_dir = None;
        Runtime::new(o)
    }

    pub fn engine_dir(&self) -> PathBuf {
        self.opts.data_root.join("rt").join(ENGINE_VERSION)
    }

    /// The managed engine's executable, if it is fully installed.
    pub fn managed_exe(&self) -> Option<PathBuf> {
        let art = self.opts.artifact.as_ref()?;
        let dir = self.engine_dir();
        let exe = dir.join(&art.exe);
        (dir.join(READY_MARKER).is_file() && exe.is_file()).then_some(exe)
    }

    fn bundled_exe(&self) -> Option<PathBuf> {
        let dir = self.opts.bundled_dir.as_ref()?;
        let name = if cfg!(windows) { "blender.exe" } else { "blender" };
        let exe = dir.join(name);
        exe.is_file().then_some(exe)
    }

    /// Find an engine to run. May start a background download and return
    /// `Preparing`; call again later to get `Ready`.
    pub fn resolve(self: &Arc<Self>) -> Resolution {
        if let Some(p) = &self.opts.explicit {
            let p = crate::discover::normalize_launcher(p.clone());
            return if p.is_file() {
                Resolution::Ready { path: p, source: format!("set by {}", self.opts.explicit_origin) }
            } else {
                Resolution::Unavailable(format!(
                    "Blender executable not found at {} (set by {}). Fix the path, or remove it so Ghostblend uses its own engine.",
                    p.display(), self.opts.explicit_origin))
            };
        }
        if self.opts.mode != RuntimeMode::System {
            if let Some(p) = self.bundled_exe() {
                return Resolution::Ready { path: p, source: "bundled engine next to ghostblend".into() };
            }
            if let Some(p) = self.managed_exe() {
                return Resolution::Ready { path: p, source: format!("Ghostblend engine (Blender {ENGINE_VERSION})") };
            }
        }
        if self.opts.mode != RuntimeMode::Managed
            && let Some(p) = (self.opts.system_lookup)()
        {
            return Resolution::Ready { path: p, source: "installed Blender".into() };
        }
        if self.opts.mode == RuntimeMode::System {
            return Resolution::Unavailable(
                "No installed Blender 4.2+ was found. Install Blender, pass --blender <path>, or drop --runtime system so Ghostblend uses its own engine.".into());
        }
        if self.opts.artifact.is_none() {
            return Resolution::Unavailable(
                "Ghostblend cannot download its engine automatically on this platform yet. Install Blender 4.2+ from blender.org or pass --blender <path>.".into());
        }
        if !self.opts.allow_download {
            return Resolution::Unavailable(format!(
                "Ghostblend's engine is not installed yet. Run `ghostblend setup` once to download it (Blender {ENGINE_VERSION}, about 414 MB), or pass --blender <path>."));
        }
        let previous_failure = match &*self.stage.lock().unwrap() {
            Stage::Failed(e) => Some(e.clone()),
            Stage::Ready(p) if p.is_file() => {
                return Resolution::Ready { path: p.clone(), source: format!("Ghostblend engine (Blender {ENGINE_VERSION})") };
            }
            _ => None,
        };
        self.start_provisioning();
        let mut text = self.preparing_text();
        if let Some(e) = previous_failure {
            text.push_str(&format!(" The previous attempt failed ({e}) and is being retried."));
        }
        Resolution::Preparing(text)
    }

    /// Short progress line, e.g. "downloading 43% (178 of 414 MB)".
    pub fn status_line(&self) -> String {
        let stage = self.stage.lock().unwrap().clone();
        let done = self.progress.done.load(Ordering::Relaxed);
        let total = self.progress.total.load(Ordering::Relaxed);
        match stage {
            Stage::Verifying => "verifying the download".to_string(),
            Stage::Extracting => "unpacking the engine".to_string(),
            _ if total > 0 => format!(
                "downloading {}% ({} of {} MB)", done * 100 / total.max(1), done / 1_000_000, total / 1_000_000),
            _ => "starting the download".to_string(),
        }
    }

    /// Human-readable progress of the engine setup, for tool errors.
    pub fn preparing_text(&self) -> String {
        format!(
            "Ghostblend is setting up its Blender engine (first run only): {}. Tools work as soon as it finishes; try again in a minute.",
            self.status_line())
    }

    fn set_stage(&self, s: Stage) {
        *self.stage.lock().unwrap() = s;
    }

    /// Start provisioning in the background unless it is already running.
    pub fn start_provisioning(self: &Arc<Self>) {
        if self.running.swap(true, Ordering::SeqCst) {
            return;
        }
        self.set_stage(Stage::Downloading);
        let this = self.clone();
        tokio::spawn(async move {
            let outcome = this.provision().await;
            match outcome {
                Ok(p) => this.set_stage(Stage::Ready(p)),
                Err(e) => {
                    tracing::warn!("engine setup failed: {e}");
                    this.set_stage(Stage::Failed(e));
                }
            }
            this.running.store(false, Ordering::SeqCst);
        });
    }

    /// Download, verify and unpack the managed engine. Idempotent.
    pub async fn provision(&self) -> Result<PathBuf, String> {
        if let Some(p) = self.managed_exe() {
            return Ok(p);
        }
        let art = self.opts.artifact.clone().ok_or("no engine build for this platform")?;
        let rt_root = self.opts.data_root.join("rt");
        tokio::fs::create_dir_all(&rt_root).await.map_err(|e| format!("cannot create {}: {e}", rt_root.display()))?;
        let part = rt_root.join(format!("{}.part", art.file));
        let url = format!("{}{}", self.opts.base_url, art.file);
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(30))
            .user_agent(concat!("ghostblend/", env!("CARGO_PKG_VERSION")))
            .build()
            .map_err(|e| format!("HTTP client: {e}"))?;

        self.set_stage(Stage::Downloading);
        let mut last_err = String::new();
        let mut downloaded = false;
        for attempt in 1..=DOWNLOAD_ATTEMPTS {
            match download(&client, &url, &part, &self.progress, self.opts.idle_timeout).await {
                Ok(()) => {
                    downloaded = true;
                    break;
                }
                Err(e) => {
                    tracing::warn!("engine download attempt {attempt} failed: {e}");
                    last_err = e;
                    tokio::time::sleep(Duration::from_millis(500 * attempt as u64)).await;
                }
            }
        }
        if !downloaded {
            return Err(format!("download failed after {DOWNLOAD_ATTEMPTS} attempts: {last_err}"));
        }

        self.set_stage(Stage::Verifying);
        let part2 = part.clone();
        let actual = tokio::task::spawn_blocking(move || sha256_file(&part2))
            .await
            .map_err(|e| e.to_string())??;
        if !actual.eq_ignore_ascii_case(&art.sha256) {
            let _ = tokio::fs::remove_file(&part).await;
            return Err(format!("checksum mismatch for {} (expected {}, got {actual}); the corrupt file was deleted", art.file, art.sha256));
        }

        self.set_stage(Stage::Extracting);
        let dest = self.engine_dir();
        let (part3, dest2, art2) = (part.clone(), dest.clone(), art.clone());
        tokio::task::spawn_blocking(move || install_archive(&part3, &dest2, &art2))
            .await
            .map_err(|e| e.to_string())??;
        let _ = tokio::fs::remove_file(&part).await;
        self.managed_exe().ok_or_else(|| format!("engine unpacked but {} is missing", art.exe))
    }
}

/// Stream `url` into `part`, resuming a partial file with an HTTP Range request.
/// Fails if no data arrives for `idle`.
pub async fn download(client: &reqwest::Client, url: &str, part: &Path, progress: &Progress, idle: Duration) -> Result<(), String> {
    let existing = tokio::fs::metadata(part).await.map(|m| m.len()).unwrap_or(0);
    let mut req = client.get(url);
    if existing > 0 {
        req = req.header(reqwest::header::RANGE, format!("bytes={existing}-"));
    }
    let mut resp = tokio::time::timeout(idle, req.send())
        .await
        .map_err(|_| format!("no response from {url} within {}s", idle.as_secs()))?
        .map_err(|e| format!("request failed: {e}"))?;
    let status = resp.status();
    if status == reqwest::StatusCode::RANGE_NOT_SATISFIABLE && existing > 0 {
        return Ok(()); // already complete; the checksum decides
    }
    let (mut file, start) = if status == reqwest::StatusCode::PARTIAL_CONTENT && existing > 0 {
        let f = tokio::fs::OpenOptions::new().append(true).open(part).await.map_err(|e| e.to_string())?;
        (f, existing)
    } else if status.is_success() {
        (tokio::fs::File::create(part).await.map_err(|e| e.to_string())?, 0)
    } else {
        return Err(format!("server answered HTTP {status} for {url}"));
    };
    progress.done.store(start, Ordering::Relaxed);
    progress.total.store(start + resp.content_length().unwrap_or(0), Ordering::Relaxed);
    loop {
        match tokio::time::timeout(idle, resp.chunk()).await {
            Err(_) => return Err(format!("download stalled: no data for {}s", idle.as_secs())),
            Ok(Err(e)) => return Err(format!("connection error: {e}")),
            Ok(Ok(None)) => break,
            Ok(Ok(Some(bytes))) => {
                file.write_all(&bytes).await.map_err(|e| format!("write failed: {e}"))?;
                progress.done.fetch_add(bytes.len() as u64, Ordering::Relaxed);
            }
        }
    }
    file.flush().await.map_err(|e| e.to_string())?;
    Ok(())
}

pub fn sha256_file(path: &Path) -> Result<String, String> {
    let f = std::fs::File::open(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    let mut reader = BufReader::with_capacity(1 << 20, f);
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = reader.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(hasher.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

/// Unpack `archive` into `dest`, replacing any unfinished previous attempt,
/// and write the ready marker last so a half-unpacked engine is never used.
pub fn install_archive(archive: &Path, dest: &Path, art: &Artifact) -> Result<(), String> {
    let parent = dest.parent().ok_or("engine directory has no parent")?;
    let tmp = parent.join(format!(".unpack-{ENGINE_VERSION}"));
    if tmp.exists() {
        std::fs::remove_dir_all(&tmp).map_err(|e| format!("cannot clear {}: {e}", tmp.display()))?;
    }
    std::fs::create_dir_all(&tmp).map_err(|e| e.to_string())?;
    match art.kind {
        ArchiveKind::Zip => extract_zip(archive, &tmp)?,
        ArchiveKind::TarXz => extract_tar_xz(archive, &tmp)?,
    }
    if !tmp.join(&art.exe).is_file() {
        return Err(format!("the archive does not contain {}", art.exe));
    }
    if dest.exists() {
        std::fs::remove_dir_all(dest).map_err(|e| format!("cannot replace {}: {e}", dest.display()))?;
    }
    std::fs::rename(&tmp, dest).map_err(|e| format!("cannot move engine into place: {e}"))?;
    std::fs::write(dest.join(READY_MARKER), ENGINE_VERSION).map_err(|e| e.to_string())?;
    Ok(())
}

/// Extract a zip, dropping the single top-level folder Blender archives use.
/// Entries that would escape the destination are skipped.
pub fn extract_zip(archive: &Path, dest: &Path) -> Result<(), String> {
    let f = std::fs::File::open(archive).map_err(|e| e.to_string())?;
    let mut zip = zip::ZipArchive::new(BufReader::new(f)).map_err(|e| format!("not a valid zip: {e}"))?;
    let entries: Vec<(PathBuf, bool)> = (0..zip.len())
        .filter_map(|i| zip.by_index(i).ok().and_then(|e| e.enclosed_name().map(|n| (n, e.is_dir()))))
        .collect();
    let strip = common_top_dir(&entries);
    for i in 0..zip.len() {
        let mut entry = zip.by_index(i).map_err(|e| e.to_string())?;
        let Some(name) = entry.enclosed_name() else { continue };
        let rel: PathBuf = if strip { name.components().skip(1).collect() } else { name };
        if rel.as_os_str().is_empty() {
            continue;
        }
        let out = dest.join(&rel);
        if entry.is_dir() {
            std::fs::create_dir_all(&out).map_err(|e| e.to_string())?;
            continue;
        }
        if let Some(p) = out.parent() {
            std::fs::create_dir_all(p).map_err(|e| e.to_string())?;
        }
        let mut o = std::fs::File::create(&out).map_err(|e| format!("cannot write {}: {e}", out.display()))?;
        std::io::copy(&mut entry, &mut o).map_err(|e| format!("cannot extract {}: {e}", rel.display()))?;
        o.flush().map_err(|e| e.to_string())?;
        #[cfg(unix)]
        if let Some(mode) = entry.unix_mode() {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&out, std::fs::Permissions::from_mode(mode));
        }
    }
    Ok(())
}

/// True when every entry lives under one shared top-level directory, i.e. no
/// file sits at the archive root and all entries share their first component.
fn common_top_dir(entries: &[(PathBuf, bool)]) -> bool {
    let mut top: Option<std::ffi::OsString> = None;
    for (name, is_dir) in entries {
        let mut comps = name.components();
        let Some(first) = comps.next() else { continue };
        if comps.next().is_none() && !is_dir {
            return false; // a file at the root
        }
        match &top {
            None => top = Some(first.as_os_str().to_owned()),
            Some(t) if t.as_os_str() != first.as_os_str() => return false,
            _ => {}
        }
    }
    top.is_some()
}

fn extract_tar_xz(archive: &Path, dest: &Path) -> Result<(), String> {
    let status = std::process::Command::new("tar")
        .arg("-xJf")
        .arg(archive)
        .arg("-C")
        .arg(dest)
        .arg("--strip-components=1")
        .status()
        .map_err(|e| format!("could not run tar: {e}"))?;
    if status.success() { Ok(()) } else { Err(format!("tar failed with {status}")) }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::AsyncReadExt;

    fn make_zip(path: &Path, entries: &[(&str, &[u8])]) {
        let f = std::fs::File::create(path).unwrap();
        let mut w = zip::ZipWriter::new(f);
        let opts = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
        for (name, data) in entries {
            w.start_file(*name, opts).unwrap();
            w.write_all(data).unwrap();
        }
        w.finish().unwrap();
    }

    fn fake_artifact(zip: &Path) -> Artifact {
        Artifact { file: "engine.zip".into(), sha256: sha256_file(zip).unwrap(), kind: ArchiveKind::Zip, exe: "blender.exe".into() }
    }

    fn opts(root: &Path) -> RuntimeOptions {
        RuntimeOptions {
            explicit: None,
            explicit_origin: "--blender",
            mode: RuntimeMode::Auto,
            allow_download: false,
            data_root: root.to_path_buf(),
            bundled_dir: None,
            system_lookup: || None,
            artifact: Some(Artifact { file: "x.zip".into(), sha256: String::new(), kind: ArchiveKind::Zip, exe: "blender.exe".into() }),
            base_url: String::new(),
            idle_timeout: Duration::from_secs(2),
        }
    }

    #[test]
    fn sha256_of_known_content() {
        let tmp = tempfile::tempdir().unwrap();
        let p = tmp.path().join("a.txt");
        std::fs::write(&p, b"abc").unwrap();
        assert_eq!(sha256_file(&p).unwrap(), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    }

    #[test]
    fn zip_top_folder_is_stripped() {
        let tmp = tempfile::tempdir().unwrap();
        let z = tmp.path().join("e.zip");
        make_zip(&z, &[("blender-5.1.2-windows-x64/blender.exe", b"exe"),
                       ("blender-5.1.2-windows-x64/5.1/scripts/x.py", b"py")]);
        let out = tmp.path().join("out");
        std::fs::create_dir_all(&out).unwrap();
        extract_zip(&z, &out).unwrap();
        assert_eq!(std::fs::read(out.join("blender.exe")).unwrap(), b"exe");
        assert!(out.join("5.1/scripts/x.py").is_file());
    }

    #[test]
    fn zip_without_top_folder_is_kept_flat() {
        let tmp = tempfile::tempdir().unwrap();
        let z = tmp.path().join("e.zip");
        make_zip(&z, &[("blender.exe", b"exe"), ("readme.txt", b"r")]);
        let out = tmp.path().join("out");
        std::fs::create_dir_all(&out).unwrap();
        extract_zip(&z, &out).unwrap();
        assert!(out.join("blender.exe").is_file());
        assert!(out.join("readme.txt").is_file());
    }

    #[test]
    fn zip_path_traversal_is_skipped() {
        let tmp = tempfile::tempdir().unwrap();
        let z = tmp.path().join("e.zip");
        make_zip(&z, &[("top/blender.exe", b"exe"), ("../evil.txt", b"x")]);
        let out = tmp.path().join("deep/out");
        std::fs::create_dir_all(&out).unwrap();
        extract_zip(&z, &out).unwrap();
        assert!(!tmp.path().join("deep/evil.txt").exists());
        assert!(!tmp.path().join("evil.txt").exists());
    }

    #[test]
    fn engine_is_ready_only_after_marker() {
        let tmp = tempfile::tempdir().unwrap();
        let z = tmp.path().join("e.zip");
        make_zip(&z, &[("top/blender.exe", b"exe")]);
        let rt = Runtime::new(RuntimeOptions { artifact: Some(fake_artifact(&z)), ..opts(tmp.path()) });
        // A half-unpacked engine (exe present, no marker) must not be used.
        std::fs::create_dir_all(rt.engine_dir()).unwrap();
        std::fs::write(rt.engine_dir().join("blender.exe"), b"half").unwrap();
        assert!(rt.managed_exe().is_none());
        install_archive(&z, &rt.engine_dir(), &fake_artifact(&z)).unwrap();
        assert_eq!(rt.managed_exe(), Some(rt.engine_dir().join("blender.exe")));
        assert_eq!(std::fs::read(rt.engine_dir().join("blender.exe")).unwrap(), b"exe");
    }

    #[test]
    fn resolution_order() {
        let tmp = tempfile::tempdir().unwrap();
        // Nothing anywhere, downloads off: explains how to get the engine.
        let rt = Runtime::new(opts(tmp.path()));
        match rt.resolve() {
            Resolution::Unavailable(m) => assert!(m.contains("ghostblend setup"), "{m}"),
            other => panic!("{other:?}"),
        }
        // A system install is used in auto mode but not in managed mode.
        let sys = Runtime::new(RuntimeOptions { system_lookup: || Some(PathBuf::from("C:/sys/blender.exe")), ..opts(tmp.path()) });
        assert!(matches!(sys.resolve(), Resolution::Ready { ref source, .. } if source == "installed Blender"));
        let managed_only = Runtime::new(RuntimeOptions {
            mode: RuntimeMode::Managed, system_lookup: || Some(PathBuf::from("C:/sys/blender.exe")), ..opts(tmp.path()) });
        assert!(matches!(managed_only.resolve(), Resolution::Unavailable(_)));
        // A bundled engine next to the executable wins over the system install.
        let bundle = tmp.path().join("bundle");
        std::fs::create_dir_all(&bundle).unwrap();
        let exe_name = if cfg!(windows) { "blender.exe" } else { "blender" };
        std::fs::write(bundle.join(exe_name), b"").unwrap();
        let bundled = Runtime::new(RuntimeOptions {
            bundled_dir: Some(bundle.clone()), system_lookup: || Some(PathBuf::from("C:/sys/blender.exe")), ..opts(tmp.path()) });
        assert!(matches!(bundled.resolve(), Resolution::Ready { ref path, .. } if *path == bundle.join(exe_name)));
        // An explicit path that does not exist never falls through.
        let explicit = Runtime::new(RuntimeOptions { explicit: Some(PathBuf::from("Z:/nope/blender.exe")), ..opts(tmp.path()) });
        assert!(matches!(explicit.resolve(), Resolution::Unavailable(ref m) if m.contains("nope")));
    }

    /// A one-file HTTP server that honours `Range: bytes=N-` and can stall.
    async fn serve(body: Vec<u8>, stall_after: Option<usize>) -> String {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move {
            loop {
                let Ok((mut sock, _)) = listener.accept().await else { break };
                let body = body.clone();
                tokio::spawn(async move {
                    let mut req = Vec::new();
                    let mut buf = [0u8; 1024];
                    while !req.windows(4).any(|w| w == b"\r\n\r\n") {
                        let n = sock.read(&mut buf).await.unwrap_or(0);
                        if n == 0 { return; }
                        req.extend_from_slice(&buf[..n]);
                    }
                    let text = String::from_utf8_lossy(&req).to_lowercase();
                    let start = text.lines().find_map(|l| l.strip_prefix("range: bytes="))
                        .and_then(|r| r.trim().trim_end_matches('-').parse::<usize>().ok()).unwrap_or(0);
                    let slice = &body[start.min(body.len())..];
                    let status = if start > 0 { "206 Partial Content" } else { "200 OK" };
                    let head = format!("HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", slice.len());
                    let _ = sock.write_all(head.as_bytes()).await;
                    match stall_after {
                        Some(n) if start == 0 => {
                            let _ = sock.write_all(&slice[..n.min(slice.len())]).await;
                            tokio::time::sleep(Duration::from_secs(30)).await;
                        }
                        _ => { let _ = sock.write_all(slice).await; }
                    }
                });
            }
        });
        format!("http://{addr}/")
    }

    #[tokio::test]
    async fn download_full_and_resume() {
        let body: Vec<u8> = (0..300_000u32).map(|i| (i % 251) as u8).collect();
        let base = serve(body.clone(), None).await;
        let tmp = tempfile::tempdir().unwrap();
        let client = reqwest::Client::new();
        let p = Progress::default();

        let full = tmp.path().join("full.part");
        download(&client, &format!("{base}f"), &full, &p, Duration::from_secs(5)).await.unwrap();
        assert_eq!(std::fs::read(&full).unwrap(), body);
        assert_eq!(p.done.load(Ordering::Relaxed), body.len() as u64);

        let resumed = tmp.path().join("resume.part");
        std::fs::write(&resumed, &body[..100_000]).unwrap();
        download(&client, &format!("{base}f"), &resumed, &p, Duration::from_secs(5)).await.unwrap();
        assert_eq!(std::fs::read(&resumed).unwrap(), body, "resume must append the missing tail");
    }

    #[tokio::test]
    async fn stalled_download_times_out() {
        let body = vec![7u8; 200_000];
        let base = serve(body, Some(10_000)).await;
        let tmp = tempfile::tempdir().unwrap();
        let err = download(&reqwest::Client::new(), &format!("{base}f"), &tmp.path().join("s.part"),
                           &Progress::default(), Duration::from_secs(1)).await.unwrap_err();
        assert!(err.contains("stalled"), "{err}");
    }

    #[tokio::test]
    async fn provision_end_to_end_and_checksum_guard() {
        let tmp = tempfile::tempdir().unwrap();
        let z = tmp.path().join("engine.zip");
        make_zip(&z, &[("blender-x/blender.exe", b"real engine"), ("blender-x/5.1/a.txt", b"a")]);
        let bytes = std::fs::read(&z).unwrap();
        let base = serve(bytes, None).await;

        let root = tmp.path().join("home");
        let rt = Runtime::new(RuntimeOptions { artifact: Some(fake_artifact(&z)), base_url: base.clone(),
                                               allow_download: true, ..opts(&root) });
        let exe = rt.provision().await.unwrap();
        assert_eq!(std::fs::read(&exe).unwrap(), b"real engine");
        assert!(!root.join("rt/engine.zip.part").exists(), "archive is removed after install");

        // A wrong pinned hash rejects the download and leaves nothing installed.
        let root2 = tmp.path().join("home2");
        let mut bad = fake_artifact(&z);
        bad.sha256 = "00".repeat(32);
        let rt2 = Runtime::new(RuntimeOptions { artifact: Some(bad), base_url: base, allow_download: true, ..opts(&root2) });
        let err = rt2.provision().await.unwrap_err();
        assert!(err.contains("checksum mismatch"), "{err}");
        assert!(rt2.managed_exe().is_none());
        assert!(!root2.join("rt/engine.zip.part").exists(), "corrupt archive is deleted");
    }

    #[tokio::test]
    async fn resolve_starts_background_setup_then_becomes_ready() {
        let tmp = tempfile::tempdir().unwrap();
        let z = tmp.path().join("engine.zip");
        make_zip(&z, &[("t/blender.exe", b"e")]);
        let base = serve(std::fs::read(&z).unwrap(), None).await;
        let rt = Runtime::new(RuntimeOptions { artifact: Some(fake_artifact(&z)), base_url: base,
                                               allow_download: true, ..opts(&tmp.path().join("h")) });
        match rt.resolve() {
            Resolution::Preparing(m) => assert!(m.contains("first run only"), "{m}"),
            other => panic!("expected Preparing, got {other:?}"),
        }
        let deadline = std::time::Instant::now() + Duration::from_secs(20);
        loop {
            if let Resolution::Ready { source, .. } = rt.resolve() {
                assert!(source.contains("Ghostblend engine"), "{source}");
                break;
            }
            assert!(std::time::Instant::now() < deadline, "setup never finished");
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }
}
