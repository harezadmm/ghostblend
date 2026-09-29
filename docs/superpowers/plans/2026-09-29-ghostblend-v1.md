# Ghostblend v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One Rust binary, `ghostblend`, that is an MCP server over stdio and supervises a persistent headless Blender worker, exposing the 25 tools in the spec.

**Architecture:** Rust (`rmcp` 3.5, tokio) owns MCP, schemas, validation, process lifecycle, timeouts, restarts, render jobs, and image encoding. A Python bridge embedded in the binary runs inside `blender -b`, reads JSON requests from stdin on a reader thread, executes them with `bpy` on the main thread, and answers on a dedicated protocol file descriptor so stray output can never corrupt framing. Every mutating command autosaves; failed mutating commands roll back.

**Tech Stack:** Rust 1.97 (x86_64-pc-windows-msvc), rmcp 3.5.0 (`server`, `transport-io`), tokio, clap, serde_json (`preserve_order`), tracing, base64, dirs, which, regex, uuid. Blender 5.1.1 / Python 3.13.9 for tests; target Blender 4.2+.

**Spec:** `docs/superpowers/specs/2026-09-29-ghostblend-design.md`

**Execution mode:** native, by the same session that wrote the spec (the user asked for a single approval gate). The plan pins interfaces, file responsibilities, tests, and acceptance commands; implementation code goes straight into the repo files named in each task. One fresh reviewer checks the whole tree at the end.

## Global Constraints

- Binary and crate name: `ghostblend`; product name in prose: Ghostblend.
- Env var for the Blender path: `GHOSTBLEND_BLENDER`.
- Session root: `dirs::data_local_dir()/ghostblend/s/<8-char id>` (Windows: `%LOCALAPPDATA%\ghostblend\s\<id>`). Keep it short: Blender fails to open scripts at paths beyond about 250 characters on Windows.
- Worker command: `blender -b --factory-startup -noaudio --python <session>/bridge/run.py -- --session <session> --workdir <dir> [--restore-latest]`.
- On Windows use `blender.exe`, never `blender-launcher.exe`; spawn every Blender child with `CREATE_NO_WINDOW` (0x08000000).
- Protocol marker on the worker's protocol stream: `@@bhm:`; render job result marker: `@@gbjob:`.
- Units: meters, rotations in degrees, colors RGBA floats 0..1.
- Timeouts: default 120 s; `render_preview` 300 s; `run_python` soft interrupt at `timeout_s` (default 60, max 600) and hard kill 10 s later; `import_model`/`export_model`/`scene_open`/`validate` 600 s; worker ready 90 s.
- Autosave ring: 10 files, `autosave/NNNNNN.blend` plus `autosave/index.jsonl`.
- `render` jobs: at most 2 running at once; `wait=true` blocks at most 240 s.
- Logs go to stderr only; stdout is the MCP channel.
- Supported Blender: 4.2+, tested on 5.1.1.
- No commits unless the user asks (harness rule); work stays in the working tree.

## Review Focus

1. **Paths with spaces, non-ASCII characters, and relative paths.** The user's own project folder is `CODE PROJECT`. Save, open, import, and export must work there, and relative paths must resolve against the directory ghostblend was launched from. Pinned by bridge test `test_paths_unicode_spaces` (Task 5) and the E2E relative export check (Task 8).
2. **Agent code that prints protocol-looking text or floods output.** Framing must stay intact and output must be truncated with a marker, not crash the session. Pinned by bridge tests `test_run_python_fake_marker` and `test_run_python_output_cap` (Task 2).
3. **Hangs and crashes inside Blender.** A pure-Python loop is interrupted softly and the worker survives; a C-level hang or a crash triggers kill, restart, and restore from the latest autosave, and the next call works. Pinned by worker integration tests `soft_interrupt`, `hard_timeout_restarts`, `crash_restarts` (Task 3).
4. **Parallel tool calls from one client.** Calls are serialized to the single worker and every response carries the right result. Pinned by E2E `parallel_calls` (Task 4).
5. **ghostblend killed abruptly.** No orphan `blender.exe` may survive. Pinned by E2E `no_orphans_after_kill` (Task 8): the bridge's stdin reader calls `os._exit(0)` on EOF, and render jobs do the same.

---

## File Structure

