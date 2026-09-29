//! Integration tests for the worker supervisor against a real headless Blender.
//!
//! They are skipped with a printed notice when no Blender is found, so the
//! suite is green on machines without Blender but exercises the real thing on
//! developer machines and CI runners that have it.

use std::path::PathBuf;
use std::time::Duration;

use ghostblend::session::Session;
use ghostblend::worker::{CallError, CallOpts, Worker, WorkerConfig};
use serde_json::{Value, json};

fn blender() -> Option<PathBuf> {
    // Reuse the crate's own discovery so the test matches production behaviour.
    match tokio::runtime::Runtime::new().unwrap().block_on(async {
        ghostblend::discover::find_blender(None)
    }) {
        Ok(found) => Some(found.path),
        Err(_) => None,
    }
}

struct Harness {
    worker: Worker,
    _session: Session,
    _tmp: tempfile::TempDir,
}

impl Harness {
    fn new(blender: PathBuf) -> Harness {
        let tmp = tempfile::tempdir().unwrap();
        let session = Session::open(Some(&tmp.path().join("s")), None, false).unwrap();
        let cfg = WorkerConfig {
            runtime: ghostblend::runtime::Runtime::fixed(blender),
            session_dir: session.dir.clone(),
            run_entry: session.run_entry(),
            workdir: tmp.path().to_path_buf(),
            autosave_keep: 10,
            ready_timeout: Duration::from_secs(90),
        };
        Harness { worker: Worker::new(cfg), _session: session, _tmp: tmp }
    }
}

macro_rules! require_blender {
    () => {
        match blender() {
            Some(b) => b,
            None => {
                eprintln!("SKIP: no Blender found; set GHOSTBLEND_BLENDER to run worker integration tests");
                return;
            }
        }
    };
}

fn rt() -> tokio::runtime::Runtime {
    tokio::runtime::Builder::new_multi_thread().enable_all().build().unwrap()
}

async fn run_py(w: &Worker, code: &str) -> Result<Value, CallError> {
    w.call("run_python", json!({ "code": code }), CallOpts::mutating(Duration::from_secs(60)))
        .await
        .map(|r| r.result)
}

#[test]
fn start_and_ping() {
    let b = require_blender!();
    rt().block_on(async {
        let h = Harness::new(b);
        let ready = h.worker.ensure_started().await.expect("worker should start");
        assert!(!ready.blender.is_empty(), "ready reports a Blender version");
        let reply = h
            .worker
            .call("ping", json!({}), CallOpts::read_only(Duration::from_secs(30)))
            .await
            .expect("ping");
        assert_eq!(reply.result["pong"], true);
    });
}

#[test]
fn scene_roundtrip() {
    let b = require_blender!();
    rt().block_on(async {
        let h = Harness::new(b);
        h.worker
            .call("scene_new", json!({}), CallOpts::mutating(Duration::from_secs(30)))
            .await
            .unwrap();
        run_py(&h.worker, "bpy.ops.mesh.primitive_cube_add()\nbpy.context.object.name = 'Box'")
            .await
            .unwrap();
        let info = h
            .worker
            .call("scene_info", json!({}), CallOpts::read_only(Duration::from_secs(30)))
            .await
            .unwrap();
        assert_eq!(info.result["objects_total"], 1);
        assert_eq!(info.result["objects"][0]["name"], "Box");
    });
}

