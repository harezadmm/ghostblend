//! The worker supervisor: owns at most one live Blender process, starts it
//! lazily, serializes calls to it, enforces timeouts, and restarts it after a
//! crash or hang, restoring the latest autosave so the agent keeps its scene.

pub mod process;
pub mod proto;

use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use tokio::sync::Mutex;

use process::{Ready, WorkerProcess};

use crate::runtime::{Resolution, Runtime};

#[derive(Clone)]
pub struct WorkerConfig {
    /// Where the Blender engine comes from (installed, bundled, or managed).
    pub runtime: Arc<Runtime>,
    pub session_dir: PathBuf,
    pub run_entry: PathBuf,
    pub workdir: PathBuf,
    pub autosave_keep: u32,
    pub ready_timeout: Duration,
}

/// Options for a single call.
pub struct CallOpts {
    pub autosave: bool,
    pub atomic: bool,
    pub timeout: Duration,
    /// If set, send an interrupt this long before the hard timeout.
    pub soft_interrupt_after: Option<Duration>,
}

impl CallOpts {
    pub fn read_only(timeout: Duration) -> Self {
        Self { autosave: false, atomic: false, timeout, soft_interrupt_after: None }
    }
    pub fn mutating(timeout: Duration) -> Self {
        Self { autosave: true, atomic: true, timeout, soft_interrupt_after: None }
    }
}

/// A successful call result.
#[derive(Debug)]
pub struct Reply {
    pub result: Value,
    pub autosave_seq: Option<u64>,
    pub notes: Vec<String>,
    pub ms: u64,
}

#[derive(Debug)]
pub enum CallError {
    /// The command ran but Blender reported an error object.
    Blender { error: Value, rolled_back: bool, notes: Vec<String> },
    /// The worker could not be started at all.
    Unavailable(String),
    /// The Blender engine is still being set up (first run); retry shortly.
    Preparing(String),
    /// The command exceeded its timeout and the worker was restarted.
    Timeout { notes: Vec<String> },
    /// The worker died while the command was in flight and was restarted.
    Crashed { notes: Vec<String> },
}

pub struct Worker {
    cfg: WorkerConfig,
    proc: Mutex<Option<Arc<WorkerProcess>>>,
    /// Serializes calls: the single Blender main thread handles one at a time.
    call_lock: Mutex<()>,
    ever_started: std::sync::atomic::AtomicBool,
}

impl Worker {
    pub fn new(cfg: WorkerConfig) -> Self {
        Self {
            cfg,
            proc: Mutex::new(None),
            call_lock: Mutex::new(()),
            ever_started: std::sync::atomic::AtomicBool::new(false),
        }
    }

    /// Ensure a live worker exists, starting it if needed. Returns its ready info.
    pub async fn ensure_started(&self) -> Result<Ready, CallError> {
        let handle = self.get_or_start().await?;
        Ok(handle.ready.clone())
    }

    async fn get_or_start(&self) -> Result<Arc<WorkerProcess>, CallError> {
        let mut guard = self.proc.lock().await;
        if let Some(p) = guard.as_ref() {
            return Ok(p.clone());
        }
        // Restore the scene if this worker has run before in this session.
        let restore = self.ever_started.load(std::sync::atomic::Ordering::Relaxed)
            || has_autosave(&self.cfg.session_dir);
        let blender = match self.cfg.runtime.resolve() {
            Resolution::Ready { path, .. } => path,
            Resolution::Preparing(msg) => return Err(CallError::Preparing(msg)),
            Resolution::Unavailable(msg) => return Err(CallError::Unavailable(msg)),
        };
        let started = WorkerProcess::spawn(
            &blender,
            &self.cfg.session_dir,
            &self.cfg.run_entry,
            &self.cfg.workdir,
            self.cfg.autosave_keep,
            restore,
            self.cfg.ready_timeout,
        )
        .await
        .map_err(|e| CallError::Unavailable(format!("{e:#}")))?;
        self.ever_started.store(true, std::sync::atomic::Ordering::Relaxed);
        let handle = Arc::new(started);
        *guard = Some(handle.clone());
        Ok(handle)
    }

    /// Kill the current worker and clear it, so the next call starts a fresh
    /// one. This always terminates the old process before returning, even if a
    /// caller still holds a handle to it (as the timeout path does), so the old
    /// and new Blender can never write the session directory concurrently.
    async fn take_down(&self) {
        let old = self.proc.lock().await.take();
        if let Some(arc) = old {
            arc.terminate().await;
        }
    }