```
Cargo.toml
src/main.rs            CLI (clap): serve (default), doctor; logging; runtime
src/config.rs          Config from CLI + env; path resolution helpers
src/discover.rs        find Blender executable, parse version
src/session.rs         session dir create/resume, write embedded bridge files
src/bridge_files.rs    include_str! table of bridge/*.py
src/worker/mod.rs      Worker supervisor: lazy start, serialized calls, timeouts, restart
src/worker/proto.rs    request/response/event types, marker parsing
src/worker/process.rs  one live Blender child: spawn, readers, pending map, stderr ring
src/jobs.rs            render job manager + progress parsing
src/tools/mod.rs       ToolSpec, registry, dispatch kinds
src/tools/schema.rs    schema builder helpers + mini validator
src/tools/defs.rs      the 25 tool definitions
src/server.rs          rmcp ServerHandler
src/doctor.rs          `ghostblend doctor`
bridge/run.py          Blender entry: sys.path, gb.main.main()
bridge/render_job.py   entry for render job processes
bridge/gb/main.py      loop, reader thread, dispatch, responses, interrupts
bridge/gb/util.py      JSON encoding, rounding, paths, lookups, RNA assignment
bridge/gb/history.py   autosave ring, restore, checkpoints
bridge/gb/scene.py     scene_new/open/save/info, object_info, summaries
bridge/gb/objects.py   add_primitive, transform, object_delete, object_duplicate
bridge/gb/modifiers.py modifier_add, modifier_apply
bridge/gb/materials.py material_set
bridge/gb/model_io.py  import_model, export_model
bridge/gb/framing.py   bbox and camera framing math
bridge/gb/font.py      5x7 bitmap font for contact-sheet labels
bridge/gb/preview.py   render_preview
bridge/gb/validate.py  validate
bridge/gb/pyexec.py    run_python
bridge/gb/api.py       api_describe, api_search
bridge/gb/render.py    render_prepare, sys_info
tests/bridge/run_tests.py + test_*.py   unittest suite executed inside Blender
tests/worker_integration.rs             real-Blender supervisor tests (skip without Blender)
tests/e2e/mcp_e2e.py                    JSON-RPC over stdio against the built binary
README.md
```

## Interfaces

**Rust → bridge request (one JSON line on stdin):**
`{"id": u64, "cmd": str, "args": object, "autosave": bool, "atomic": bool}`

**Rust → bridge control (one JSON line on stdin, handled by the reader thread):**
`{"control": "interrupt", "id": u64}`

**Bridge → Rust response (protocol fd, one line, `\n@@bhm:` prefix):**
`{"id": u64, "ok": true, "result": object, "autosave": {"seq": u64, "path": str}?, "ms": u64}`
`{"id": u64, "ok": false, "error": {"type": str, "message": str, "hint": str?, "traceback": str?}, "rolled_back": bool, "ms": u64}`

**Bridge events:** `{"event": "ready", "blender": str, "python": str, "pid": u64, "restored": {"seq": u64, "path": str}?}`, `{"event": "log", "level": str, "msg": str}`.

**Rust worker API:**
```rust
pub struct CallOpts { pub autosave: bool, pub atomic: bool, pub timeout: Duration, pub soft_interrupt_after: Option<Duration> }
pub struct Reply { pub result: Value, pub autosave_seq: Option<u64>, pub notes: Vec<String>, pub ms: u64 }
pub enum CallError { Blender { error: Value, rolled_back: bool, notes: Vec<String> }, Unavailable(String), Timeout { notes: Vec<String> }, Crashed { notes: Vec<String> } }
impl Worker { pub async fn call(&self, cmd: &str, args: Value, opts: CallOpts) -> Result<Reply, CallError> }
```

**Tool registry:**
```rust
pub enum Kind { Worker(&'static str), Preview, Render, JobStatus, JobCancel }
pub struct ToolSpec { pub name: &'static str, pub title: &'static str, pub description: &'static str, pub schema: Value, pub mutating: bool, pub read_only: bool, pub timeout: Duration, pub kind: Kind }
pub fn registry() -> Vec<ToolSpec>
pub fn validate(schema: &Value, args: &Value) -> Result<(), Vec<String>>
```

**Bridge command table:** `gb.main.COMMANDS: dict[str, Callable[[dict], dict]]`, one entry per worker command: the 21 worker-backed tools plus `render_prepare`, `sys_info`, `ping`.

---

### Task 1: Crate scaffold, CLI, Blender discovery

**Files:** Create `Cargo.toml`, `src/main.rs`, `src/config.rs`, `src/discover.rs`, `.gitignore`.

