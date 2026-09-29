//! A single live headless Blender process and the tasks that read its output.

use std::collections::{HashMap, VecDeque};
use std::path::Path;
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::{Context, Result, anyhow};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin};
use tokio::sync::{Mutex as AsyncMutex, oneshot};

use super::proto::{self, Event, Message, Response};

const STDERR_RING: usize = 200;

/// Details Blender reports when the worker becomes ready.
#[derive(Debug, Clone)]
pub struct Ready {
    pub blender: String,
    pub python: String,
    pub pid: u64,
    pub seq: u64,
    pub restored: bool,
}

type Pending = Arc<Mutex<HashMap<u64, oneshot::Sender<Response>>>>;

pub struct WorkerProcess {
    // Behind an async mutex so the process can be killed through a shared
    // reference, without needing unique ownership of the Arc.
    child: AsyncMutex<Child>,
    stdin: AsyncMutex<ChildStdin>,
    pending: Pending,
    stderr_ring: Arc<Mutex<VecDeque<String>>>,
    next_id: AtomicU64,
    pub ready: Ready,
    pub os_pid: Option<u32>,
}

impl WorkerProcess {
    /// Spawn the worker and wait until it reports the `ready` event.
    pub async fn spawn(
        blender: &Path,
        session_dir: &Path,
        run_entry: &Path,
        workdir: &Path,
        autosave_keep: u32,
        restore_latest: bool,
        ready_timeout: Duration,
    ) -> Result<WorkerProcess> {
        let mut cmd = tokio::process::Command::new(blender);
        cmd.arg("-b")
            .arg("--factory-startup")
            .arg("-noaudio")
            .arg("--python-exit-code")
            .arg("47")
            .arg("--python")
            .arg(run_entry)
            .arg("--")
            .arg("--session")
            .arg(session_dir)
            .arg("--workdir")
            .arg(workdir)
            .arg("--autosave-keep")
            .arg(autosave_keep.to_string());
        if restore_latest {
            cmd.arg("--restore-latest");
        }
        cmd.env("PYTHONUNBUFFERED", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        crate::util::no_console_window(&mut cmd);

        let mut child = cmd.spawn().with_context(|| {
            format!("launching Blender worker: {}", blender.display())
        })?;
        let os_pid = child.id();
        let stdin = child.stdin.take().context("worker stdin missing")?;
        let stdout = child.stdout.take().context("worker stdout missing")?;
        let stderr = child.stderr.take().context("worker stderr missing")?;

        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let stderr_ring = Arc::new(Mutex::new(VecDeque::with_capacity(STDERR_RING)));
        let (ready_tx, ready_rx) = oneshot::channel::<Ready>();

        spawn_stderr_reader(stderr, stderr_ring.clone());
        spawn_stdout_reader(stdout, pending.clone(), ready_tx);

        let ready = match tokio::time::timeout(ready_timeout, ready_rx).await {
            Ok(Ok(r)) => r,
            Ok(Err(_)) | Err(_) => {
                let _ = child.start_kill();
                let tail = tail_of(&stderr_ring);
                return Err(anyhow!(
                    "Blender worker did not become ready within {:?}.\n{}",
                    ready_timeout,
                    tail
                ));
            }
        };

        Ok(WorkerProcess {
            child: AsyncMutex::new(child),
            stdin: AsyncMutex::new(stdin),
            pending,
            stderr_ring,
            next_id: AtomicU64::new(1),
            ready,
            os_pid,
        })
    }

    pub fn alloc_id(&self) -> u64 {
        self.next_id.fetch_add(1, Ordering::Relaxed)
    }

    /// Register interest in a response before sending the request.
    pub fn register(&self, id: u64) -> oneshot::Receiver<Response> {
        let (tx, rx) = oneshot::channel();
        self.pending.lock().unwrap().insert(id, tx);
        rx
    }

    pub fn forget(&self, id: u64) {
        self.pending.lock().unwrap().remove(&id);
    }

    pub async fn send_json(&self, value: &serde_json::Value) -> Result<()> {
        let mut line = serde_json::to_string(value)?;
        line.push('\n');
        let mut stdin = self.stdin.lock().await;
        stdin.write_all(line.as_bytes()).await?;
        stdin.flush().await?;
        Ok(())
    }

    pub fn stderr_tail(&self) -> String {
        tail_of(&self.stderr_ring)
    }

    /// Kill the Blender process and wait for it to exit. Safe to call through a
    /// shared reference, and idempotent: killing an already-dead process is a
    /// no-op. The caller must ensure this returns before spawning a replacement,
    /// so the two never write the session directory at the same time.
    pub async fn terminate(&self) {
        let mut child = self.child.lock().await;
        let _ = child.start_kill();
        let _ = child.wait().await;
    }
}

fn spawn_stderr_reader(
    stderr: tokio::process::ChildStderr,
    ring: Arc<Mutex<VecDeque<String>>>,
) {
    tokio::spawn(async move {
        let mut reader = BufReader::new(stderr);
        let mut buf = Vec::new();
        loop {
            buf.clear();
            match reader.read_until(b'\n', &mut buf).await {
                Ok(0) => break,
                Ok(_) => {
                    let line = String::from_utf8_lossy(&buf).trim_end().to_string();
                    if line.is_empty() {
                        continue;
                    }
                    tracing::debug!(target: "ghostblend::worker", "blender: {line}");
                    let mut r = ring.lock().unwrap();
                    if r.len() == STDERR_RING {
                        r.pop_front();
                    }
                    r.push_back(line);
                }
                Err(_) => break,
            }
        }
    });
}

fn spawn_stdout_reader(
    stdout: tokio::process::ChildStdout,
    pending: Pending,
    ready_tx: oneshot::Sender<Ready>,
) {
    tokio::spawn(async move {
        let mut reader = BufReader::new(stdout);
        let mut buf = Vec::new();
        let mut ready_tx = Some(ready_tx);
        loop {
            buf.clear();
            match reader.read_until(b'\n', &mut buf).await {
                Ok(0) => break,
                Ok(_) => {
                    let line = String::from_utf8_lossy(&buf);
                    match proto::parse_line(line.trim_end()) {
                        Some(Message::Response(r)) => route_response(&pending, r),
                        Some(Message::Event(e)) => handle_event(e, &mut ready_tx),
                        Some(Message::Malformed(m)) => {
                            tracing::warn!(target: "ghostblend::worker", "malformed protocol line: {m}");
                        }
                        None => {
                            let t = line.trim_end();
                            if !t.is_empty() {
                                tracing::debug!(target: "ghostblend::worker", "blender out: {t}");
                            }
                        }
                    }
                }
                Err(_) => break,
            }
        }
        // stdout closed: the worker is gone. Fail every waiting caller.
        let mut map = pending.lock().unwrap();
        map.clear();
    });
}

fn route_response(pending: &Pending, resp: Response) {
    if let Some(tx) = pending.lock().unwrap().remove(&resp.id) {
        let _ = tx.send(resp);
    } else {
        tracing::warn!(target: "ghostblend::worker", "response for unknown id {}", resp.id);
    }
}

fn handle_event(e: Event, ready_tx: &mut Option<oneshot::Sender<Ready>>) {
    match e.event.as_str() {
        "ready" => {
            if let Some(tx) = ready_tx.take() {
                let _ = tx.send(Ready {
                    blender: e.blender.unwrap_or_default(),
                    python: e.python.unwrap_or_default(),
                    pid: e.pid.unwrap_or(0),
                    seq: e.seq.unwrap_or(0),
                    restored: e.restored.is_some(),
                });
            }
        }
        "log" => {
            let level = e.level.as_deref().unwrap_or("info");
            let msg = e.msg.unwrap_or_default();
            tracing::debug!(target: "ghostblend::worker", "bridge[{level}]: {msg}");
        }
        "fatal" => {
            tracing::error!(target: "ghostblend::worker", "bridge fatal: {}", e.msg.unwrap_or_default());
        }
        other => {
            tracing::debug!(target: "ghostblend::worker", "event: {other}");
        }
    }
}

fn tail_of(ring: &Arc<Mutex<VecDeque<String>>>) -> String {
    let r = ring.lock().unwrap();
    let lines: Vec<&str> = r.iter().rev().take(30).map(|s| s.as_str()).collect();
    let mut out = String::from("Recent Blender output:\n");
    for line in lines.iter().rev() {
        out.push_str("  ");
        out.push_str(line);
        out.push('\n');
    }
    out
}
