//! The MCP server: exposes the tool catalogue and dispatches calls to the
//! worker (and, for renders, the job manager).

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use base64::Engine;
use rmcp::ServerHandler;
use rmcp::model::{
    CallToolRequestParams, CallToolResponse, CallToolResult, ContentBlock as Content, Implementation,
    ListToolsResult, PaginatedRequestParams, ProtocolVersion, ServerCapabilities, ServerConfig, Tool,
    ToolAnnotations,
};
use rmcp::service::{RequestContext, RoleServer};
use rmcp::{ErrorData as McpError, model::ServerResult};
use serde_json::{Value, json};

use crate::jobs::JobManager;
use crate::tools::{Kind, ToolSpec, registry};
use crate::worker::{CallError, CallOpts, Reply, Worker};

const INSTRUCTIONS: &str = "Ghostblend drives a headless Blender in the background. \
Call scene_info first to see what exists, build with the typed tools, and call render_preview \
to see your work as an image. Every scene-changing command is autosaved; use checkpoint_restore \
to undo. run_python is the escape hatch for anything else.";

pub struct GhostServer {
    worker: Arc<Worker>,
    jobs: Arc<JobManager>,
    workdir: PathBuf,
    specs: HashMap<&'static str, ToolSpec>,
    order: Vec<&'static str>,
}

impl GhostServer {
    pub fn new(worker: Arc<Worker>, jobs: Arc<JobManager>, workdir: PathBuf) -> Self {
        let reg = registry();
        let order = reg.iter().map(|t| t.name).collect();
        let specs = reg.into_iter().map(|t| (t.name, t)).collect();
        Self { worker, jobs, workdir, specs, order }
    }

    fn tools(&self) -> Vec<Tool> {
        self.order
            .iter()
            .map(|name| {
                let spec = &self.specs[name];
                let schema = spec
                    .schema
                    .as_object()
                    .cloned()
                    .unwrap_or_default();
                let annotations = ToolAnnotations::new()
                    .read_only(spec.read_only)
                    .destructive(spec.mutating);
                Tool::new(spec.name, spec.description, Arc::new(schema))
                    .with_title(spec.title)
                    .annotate(annotations)
            })
            .collect()
    }

    async fn dispatch(&self, name: &str, args: Value) -> CallToolResult {
        let Some(spec) = self.specs.get(name) else {
            return CallToolResult::error(vec![Content::text(format!("Unknown tool '{name}'"))]);
        };
        if let Err(problems) = spec.validate(&args) {
            let msg = format!(
                "Invalid arguments for {name}:\n- {}",
                problems.join("\n- ")
            );
            return CallToolResult::error(vec![Content::text(msg)]);
        }
        match &spec.kind {
            Kind::Worker(cmd) => self.call_worker(cmd, args, spec).await,
            Kind::Preview => self.call_preview(args, spec).await,
            Kind::Render => self.call_render(args).await,
            Kind::JobStatus => self.call_job_status(args),
            Kind::JobCancel => self.call_job_cancel(args).await,
        }
    }

    async fn call_render(&self, mut args: Value) -> CallToolResult {
        let output = args.get("output_path").and_then(Value::as_str).unwrap_or_default();
        let abs = crate::util::resolve_path(&self.workdir, output);
        let job_id = self.jobs.new_job_id();

        // Snapshot the current scene through the worker so the render matches it.
        let prep = self
            .worker
            .call("render_prepare", json!({ "job_id": job_id }), CallOpts::read_only(Duration::from_secs(120)))
            .await;
        let scene = match prep {
            Ok(reply) => match reply.result.get("scene").and_then(Value::as_str) {
                Some(s) => PathBuf::from(s),
                None => return CallToolResult::error(vec![Content::text("Could not prepare the scene for rendering.")]),
            },
            Err(e) => return failure(e),
        };

        if let Some(obj) = args.as_object_mut() {
            obj.insert("output_path".into(), json!(abs.to_string_lossy()));
        }
        let wait = args.get("wait").and_then(Value::as_bool).unwrap_or(false);
        let job = self.jobs.start(job_id.clone(), scene, args);

        if wait {
            let status = self.jobs.wait(&job_id, Duration::from_secs(240)).await;
            match status {
                Some(v) => job_status_result(v, Some(job.as_ref()), true),
                None => CallToolResult::error(vec![Content::text("Job vanished unexpectedly.")]),
            }
        } else {
            CallToolResult::success(vec![Content::text(format!(
                "Render job {job_id} started. Poll job_status with this id.\n{}",
                pretty(&job.snapshot(false))
            ))])
        }
    }

    fn call_job_status(&self, args: Value) -> CallToolResult {
        let id = args.get("job_id").and_then(Value::as_str).unwrap_or_default();
        let include = args.get("include_image").and_then(Value::as_bool).unwrap_or(false);
        match self.jobs.status(id, include) {
            Some(v) => {
                let job = self.jobs.get(id);
                job_status_result(v, job.as_deref(), include)
            }
            None => CallToolResult::error(vec![Content::text(format!("No render job with id '{id}'."))]),
        }
    }

    async fn call_job_cancel(&self, args: Value) -> CallToolResult {
        let id = args.get("job_id").and_then(Value::as_str).unwrap_or_default().to_string();
        match self.jobs.cancel(&id).await {
            Some(v) => CallToolResult::success(vec![Content::text(pretty(&v))]),
            None => CallToolResult::error(vec![Content::text("No such render job.")]),
        }
    }

    fn opts(&self, spec: &ToolSpec) -> CallOpts {
        if spec.mutating {
            CallOpts::mutating(spec.timeout)
        } else {
            CallOpts::read_only(spec.timeout)
        }
    }