- [ ] Write unit tests in `src/discover.rs`: highest `Blender X.Y` folder wins (`4.5` vs `5.1` vs `3.6`, including `10.0` beating `9.9`); `blender-launcher.exe` maps to sibling `blender.exe`; an explicit path that does not exist yields an error naming the path; version parsing of `Blender 5.1.1 (hash b70da489d7f4 ...)`.
- [ ] Run `cargo test discover` and see the tests fail to compile.
- [ ] Implement `find_blender(explicit: Option<&Path>) -> Result<Found, DiscoverError>` with the spec's search order (flag, env, PATH, Program Files, Steam, macOS app bundle, Linux paths) and `blender_version(path) -> Option<String>` running `--version` with `CREATE_NO_WINDOW`.
- [ ] Run `cargo test discover`: all pass.
- [ ] CLI: `ghostblend [serve]` flags `--blender --session-dir --resume --ephemeral --workdir --autosave-keep --max-jobs --log-file`; `ghostblend doctor`. `cargo run -- --help` prints both.

### Task 2: Bridge core (loop, history, scene tools, run_python)

**Files:** Create `bridge/run.py`, `bridge/gb/{__init__,main,util,history,scene,pyexec}.py`, `tests/bridge/run_tests.py`, `tests/bridge/test_core.py`.

- [ ] Tests (run inside Blender via `run_tests.py`, which imports `gb` from `bridge/`): `dispatch` of unknown command returns `ok:false` with the list of known commands; `scene_new` gives zero objects; `scene_info` lists objects with rounded transforms; autosave writes `000001.blend` and `index.jsonl`, keeps only 10; `restore(steps=1)` returns to the previous state; a failing mutating command with `atomic` rolls back; `run_python` returns `result`, captured `stdout`, and a traceback that points at the user's line; `test_run_python_fake_marker` prints `@@bhm:{"id":1}` and it comes back inside `stdout`; `test_run_python_output_cap` prints 1 MB and gets a truncated string with a marker; interrupt via `request_interrupt()` stops `while True: pass` with a `GhostInterrupt` error.
- [ ] Run `blender -b --factory-startup --python tests/bridge/run_tests.py`; tests fail.
- [ ] Implement: protocol fd = `os.dup(1)`, then `os.dup2(2, 1)` so C-level prints go to stderr; `sys.stdout = sys.stderr` outside commands; reader thread parses lines, handles `control:interrupt` via `ctypes.pythonapi.PyThreadState_SetAsyncExc(main_ident, GhostInterrupt)` only while the matching request is interruptible, and calls `os._exit(0)` on stdin EOF; main thread pops requests from a queue.
- [ ] Run the suite: all pass.

### Task 3: Rust worker supervisor

**Files:** Create `src/worker/{mod,proto,process}.rs`, `src/session.rs`, `src/bridge_files.rs`, `tests/worker_integration.rs`.

- [ ] Unit tests in `proto.rs`: marker found mid-line after noise; non-marker lines ignored; malformed JSON after the marker reported as a protocol error, not a panic; response and event variants deserialize.
- [ ] Integration tests (skip with a printed notice when no Blender is found): `start_and_ping`; `scene_roundtrip` (scene_new, add a cube through run_python, scene_info shows it); `soft_interrupt` (`while True: pass`, timeout 2 s, error mentions the interrupt, next call works in under 5 s); `hard_timeout_restarts` (`time.sleep(120)`, timeout 2 s, restart note present, earlier cube still there); `crash_restarts` (`os._exit(3)`, crash note present, state restored); `stderr_flood` (write 5 MB to `sys.stderr`, call completes).
- [ ] Implement `Worker` with a tokio `Mutex<Option<WorkerProcess>>`, lazy start, `call()` with soft interrupt then hard kill, eager restart with `--restore-latest`, and a 200-line stderr ring used in crash notes.
- [ ] `cargo test` (unit) and `cargo test --test worker_integration -- --nocapture` (needs Blender): all pass.

### Task 4: Tool registry, validator, MCP server, first E2E

**Files:** Create `src/tools/{mod,schema,defs}.rs`, `src/server.rs`, `tests/e2e/mcp_e2e.py`.

