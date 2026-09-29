//! Render-job manager: runs each render as its own `blender -b` process so a
//! long render never blocks the interactive worker, parses Blender's progress
//! output, and supports polling, cancellation and bounded waiting.

use std::collections::{HashMap, VecDeque};
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::ChildStdin;
use tokio::sync::{Notify, Semaphore};

const RESULT_MARKER: &str = "@@gbjob:";
const LOG_TAIL: usize = 40;
/// Upper bound on a single render job. Generous enough for heavy stills and
/// short animations, but finite so a hung Blender can never starve the slots.
const RENDER_MAX: Duration = Duration::from_secs(3600);

/// One parsed unit of render progress.
#[derive(Debug, Clone, PartialEq)]
pub enum Progress {
    /// Cycles sample progress within a frame.
    Sample { current: u32, total: u32 },
    /// EEVEE sample progress within a frame.
    FrameSamples { current: u32, total: u32 },
    /// The renderer moved to a frame.
    Frame(i64),
    /// A file was written.
    Saved(String),
}

/// Parse one line of Blender render output. Returns `None` for lines we ignore.
pub fn parse_progress(line: &str) -> Option<Progress> {
    if let Some(rest) = line.strip_prefix("Saved: ") {
        let path = rest.trim().trim_matches('\'').trim_matches('"');
        return Some(Progress::Saved(path.to_string()));
    }
    // EEVEE: "... | Rendering 3 / 16 samples"
    if let Some(idx) = line.find("Rendering ") {
        let tail = &line[idx + "Rendering ".len()..];
        if let Some((cur, total)) = parse_fraction(tail, "samples") {
            return Some(Progress::FrameSamples { current: cur, total });
        }
    }
    // Cycles: "... | Sample 12/64"
    if let Some(idx) = line.find("Sample ") {
        let tail = &line[idx + "Sample ".len()..];
        if let Some((cur, total)) = parse_slash_fraction(tail) {
            return Some(Progress::Sample { current: cur, total });
        }
    }
    // Frame marker: "Fra:5 ..."
    if let Some(rest) = line.strip_prefix("Fra:") {
        let digits: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
        if let Ok(n) = digits.parse::<i64>() {
            return Some(Progress::Frame(n));
        }
    }
    None
}

fn parse_slash_fraction(s: &str) -> Option<(u32, u32)> {
    let s = s.trim_start();
    let end = s.find(|c: char| !(c.is_ascii_digit() || c == '/')).unwrap_or(s.len());
    let (cur, total) = s[..end].split_once('/')?;
    Some((cur.trim().parse().ok()?, total.trim().parse().ok()?))
}