    async fn call_worker(&self, cmd: &str, args: Value, spec: &ToolSpec) -> CallToolResult {
        let mut opts = self.opts(spec);
        if cmd == "run_python" {
            let secs = args
                .get("timeout_s")
                .and_then(Value::as_u64)
                .unwrap_or(60)
                .clamp(1, 600);
            opts.timeout = std::time::Duration::from_secs(secs + 10);
            opts.soft_interrupt_after = Some(std::time::Duration::from_secs(secs));
        }
        match self.worker.call(cmd, args, opts).await {
            Ok(reply) => success(reply),
            Err(e) => failure(e),
        }
    }

    async fn call_preview(&self, args: Value, spec: &ToolSpec) -> CallToolResult {
        match self.worker.call("render_preview", args, self.opts(spec)).await {
            Ok(reply) => preview_result(reply),
            Err(e) => failure(e),
        }
    }
}

fn success(reply: Reply) -> CallToolResult {
    let mut content = Vec::new();
    if !reply.notes.is_empty() {
        content.push(Content::text(format!("Note: {}", reply.notes.join("\n"))));
    }
    content.push(Content::text(pretty(&reply.result)));
    let mut result = CallToolResult::success(content);
    result.structured_content = Some(reply.result);
    result
}

fn preview_result(reply: Reply) -> CallToolResult {
    let mut content = Vec::new();
    let mut images = Vec::new();
    if let Some(arr) = reply.result.get("images").and_then(Value::as_array) {
        for p in arr.iter().filter_map(Value::as_str) {
            images.push(p.to_string());
        }
    }
    if let Some(p) = reply.result.get("image").and_then(Value::as_str) {
        images.push(p.to_string());
    }
    for path in &images {
        match std::fs::read(path) {
            Ok(bytes) => {
                let b64 = base64::engine::general_purpose::STANDARD.encode(&bytes);
                content.push(Content::image(b64, "image/png"));
            }
            Err(e) => content.push(Content::text(format!("Could not read preview {path}: {e}"))),
        }
    }
    // A trimmed copy of the metadata (without the paths we already rendered).
    let mut meta = reply.result.clone();
    if let Some(obj) = meta.as_object_mut() {
        obj.remove("images");
        obj.remove("image");
    }
    if !reply.notes.is_empty() {
        content.push(Content::text(format!("Note: {}", reply.notes.join("\n"))));
    }
    content.push(Content::text(pretty(&meta)));
    if content.is_empty() {
        content.push(Content::text("No image was produced."));
    }
    CallToolResult::success(content)
}

fn job_status_result(status: Value, job: Option<&crate::jobs::Job>, include_image: bool) -> CallToolResult {
    let mut content = Vec::new();
    if include_image && status.get("state").and_then(Value::as_str) == Some("done")
        && let Some(path) = job.and_then(|j| j.first_output_path()) {
            match std::fs::read(&path) {
                Ok(bytes) if looks_like_image(&path) => {
                    let b64 = base64::engine::general_purpose::STANDARD.encode(&bytes);
                    content.push(Content::image(b64, mime_for(&path)));
                }
                _ => {}
            }
        }
    content.push(Content::text(pretty(&status)));
    CallToolResult::success(content)
}

fn looks_like_image(path: &str) -> bool {
    let p = path.to_lowercase();
    p.ends_with(".png") || p.ends_with(".jpg") || p.ends_with(".jpeg")
}

fn mime_for(path: &str) -> &'static str {
    let p = path.to_lowercase();
    if p.ends_with(".jpg") || p.ends_with(".jpeg") { "image/jpeg" } else { "image/png" }
}

fn failure(err: CallError) -> CallToolResult {
    let text = match err {
        CallError::Blender { error, rolled_back, notes } => {
            let mut t = crate::worker::proto::error_message(&error);
            if rolled_back {
                t.push_str("\n(The scene was rolled back to before this command.)");
            }
            if !notes.is_empty() {
                t.push_str(&format!("\n{}", notes.join("\n")));
            }
            t
        }
        CallError::Unavailable(m) => format!("Blender is unavailable: {m}"),
        CallError::Preparing(m) => m,
        CallError::Timeout { notes } => format!("The command timed out.\n{}", notes.join("\n")),
        CallError::Crashed { notes } => format!("Blender stopped unexpectedly.\n{}", notes.join("\n")),
    };
    CallToolResult::error(vec![Content::text(text)])
}

fn pretty(v: &Value) -> String {
    serde_json::to_string_pretty(v).unwrap_or_else(|_| v.to_string())
}

impl ServerHandler for GhostServer {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(ServerCapabilities::builder().enable_tools().build())
            .with_protocol_version(ProtocolVersion::LATEST)
            .with_server_info(Implementation::new("ghostblend", env!("CARGO_PKG_VERSION")))
            .with_instructions(INSTRUCTIONS)
    }

    async fn list_tools(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListToolsResult, McpError> {
        Ok(ListToolsResult::with_all_items(self.tools()))
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        _context: RequestContext<RoleServer>,
    ) -> Result<CallToolResponse, McpError> {
        let name = request.name.to_string();
        let args = request.arguments.map(Value::Object).unwrap_or(json!({}));
        let result = self.dispatch(&name, args).await;
        Ok(result.into())
    }
}

/// Convenience for `ServerResult` in tests.
pub fn tool_names() -> Vec<&'static str> {
    registry().into_iter().map(|t| t.name).collect()
}

// Keep the import used even if only exercised in tests.
#[allow(unused_imports)]
use ServerResult as _ServerResult;
