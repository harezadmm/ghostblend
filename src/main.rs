use std::io::Write;
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use clap::{Args, Parser, Subcommand};
use rmcp::ServiceExt;
use rmcp::transport::io::stdio;

use ghostblend::config;
use ghostblend::jobs::JobManager;
use ghostblend::runtime::{self, Resolution, RuntimeMode};
use ghostblend::server::GhostServer;
use ghostblend::session::Session;
use ghostblend::worker::{Worker, WorkerConfig};

#[derive(Parser, Debug)]
#[command(
    name = "ghostblend",
    version,
    about = "Headless Blender for AI agents, with a built-in MCP server on stdio. \
             Ghostblend brings its own Blender engine; no separate Blender install is needed."
)]
struct Cli {
    #[command(subcommand)]
    command: Option<Cmd>,
    #[command(flatten)]
    opts: Opts,
}

#[derive(Subcommand, Debug, Clone, Copy)]
enum Cmd {
    /// Run the MCP server on stdin/stdout (the default when no command is given).
    Serve,
    /// Download and install Ghostblend's own Blender engine now (one time, about 414 MB).
    Setup,
    /// Check the engine and run a short self-test.
    Doctor,
}

#[derive(Args, Debug, Clone)]
struct Opts {
    /// Use this Blender executable instead of Ghostblend's engine (or set GHOSTBLEND_BLENDER).
    #[arg(long, global = true, value_name = "PATH")]
    blender: Option<PathBuf>,
    /// Where the engine comes from: auto (own engine, else an installed Blender, else download),
    /// managed (always Ghostblend's own engine), or system (only an installed Blender).
    #[arg(long, global = true, value_enum, default_value_t = RuntimeMode::Auto)]
    runtime: RuntimeMode,
    /// Never download the engine automatically; use `ghostblend setup` instead.
    #[arg(long, global = true)]
    no_download: bool,
    /// Use this exact directory for the session (autosaves, previews, renders).
    #[arg(long, global = true, value_name = "DIR")]
    session_dir: Option<PathBuf>,
    /// Resume an earlier session by id and restore its latest autosave.
    #[arg(long, global = true, value_name = "ID")]
    resume: Option<String>,
    /// Delete the session directory when the server exits.
    #[arg(long, global = true)]
    ephemeral: bool,
    /// Base directory for relative paths. Default: the current directory.
    #[arg(long, global = true, value_name = "DIR")]
    workdir: Option<PathBuf>,
    /// Number of autosaves kept per session.
    #[arg(long, global = true, default_value_t = 10, value_name = "N",
          value_parser = clap::value_parser!(u32).range(1..=1000))]
    autosave_keep: u32,
    /// Maximum number of render jobs running at the same time.
    #[arg(long, global = true, default_value_t = 2, value_name = "N",
          value_parser = clap::value_parser!(u64).range(1..=16))]
    max_jobs: u64,
    /// Write logs to this file instead of stderr.
    #[arg(long, global = true, value_name = "FILE")]
    log_file: Option<PathBuf>,
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    init_logging(cli.opts.log_file.as_deref());
    let cfg = match build_config(&cli.opts) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("ghostblend: {e:#}");
            return ExitCode::from(2);
        }
    };
    let rt = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("failed to start the tokio runtime");
    let command = cli.command.unwrap_or(Cmd::Serve);
    let outcome = rt.block_on(async move {
        match command {
            Cmd::Serve => serve(cfg).await,
            Cmd::Setup => setup(cfg).await,
            Cmd::Doctor => ghostblend::doctor::run(cfg).await,
        }
    });
    match outcome {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("ghostblend: {e:#}");
            ExitCode::FAILURE
        }
    }
}

