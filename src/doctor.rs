//! `ghostblend doctor`: verify the Blender install and run a short self-test.

use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use crate::config::Config;
use crate::discover;
use crate::runtime::{self, Resolution, Runtime};
use crate::session::Session;
use crate::worker::{CallOpts, Worker, WorkerConfig};

pub async fn run(cfg: Config) -> anyhow::Result<()> {
    println!("Ghostblend doctor\n=================");

    let engine = runtime::from_config(&cfg, cfg.runtime_mode, false);
    match engine.managed_exe() {
        Some(p) => println!("[ok] Ghostblend engine: Blender {} installed at {}", runtime::ENGINE_VERSION, p.display()),
        None => println!(
            "[..] Ghostblend engine: not installed (run `ghostblend setup` once, about 414 MB; \
             the server also downloads it automatically on first use)"),
    }
    let (path, source) = match engine.resolve() {
        Resolution::Ready { path, source } => (path, source),
        Resolution::Preparing(m) | Resolution::Unavailable(m) => {
            println!("[X] Engine: {m}");
            anyhow::bail!("no Blender engine is available yet");
        }
    };
    println!("[ok] Engine in use: {}", path.display());
    println!("     source: {source}");
    let found = discover::Found { path, source: "resolved" };

    let version = discover::blender_version(&found.path).await;
    match &version {
        Some(v) if discover::meets_minimum(v) => println!("[ok] Blender version: {v}"),
        Some(v) => println!("[!]  Blender version: {v} (Ghostblend targets 4.2 or newer)"),
        None => println!("[!]  Could not read the Blender version"),
    }

    let session = Session::open(cfg.session_dir.as_deref(), None, true)?;
    println!("[ok] Session directory: {}", session.dir.display());

    let wcfg = WorkerConfig {
        runtime: Runtime::fixed(found.path.clone()),
        session_dir: session.dir.clone(),
        run_entry: session.run_entry(),
        workdir: cfg.workdir.clone(),
        autosave_keep: cfg.autosave_keep,
        ready_timeout: Duration::from_secs(90),
    };
    let worker = Arc::new(Worker::new(wcfg));

    let t0 = Instant::now();
    let ready = match worker.ensure_started().await {
        Ok(r) => r,
        Err(e) => {
            println!("[X] Worker failed to start: {e:?}");
            anyhow::bail!("worker did not start");
        }
    };
    println!("[ok] Worker ready in {:.2}s (Blender {}, Python {})",
             t0.elapsed().as_secs_f32(), ready.blender, ready.python);

    if let Ok(reply) = worker.call("sys_info", json!({}), CallOpts::read_only(Duration::from_secs(30))).await {
        report_sys_info(&reply.result);
    }

    // Time a preview round-trip.
    let _ = worker.call("scene_new", json!({}), CallOpts::mutating(Duration::from_secs(30))).await;
    let _ = worker.call("add_primitive", json!({"type": "monkey"}), CallOpts::mutating(Duration::from_secs(30))).await;
    let tp = Instant::now();
    match worker.call("render_preview", json!({"size": 256, "views": ["front"]}),
                      CallOpts::read_only(Duration::from_secs(120))).await {
        Ok(_) => println!("[ok] render_preview (256px) in {:.2}s", tp.elapsed().as_secs_f32()),
        Err(e) => println!("[!]  render_preview failed: {e:?}"),
    }

    let exe = std::env::current_exe()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| "ghostblend".to_string());
    println!("\nRegister with Claude Code:");
    println!("  claude mcp add ghostblend -- \"{exe}\"");
    println!("\nAll checks completed.");
    Ok(())
}

fn report_sys_info(info: &Value) {
    if let Some(engines) = info.get("engines").and_then(Value::as_array) {
        let names: Vec<&str> = engines.iter().filter_map(Value::as_str).collect();
        println!("[ok] Render engines: {}", names.join(", "));
    }
    if let Some(devices) = info.get("cycles_devices").and_then(Value::as_array) {
        let mut gpu = Vec::new();
        for d in devices {
            if let (Some(name), Some(ty)) = (
                d.get("name").and_then(Value::as_str),
                d.get("type").and_then(Value::as_str),
            ) && ty != "CPU"
            {
                gpu.push(format!("{name} ({ty})"));
            }
        }
        if gpu.is_empty() {
            println!("[ok] Cycles: CPU only");
        } else {
            println!("[ok] Cycles GPU: {}", gpu.join(", "));
        }
    }
}