- [ ] Unit tests: validator rejects a missing required field, a wrong type, an out-of-range number, a vector of the wrong length, an unknown field (message names the field), and an enum miss (message lists allowed values); registry has exactly 25 tools, unique names, every schema is `type: object` with `additionalProperties: false`, every property has a description.
- [ ] Implement the registry for all 25 tools (schemas complete now, even if later tasks add the bridge side) and `GhostServer` (`get_info`, `list_tools`, `call_tool`).
- [ ] E2E (`python tests/e2e/mcp_e2e.py --binary target/debug/ghostblend.exe`): initialize, `tools/list` has 25 tools, `scene_new`, `run_python` returns a result, validation error comes back as `isError`, `parallel_calls` sends two `tools/call` requests without waiting and checks both, and `--blender Z:\nope\blender.exe` still lists tools while calls return an actionable error.

### Task 5: Objects, modifiers, materials, import/export

**Files:** Create `bridge/gb/{objects,modifiers,materials,model_io}.py`, `tests/bridge/test_build.py`.

- [ ] Tests: every primitive type creates the right object type and name; `look_at` points a camera's -Z at the target within 1e-4; transform `delta` and `apply`; `object_delete` with one missing name deletes nothing and names the missing one; duplicate linked shares mesh data, unlinked does not; `modifier_add` bevel with `width` and boolean with `object` given by name; unknown modifier param returns the list of valid params; `modifier_apply` changes the vertex count; `material_set` sets Principled inputs and the viewport color; import/export roundtrip for glb, obj, stl, fbx, ply keeps the vertex count; `test_paths_unicode_spaces` saves, exports, and re-imports under `tmp/Folder Ünïcode ñ/`.
- [ ] Implement, then run the bridge suite: all pass.

### Task 6: render_preview

**Files:** Create `bridge/gb/{framing,font,preview}.py`, `tests/bridge/test_preview.py`; Rust `Kind::Preview` wiring.

- [ ] Tests: default call returns a PNG of 768x768 with four labeled tiles; a single `front` view returns one tile; the scene's engine, camera, resolution, and object count are unchanged afterwards; an empty scene still returns an image and a note; `shading=rendered` works on EEVEE and Cycles CPU.
- [ ] Implement with Workbench for `solid`/`textured`, snapshot-and-restore of every touched setting, temporary cameras removed afterwards.
- [ ] E2E: `render_preview` returns image content whose base64 decodes to a PNG signature; measure the round trip.

### Task 7: validate, api_describe, api_search

**Files:** Create `bridge/gb/{validate,api}.py`, `tests/bridge/test_inspect.py`.

- [ ] Tests: a cube is clean; a plane reports boundary edges; a mesh with a duplicated vertex reports doubles; flipped face reports inconsistent normals; negative scale warns; `api_describe("bpy.ops.mesh.primitive_cube_add")` lists `size`; `api_describe("bpy.types.BevelModifier")` lists `width`; `api_search("bevel")` finds `bpy.ops.mesh.bevel` and `bpy.types.BevelModifier`.
- [ ] Implement, run the suite: all pass.

### Task 8: Render jobs

**Files:** Create `src/jobs.rs`, `bridge/gb/render.py`, `bridge/render_job.py`, `tests/bridge/test_render.py`.

- [ ] Unit tests (`jobs.rs`): progress parsing for Cycles `Sample 12/64`, EEVEE `Rendering 3 / 16 samples`, `Fra:5`, and `Saved: 'C:\out\a.png'`.
- [ ] Implement `render_prepare` (snapshot to the job dir), `render_job.py` (overrides, auto camera and light when missing, still or animation, thumbnail, `@@gbjob:` result, stdin-EOF watchdog), and `JobManager` (queue, max 2 running, cancel, wait up to 240 s).
- [ ] E2E: `render` with `wait=true` at 320x240 finishes with an output file and a thumbnail image; relative `output_path` lands under the launch directory; `job_cancel` stops a long render; `no_orphans_after_kill` kills ghostblend and checks the worker PID is gone within 5 s.

### Task 9: Checkpoints, doctor, README, release build

**Files:** Modify `bridge/gb/history.py`; create `src/doctor.rs`, `README.md`.

- [ ] Tests: `checkpoint_save` then changes then `checkpoint_restore` returns the saved state; `checkpoint_list` shows the checkpoint and recent autosaves with their commands.
- [ ] `ghostblend doctor` prints Blender path and version, worker ready time, engines, Cycles devices, preview time, session root, and the `claude mcp add` line; exits non-zero on failure.
- [ ] `cargo build --release`, `cargo test`, bridge suite, integration tests, and E2E against the release binary all pass; README covers install, registration, tools, and troubleshooting.
- [ ] Final whole-tree review by a fresh reviewer; fix findings.