fn init_logging(log_file: Option<&std::path::Path>) {
    use tracing_subscriber::{EnvFilter, fmt};
    let filter = EnvFilter::try_from_env("GHOSTBLEND_LOG")
        .or_else(|_| EnvFilter::try_from_default_env())
        .unwrap_or_else(|_| EnvFilter::new("warn"));
    let builder = fmt().with_env_filter(filter).with_writer(std::io::stderr);
    if let Some(path) = log_file
        && let Ok(file) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
            fmt()
                .with_env_filter(
                    EnvFilter::try_from_env("GHOSTBLEND_LOG").unwrap_or_else(|_| EnvFilter::new("info")),
                )
                .with_writer(std::sync::Mutex::new(file))
                .with_ansi(false)
                .init();
            return;
        }
    builder.init();
}

async fn serve(cfg: config::Config) -> anyhow::Result<()> {
    let session = Session::open(cfg.session_dir.as_deref(), cfg.resume.as_deref(), cfg.ephemeral)?;
    tracing::info!(session = %session.dir.display(), "ghostblend session");

    // Never refuse to start. If the engine is not there yet, this kicks off its
    // setup in the background; tools report progress until it is ready.
    let engine = runtime::from_config(&cfg, cfg.runtime_mode, cfg.allow_download);
    match engine.resolve() {
        Resolution::Ready { path, source } => tracing::info!(engine = %path.display(), %source, "engine ready"),
        Resolution::Preparing(m) => tracing::info!("{m}"),
        Resolution::Unavailable(m) => tracing::warn!("{m}"),
    }

    let wcfg = WorkerConfig {
        runtime: engine.clone(),
        session_dir: session.dir.clone(),
        run_entry: session.run_entry(),
        workdir: cfg.workdir.clone(),
        autosave_keep: cfg.autosave_keep,
        ready_timeout: Duration::from_secs(90),
    };
    let worker = Arc::new(Worker::new(wcfg));
    let jobs = Arc::new(JobManager::new(engine, session.render_entry(), cfg.max_jobs));
    let server = GhostServer::new(worker.clone(), jobs, cfg.workdir.clone());

    let running = server.serve(stdio()).await?;
    let reason = running.waiting().await?;
    tracing::info!(?reason, "ghostblend server stopped");
    Ok(())
}

/// Install Ghostblend's own engine now, with a progress line.
async fn setup(cfg: config::Config) -> anyhow::Result<()> {
    let engine = runtime::from_config(&cfg, RuntimeMode::Managed, true);
    if let Some(p) = engine.managed_exe() {
        println!("Ghostblend's engine is already installed (Blender {}):\n  {}", runtime::ENGINE_VERSION, p.display());
        return Ok(());
    }
    if runtime::host_artifact().is_none() {
        anyhow::bail!("automatic engine setup is not available on this platform yet; install Blender 4.2+ and pass --blender");
    }
    println!(
        "Installing Ghostblend's engine: Blender {} (about 414 MB) from download.blender.org into\n  {}",
        runtime::ENGINE_VERSION,
        engine.engine_dir().display()
    );
    let ticker_engine = engine.clone();
    let ticker = tokio::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_millis(500)).await;
            print!("\r  {:<60}", ticker_engine.status_line());
            let _ = std::io::stdout().flush();
        }
    });
    let outcome = engine.provision().await;
    ticker.abort();
    match outcome {
        Ok(p) => {
            println!("\r  {:<60}\nDone. Engine installed at:\n  {}", "installed and verified (SHA-256)", p.display());
            Ok(())
        }
        Err(e) => {
            println!();
            anyhow::bail!("engine setup failed: {e}. Run `ghostblend setup` again to resume.")
        }
    }
}

fn build_config(o: &Opts) -> anyhow::Result<config::Config> {
    let cwd = std::env::current_dir()?;
    let workdir = match &o.workdir {
        Some(w) if w.is_absolute() => w.clone(),
        Some(w) => cwd.join(w),
        None => cwd,
    };
    Ok(config::Config {
        blender: o.blender.clone(),
        session_dir: o.session_dir.clone(),
        resume: o.resume.clone(),
        ephemeral: o.ephemeral,
        workdir,
        autosave_keep: o.autosave_keep,
        max_jobs: o.max_jobs as usize,
        runtime_mode: o.runtime,
        allow_download: !o.no_download,
    })
}