#[test]
fn soft_interrupt_keeps_worker_alive() {
    let b = require_blender!();
    rt().block_on(async {
        let h = Harness::new(b);
        h.worker.ensure_started().await.unwrap();
        run_py(&h.worker, "bpy.ops.mesh.primitive_cube_add()\nbpy.context.object.name = 'Before'")
            .await
            .unwrap();
        let opts = CallOpts {
            autosave: true,
            atomic: true,
            timeout: Duration::from_secs(6),
            soft_interrupt_after: Some(Duration::from_secs(2)),
        };
        let start = std::time::Instant::now();
        let res = h.worker.call("run_python", json!({"code": "while True:\n    pass"}), opts).await;
        assert!(matches!(res, Err(CallError::Blender { .. })), "got {res:?}");
        assert!(start.elapsed() < Duration::from_secs(6), "interrupt should be well before hard timeout");
        // The worker is still alive and remembers the earlier object.
        let info = h
            .worker
            .call("scene_info", json!({}), CallOpts::read_only(Duration::from_secs(30)))
            .await
            .unwrap();
        assert_eq!(info.result["objects"][0]["name"], "Before");
    });
}

#[test]
fn hard_timeout_restarts_and_restores() {
    let b = require_blender!();
    rt().block_on(async {
        let h = Harness::new(b);
        run_py(&h.worker, "bpy.ops.mesh.primitive_cube_add()\nbpy.context.object.name = 'Keep'")
            .await
            .unwrap();
        // A C-level sleep ignores the Python interrupt, forcing a hard kill.
        let opts = CallOpts {
            autosave: false,
            atomic: false,
            timeout: Duration::from_secs(3),
            soft_interrupt_after: Some(Duration::from_secs(1)),
        };
        let res = h.worker.call("run_python", json!({"code": "import time\ntime.sleep(120)"}), opts).await;
        match res {
            Err(CallError::Timeout { notes }) => {
                assert!(notes.iter().any(|n| n.contains("restart")), "notes: {notes:?}");
            }
            other => panic!("expected Timeout, got {other:?}"),
        }
        // Next call works on a fresh worker with the scene restored.
        let info = h
            .worker
            .call("scene_info", json!({}), CallOpts::read_only(Duration::from_secs(30)))
            .await
            .expect("worker restarted");
        let names: Vec<String> = info.result["objects"]
            .as_array()
            .unwrap()
            .iter()
            .map(|o| o["name"].as_str().unwrap().to_string())
            .collect();
        assert!(names.contains(&"Keep".to_string()), "restored names: {names:?}");

        // Regression guard: the killed worker must be gone before the replacement
        // spawns, so the scene is exactly what was restored — no phantom object
        // from a second Blender writing the same autosave directory.
        assert_eq!(info.result["objects_total"], 1, "unexpected extra objects: {names:?}");
        run_py(&h.worker, "bpy.ops.mesh.primitive_cube_add()\nbpy.context.object.name = 'After'")
            .await
            .unwrap();
        let info2 = h
            .worker
            .call("scene_info", json!({}), CallOpts::read_only(Duration::from_secs(30)))
            .await
            .unwrap();
        assert_eq!(info2.result["objects_total"], 2, "scene diverged after restart");
    });
}

#[test]
fn crash_restarts_and_restores() {
    let b = require_blender!();
    rt().block_on(async {
        let h = Harness::new(b);
        run_py(&h.worker, "bpy.ops.mesh.primitive_cube_add()\nbpy.context.object.name = 'Survivor'")
            .await
            .unwrap();
        let res = run_py(&h.worker, "import os\nos._exit(3)").await;
        assert!(matches!(res, Err(CallError::Crashed { .. })), "got {res:?}");
        let info = h
            .worker
            .call("scene_info", json!({}), CallOpts::read_only(Duration::from_secs(30)))
            .await
            .expect("worker restarted after crash");
        assert_eq!(info.result["objects"][0]["name"], "Survivor");
    });
}

#[test]
fn stderr_flood_does_not_break_framing() {
    let b = require_blender!();
    rt().block_on(async {
        let h = Harness::new(b);
        let reply = run_py(
            &h.worker,
            "import sys\nfor _ in range(20000):\n    sys.stderr.write('noise line to flood the ring buffer\\n')\nresult = 'done'",
        )
        .await
        .unwrap();
        assert_eq!(reply["result"], "done");
    });
}