fn parse_fraction(s: &str, unit: &str) -> Option<(u32, u32)> {
    let up_to = s.find(unit).unwrap_or(s.len());
    let seg = &s[..up_to];
    let (cur, total) = seg.split_once('/')?;
    Some((cur.trim().parse().ok()?, total.trim().parse().ok()?))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum State {
    #[default]
    Queued,
    Running,
    Done,
    Failed,
    Cancelled,
}

#[derive(Default)]
struct JobInner {
    state: State,
    percent: f32,
    log: VecDeque<String>,
    outputs: Vec<Value>,
    error: Option<String>,
    frame: Option<i64>,
}


pub struct Job {
    pub id: String,
    inner: Mutex<JobInner>,
    done: Notify,
    stdin: Mutex<Option<ChildStdin>>,
    cancel: std::sync::atomic::AtomicBool,
}

impl Job {
    pub fn snapshot(&self, include_outputs: bool) -> Value {
        let g = self.inner.lock().unwrap();
        let mut v = json!({
            "job_id": self.id,
            "state": g.state,
            "percent": (g.percent * 10.0).round() / 10.0,
            "log_tail": g.log.iter().cloned().collect::<Vec<_>>(),
        });
        if let Some(f) = g.frame {
            v["frame"] = json!(f);
        }
        if let Some(e) = &g.error {
            v["error"] = json!(e);
        }
        if include_outputs || matches!(g.state, State::Done) {
            v["outputs"] = json!(g.outputs);
        }
        v
    }

    fn set_state(&self, s: State) {
        self.inner.lock().unwrap().state = s;
        if matches!(s, State::Done | State::Failed | State::Cancelled) {
            self.done.notify_waiters();
        }
    }

    fn push_log(&self, line: String) {
        let mut g = self.inner.lock().unwrap();
        if g.log.len() == LOG_TAIL {
            g.log.pop_front();
        }
        g.log.push_back(line);
    }

    /// The first output image path, if any (for job_status include_image).
    pub fn first_output_path(&self) -> Option<String> {
        let g = self.inner.lock().unwrap();
        for o in &g.outputs {
            for key in ["still", "animation"] {
                if let Some(p) = o.get(key).and_then(Value::as_str) {
                    return Some(p.to_string());
                }
            }
        }
        None
    }
}

pub struct JobManager {
    runtime: Arc<crate::runtime::Runtime>,
    render_entry: PathBuf,
    jobs: Mutex<HashMap<String, Arc<Job>>>,
    order: Mutex<Vec<String>>,
    counter: AtomicU64,
    slots: Arc<Semaphore>,
}

impl JobManager {
    pub fn new(runtime: Arc<crate::runtime::Runtime>, render_entry: PathBuf, max_running: usize) -> Self {
        Self {
            runtime,
            render_entry,
            jobs: Mutex::new(HashMap::new()),
            order: Mutex::new(Vec::new()),
            counter: AtomicU64::new(1),
            slots: Arc::new(Semaphore::new(max_running.max(1))),
        }
    }

    pub fn new_job_id(&self) -> String {
        format!("r{}", self.counter.fetch_add(1, Ordering::Relaxed))
    }

    /// Start a render job against a prepared scene file.
    pub fn start(&self, id: String, scene: PathBuf, render_args: Value) -> Arc<Job> {
        let job = Arc::new(Job {
            id: id.clone(),
            inner: Mutex::new(JobInner::default()),
            done: Notify::new(),
            stdin: Mutex::new(None),
            cancel: std::sync::atomic::AtomicBool::new(false),
        });
        {
            self.jobs.lock().unwrap().insert(id.clone(), job.clone());
            self.order.lock().unwrap().push(id.clone());
        }
        let runtime = self.runtime.clone();
        let entry = self.render_entry.clone();
        let slots = self.slots.clone();
        let job2 = job.clone();
        tokio::spawn(async move {
            run_job(job2, runtime, entry, scene, render_args, slots).await;
        });
        job
    }

    pub fn get(&self, id: &str) -> Option<Arc<Job>> {
        self.jobs.lock().unwrap().get(id).cloned()
    }

    pub fn status(&self, id: &str, include_image: bool) -> Option<Value> {
        self.get(id).map(|j| j.snapshot(include_image))
    }

    pub async fn cancel(&self, id: &str) -> Option<Value> {
        let job = self.get(id)?;
        job.cancel.store(true, Ordering::Relaxed);
        // Closing stdin trips the render process's watchdog. Take it out of the
        // mutex first so we do not hold the lock across the await.
        let stdin = job.stdin.lock().unwrap().take();
        if let Some(mut stdin) = stdin {
            let _ = stdin.shutdown().await;
        }
        {
            let mut g = job.inner.lock().unwrap();
            if matches!(g.state, State::Queued | State::Running) {
                g.state = State::Cancelled;
            }
        }
        job.done.notify_waiters();
        Some(job.snapshot(false))
    }

    /// Wait until the job leaves Running/Queued, or `timeout` elapses.
    pub async fn wait(&self, id: &str, timeout: Duration) -> Option<Value> {
        let job = self.get(id)?;
        let deadline = tokio::time::Instant::now() + timeout;
        loop {
            // Register as a waiter *before* checking the state, so a completion
            // that fires between the check and the await cannot be missed
            // (`notify_waiters` wakes only already-registered waiters).
            let notified = job.done.notified();
            tokio::pin!(notified);
            notified.as_mut().enable();
            {
                let g = job.inner.lock().unwrap();
                if !matches!(g.state, State::Queued | State::Running) {
                    break;
                }
            }
            if tokio::time::timeout_at(deadline, notified).await.is_err() {
                break;
            }
        }
        Some(job.snapshot(true))
    }
}

async fn run_job(
    job: Arc<Job>,
    runtime: Arc<crate::runtime::Runtime>,
    entry: PathBuf,
    scene: PathBuf,
    render_args: Value,
    slots: Arc<Semaphore>,
) {
    let _permit = slots.acquire().await.expect("semaphore closed");
    if job.cancel.load(Ordering::Relaxed) {
        job.set_state(State::Cancelled);
        return;
    }
    let blender = match runtime.resolve() {
        crate::runtime::Resolution::Ready { path, .. } => path,
        crate::runtime::Resolution::Preparing(m) | crate::runtime::Resolution::Unavailable(m) => {
            job.inner.lock().unwrap().error = Some(m);
            job.set_state(State::Failed);
            return;
        }
    };
    job.set_state(State::Running);

    let mut cmd = tokio::process::Command::new(&blender);
    cmd.arg("-b")
        .arg(&scene)
        .arg("--python")
        .arg(&entry)
        .arg("--")
        .arg(render_args.to_string())
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    crate::util::no_console_window(&mut cmd);

    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            job.inner.lock().unwrap().error = Some(format!("could not launch Blender: {e}"));
            job.set_state(State::Failed);
            return;
        }
    };
    if let Some(stdin) = child.stdin.take() {
        *job.stdin.lock().unwrap() = Some(stdin);
    }
    // A cancel that arrived during spawn (before stdin was stored) would have
    // found no stdin to close, so re-check now that the child is fully wired up.
    if job.cancel.load(Ordering::Relaxed) {
        let _ = child.start_kill();
        let _ = child.wait().await;
        job.set_state(State::Cancelled);
        return;
    }
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();

    if let Some(stderr) = stderr {
        let job_err = job.clone();
        tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if !line.trim().is_empty() {
                    job_err.push_log(line);
                }
            }
        });
    }

    let mut total_samples = render_args.get("samples").and_then(Value::as_u64).unwrap_or(0) as u32;
    let job_read = job.clone();
    let core = async {
        if let Some(stdout) = stdout {
            let mut reader = BufReader::new(stdout);
            let mut buf = Vec::new();
            loop {
                buf.clear();
                match reader.read_until(b'\n', &mut buf).await {
                    Ok(0) => break,
                    Ok(_) => {
                        let line = String::from_utf8_lossy(&buf);
                        let line = line.trim_end();
                        if let Some(idx) = line.find(RESULT_MARKER) {
                            apply_result(&job_read, &line[idx + RESULT_MARKER.len()..]);
                        } else if let Some(p) = parse_progress(line) {
                            update_progress(&job_read, p, &mut total_samples);
                        }
                    }
                    Err(_) => break,
                }
            }
        }
        child.wait().await
    };

    // Bound the render so a wedged Blender (no output, never exits) cannot pin a
    // job in Running forever and hold its semaphore slot. On timeout the `core`
    // future is dropped, and kill_on_drop terminates the child.
    match tokio::time::timeout(RENDER_MAX, core).await {
        Ok(status) => finalize(&job, status),
        Err(_) => {
            job.inner.lock().unwrap().error =
                Some(format!("render exceeded the {}s limit and was stopped", RENDER_MAX.as_secs()));
            finalize(&job, Err(std::io::Error::new(std::io::ErrorKind::TimedOut, "render timeout")));
        }
    }
}