    pub async fn call(&self, cmd: &str, args: Value, opts: CallOpts) -> Result<Reply, CallError> {
        let _serial = self.call_lock.lock().await;
        let mut notes = Vec::new();
        let handle = self.get_or_start().await?;

        let id = handle.alloc_id();
        let rx = handle.register(id);
        let request = json!({
            "id": id, "cmd": cmd, "args": args,
            "autosave": opts.autosave, "atomic": opts.atomic,
        });
        if let Err(e) = handle.send_json(&request).await {
            handle.forget(id);
            self.take_down().await;
            notes.push(format!("worker restarted: could not send request ({e})"));
            return Err(CallError::Crashed { notes });
        }

        let start = Instant::now();
        let outcome = self.await_reply(&handle, id, rx, &opts, start).await;
        match outcome {
            AwaitResult::Reply(resp) => build_reply(resp, notes),
            AwaitResult::Timeout => {
                handle.forget(id);
                self.restart_after_failure(&mut notes, "timed out").await;
                Err(CallError::Timeout { notes })
            }
            AwaitResult::Crashed => {
                handle.forget(id);
                let tail = handle.stderr_tail();
                self.restart_after_failure(&mut notes, "worker exited").await;
                notes.push(tail);
                Err(CallError::Crashed { notes })
            }
        }
    }

    async fn await_reply(
        &self,
        handle: &Arc<WorkerProcess>,
        id: u64,
        mut rx: tokio::sync::oneshot::Receiver<proto::Response>,
        opts: &CallOpts,
        start: Instant,
    ) -> AwaitResult {
        let hard = opts.timeout;
        let soft = opts.soft_interrupt_after;
        let mut interrupted = false;
        loop {
            let elapsed = start.elapsed();
            if elapsed >= hard {
                return AwaitResult::Timeout;
            }
            let next_deadline = match (soft, interrupted) {
                (Some(s), false) if s < hard => s.min(hard),
                _ => hard,
            };
            let wait = next_deadline.saturating_sub(elapsed).max(Duration::from_millis(1));
            match tokio::time::timeout(wait, &mut rx).await {
                Ok(Ok(resp)) => return AwaitResult::Reply(resp),
                Ok(Err(_)) => return AwaitResult::Crashed, // sender dropped: stdout closed
                Err(_) => {
                    if soft.is_some() && !interrupted && start.elapsed() >= soft.unwrap() {
                        interrupted = true;
                        let _ = handle
                            .send_json(&json!({"control": "interrupt", "id": id}))
                            .await;
                    }
                }
            }
        }
    }

    async fn restart_after_failure(&self, notes: &mut Vec<String>, why: &str) {
        self.take_down().await;
        notes.push(format!(
            "The command {why}, so Ghostblend restarted Blender and restored your latest autosave."
        ));
        match self.get_or_start().await {
            Ok(h) => {
                if h.ready.restored {
                    notes.push(format!("Scene restored to autosave #{}.", h.ready.seq));
                }
            }
            Err(e) => notes.push(format!("Restart failed: {e:?}")),
        }
    }
}

// The Reply variant is the common case (a successful call), so boxing it to
// equalise variant sizes would add an allocation to the hot path for no gain.
#[allow(clippy::large_enum_variant)]
enum AwaitResult {
    Reply(proto::Response),
    Timeout,
    Crashed,
}

fn build_reply(resp: proto::Response, mut notes: Vec<String>) -> Result<Reply, CallError> {
    if let Some(w) = resp.warnings {
        notes.extend(w);
    }
    if resp.ok {
        Ok(Reply {
            result: resp.result,
            autosave_seq: resp.autosave.map(|a| a.seq),
            notes,
            ms: resp.ms,
        })
    } else {
        Err(CallError::Blender {
            error: resp.error.unwrap_or(Value::Null),
            rolled_back: resp.rolled_back.unwrap_or(false),
            notes,
        })
    }
}

fn has_autosave(session_dir: &std::path::Path) -> bool {
    std::fs::read_dir(session_dir.join("autosave"))
        .map(|mut rd| {
            rd.any(|e| {
                e.ok()
                    .and_then(|e| e.file_name().into_string().ok())
                    .is_some_and(|n| n.ends_with(".blend"))
            })
        })
        .unwrap_or(false)
}