fn update_progress(job: &Arc<Job>, p: Progress, total_samples: &mut u32) {
    let mut g = job.inner.lock().unwrap();
    match p {
        Progress::Sample { current, total } | Progress::FrameSamples { current, total } => {
            if total > 0 {
                *total_samples = total;
                g.percent = (current as f32 / total as f32 * 100.0).clamp(0.0, 100.0);
            }
        }
        Progress::Frame(n) => g.frame = Some(n),
        Progress::Saved(path) => {
            g.percent = 100.0;
            g.log.push_back(format!("Saved: {path}"));
            if g.log.len() > LOG_TAIL {
                g.log.pop_front();
            }
        }
    }
}

fn apply_result(job: &Arc<Job>, json_part: &str) {
    let Ok(v) = serde_json::from_str::<Value>(json_part.trim()) else {
        return;
    };
    let mut g = job.inner.lock().unwrap();
    if v.get("ok").and_then(Value::as_bool) == Some(true) {
        if let Some(outs) = v.get("outputs").and_then(Value::as_array) {
            g.outputs = outs.clone();
        }
        g.percent = 100.0;
    } else {
        g.error = v.get("error").and_then(Value::as_str).map(String::from);
    }
}

fn finalize(job: &Arc<Job>, status: std::io::Result<std::process::ExitStatus>) {
    let cancelled = job.cancel.load(Ordering::Relaxed);
    let mut g = job.inner.lock().unwrap();
    if cancelled {
        g.state = State::Cancelled;
    } else if g.error.is_some() {
        g.state = State::Failed;
    } else if g.outputs.is_empty() {
        g.error = Some(match status {
            Ok(s) => format!("Blender exited ({s}) without producing an image"),
            Err(e) => format!("Blender process error: {e}"),
        });
        g.state = State::Failed;
    } else {
        g.state = State::Done;
        g.percent = 100.0;
    }
    drop(g);
    job.done.notify_waiters();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_cycles_sample() {
        let line = "Fra:1 Mem:45.1M (Peak 46.0M) | Time:00:01.23 | Sample 12/64";
        assert_eq!(parse_progress(line), Some(Progress::Sample { current: 12, total: 64 }));
    }

    #[test]
    fn parses_eevee_samples() {
        let line = "Fra:1 Mem:20.0M | Rendering 3 / 16 samples";
        assert_eq!(parse_progress(line), Some(Progress::FrameSamples { current: 3, total: 16 }));
    }

    #[test]
    fn parses_frame() {
        assert_eq!(parse_progress("Fra:5 Mem:1.0M | Preparing"), Some(Progress::Frame(5)));
    }

    #[test]
    fn parses_saved_windows_path() {
        let line = "Saved: 'C:\\out\\a.png'";
        assert_eq!(parse_progress(line), Some(Progress::Saved("C:\\out\\a.png".to_string())));
    }

    #[test]
    fn ignores_unrelated_lines() {
        assert_eq!(parse_progress("Blender 5.1.1"), None);
        assert_eq!(parse_progress("Info: Total render time"), None);
    }

    #[test]
    fn percent_from_samples() {
        let job = Arc::new(Job {
            id: "t".into(),
            inner: Mutex::new(JobInner::default()),
            done: Notify::new(),
            stdin: Mutex::new(None),
            cancel: std::sync::atomic::AtomicBool::new(false),
        });
        let mut total = 0;
        update_progress(&job, Progress::Sample { current: 16, total: 64 }, &mut total);
        assert_eq!(job.inner.lock().unwrap().percent, 25.0);
    }
}
