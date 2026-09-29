# Ghostblend

**Headless Blender for AI agents, with a built-in MCP server.**

[Bahasa Indonesia](README.id.md)

![Four views of a scene an agent built through Ghostblend, returned as one image](docs/images/preview-sheet.png)

*An agent built this scene with ten tool calls and got these four views back as a
single image in 1.5 seconds. Nothing opened on screen.*

Ghostblend is one executable. It speaks the [Model Context Protocol](https://modelcontextprotocol.io)
on stdio, and behind it runs a full Blender with no window. Your AI agent gets
typed tools to build scenes, edit meshes, assign materials, import and export
models, and render, and it sees its work as images. You do not need to install
Blender, open it, or add an add-on.

---

## Contents

- [Why it exists](#why-it-exists)
- [What an agent can do](#what-an-agent-can-do)
- [Quick start](#quick-start)
- [Tool reference](#tool-reference)
- [How it works](#how-it-works)
- [The Blender engine](#the-blender-engine)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Performance](#performance)
- [Security](#security)
- [Development](#development)
- [Status and limitations](#status-and-limitations)

---

## Why it exists

### The idea

People who build with AI agents often need what Blender can *do*: modelling,
modifiers, materials, format conversion, mesh repair, rendering. They do not
need Blender's interface. An agent cannot use a GUI anyway. So the idea is simple:
run Blender headless in the background and connect it to the agent over MCP,
the standard way agents talk to tools.

### What was missing

When this project started, in September 2026, the options for connecting an
agent to Blender looked like this:

| Option | How it works | What that means for an agent |
|---|---|---|
| MCP for Blender, the most popular community server | An add-on inside a running Blender window, reached over a local socket | Someone has to install the add-on and keep Blender open on screen |
| The official Blender Lab MCP server | An add-on inside a running Blender window, plus a relay process; needs Blender 5.1+ | Same as above. Its six headless `_for_cli` tools start a fresh Blender for every call, so nothing carries over between calls and each call pays Blender's start-up time |
| Small headless servers | Run a Python string in `blender -b`, write a file | The agent is blind, there is no undo, and a crash or hang ends the session |

Each approach leaves an agent with at least one of these problems:

- **It cannot see.** An agent working headless has no viewport. Without images
  it builds by guesswork.
- **It forgets.** Starting Blender for every call costs about two seconds and
  throws away the scene unless the agent saves and reloads by hand.
- **It cannot undo.** Blender's undo does not exist in background mode, so one
  bad script corrupts the scene for every call after it.
- **It gets stuck.** A long render or a runaway loop blocks everything, and a
  crash takes the work with it.
- **It is a chore to set up.** Add-ons, open windows, Python environments.

### What Ghostblend does about it

| Problem | Ghostblend's answer |
|---|---|
| Cannot see | `render_preview` returns a labelled multi-view image inside the tool result |
| Forgets | One Blender stays alive for the whole session; the scene persists between calls |
| Cannot undo | Every scene change is autosaved. A failed change rolls back automatically, and `checkpoint_restore` steps back any number of changes |
| Gets stuck | Runaway Python is interrupted; a hang or crash restarts Blender and restores the last autosave. Final renders run as separate background jobs |
| Setup chore | One executable. It downloads its own Blender engine the first time, or uses one you already have |

### Design principles

- **Blender stays Blender.** Ghostblend never rewrites or patches Blender. It
  runs the unmodified official build as a subprocess. Rewriting a program of
  millions of lines would be neither possible nor useful; the value is in the
  layer between the agent and Blender.
- **The agent should never lose work.** Autosave, rollback, and restore are on
  by default.
- **Errors should teach.** Arguments are validated before they reach Blender.
  A typo such as `widht` comes back as "did you mean 'width'?", and a missing
  object name lists the closest matches.
- **Nothing should linger.** If the server is killed, every Blender process it
  started exits within seconds.

---

## What an agent can do

Ask for something in plain language, for example:

> Make a small table with four legs and a wooden-looking top, show me all four
> views, check it is watertight, and export it as `table.glb`.

The agent then calls tools such as:

```text
scene_new
add_primitive    type=cube  name=Top   size=1  scale=[1.2, 0.8, 0.05]  location=[0, 0, 0.75]
add_primitive    type=cube  name=Leg1  size=1  scale=[0.05, 0.05, 0.72] location=[0.55, 0.35, 0.36]
object_duplicate name=Leg1  new_name=Leg2  location=[-0.55, 0.35, 0.36]
...
material_set     object=Top  base_color=[0.55, 0.35, 0.2, 1]  roughness=0.6
render_preview                          the image comes back and the agent looks at it
validate         objects=[Top, Leg1, ...]
export_model     path=table.glb
```

Typical uses:

- **Asset pipelines:** convert between glTF, FBX, OBJ, STL, PLY, USD and
  Alembic; apply modifiers; decimate; validate before shipping to a game engine.
- **3D printing prep:** find non-manifold edges, holes, flipped normals and
  unapplied scale, then export STL.
- **Procedural modelling from text:** build scenes from primitives and
  modifiers, iterating with previews.
- **Product and concept renders:** set materials and lights, then render with
  EEVEE or Cycles on your GPU.
- **Anything else Blender can do:** `run_python` gives full `bpy` access, and
  `api_describe` / `api_search` read the running Blender's own API so the agent
  gets names right the first time.

---

## Quick start

### 1. Build

You need a [Rust toolchain](https://rustup.rs).

```bash
cargo build --release
```

The executable is `target/release/ghostblend` (`ghostblend.exe` on Windows).

### 2. Install the engine (optional)

```bash
ghostblend setup
```

This downloads Blender 5.1.2 (414 MB) from download.blender.org, verifies it
against a pinned SHA-256, and unpacks it into Ghostblend's data folder. You can
skip this step: the server does the same thing automatically the first time it
needs Blender, and if you already have Blender 4.2 or newer installed it uses
that and downloads nothing.

### 3. Check it

```bash
ghostblend doctor
```

```text
[ok] Engine in use: C:\Program Files\Blender Foundation\Blender 5.1\blender.exe
[ok] Blender version: 5.1.1
[ok] Worker ready in 1.86s (Blender 5.1.1, Python 3.13.9)
[ok] Render engines: BLENDER_WORKBENCH, BLENDER_EEVEE, CYCLES
[ok] Cycles GPU: NVIDIA GeForce RTX 4070 (CUDA), NVIDIA GeForce RTX 4070 (OPTIX)
[ok] render_preview (256px) in 0.74s
```

### 4. Connect your agent

**Claude Code**

```bash
claude mcp add ghostblend -- /path/to/ghostblend
```

**Claude Desktop, Cursor, and other clients** use the same shape in their MCP
configuration file (`claude_desktop_config.json`, `.cursor/mcp.json`, and so on):

```json
{
  "mcpServers": {
    "ghostblend": {
      "command": "C:\\path\\to\\ghostblend.exe"
    }
  }
}
```

No arguments are needed; `serve` is the default command. Relative paths the
agent passes, such as `out/model.glb`, resolve against the directory the client
starts Ghostblend in.

### 5. Try it

Ask your agent to build something and to show you a preview.

---

## Tool reference

Distances are metres, rotations are degrees, colours are RGBA floats from 0 to 1.
Tools marked "autosaved" change the scene. If one fails, the scene rolls back to
how it was before the call.

### Scene

| Tool | Autosaved | What it does |
|---|---|---|
| `scene_new` | yes | Start an empty scene, or Blender's default cube, camera and light with `keep_defaults` |
| `scene_open` | yes | Open a `.blend` file |
| `scene_save` | | Save to a `.blend`; with no path, save back to the file opened or last saved |
| `scene_info` | | Every object with transform and mesh counts, plus camera, world, frame range, render settings and units |
| `object_info` | | One object in full: modifiers with their settings, materials, world bounding box, UVs, custom properties |

### Building

| Tool | Autosaved | What it does |
|---|---|---|
| `add_primitive` | yes | cube, uv_sphere, ico_sphere, cylinder, cone, torus, plane, circle, monkey, grid, empty, camera, light |
| `transform` | yes | Set or offset location, rotation and scale; `apply` bakes the transform into the mesh |
| `object_delete` | yes | Delete objects by name; unknown names are reported, not fatal |
| `object_duplicate` | yes | Copy an object, as an independent copy or a linked instance |
| `modifier_add` | yes | subdivision, mirror, array, boolean, bevel, solidify, decimate, triangulate, weld, displace, remesh, wireframe, screw, smooth, edge_split; `params` are checked against Blender's own settings |
| `modifier_apply` | yes | Bake one modifier, or all of them, into the mesh |
| `material_set` | yes | Principled BSDF: base colour, metallic, roughness, IOR, alpha, transmission, emission, image texture |

### Files

| Tool | Autosaved | What it does |
|---|---|---|
| `import_model` | yes | glTF/GLB, FBX, OBJ, STL, PLY, USD, Alembic; format from the extension |
| `export_model` | | Same formats; the whole scene or a selection; applies modifiers by default |

### Seeing and rendering

| Tool | What it does |
|---|---|
| `render_preview` | Quick images returned inline: by default a 2x2 sheet of front, right, top and perspective. `shading` is solid, textured or rendered; `objects` frames a subset. Never changes the scene |
| `render` | Final render of a still or an animation with Workbench, EEVEE or Cycles on GPU or CPU. Runs as a background job and returns a job id; `wait=true` blocks for short stills |
| `job_status` | State, progress percent, recent log lines, output files; `include_image` returns the finished picture |
| `job_cancel` | Stop a queued or running render |

### History

| Tool | Autosaved | What it does |
|---|---|---|
| `checkpoint_save` | | Save a named snapshot of the whole scene |
| `checkpoint_list` | | Named checkpoints plus the autosave history, one entry per change |
| `checkpoint_restore` | yes | Go back to a checkpoint, a specific autosave, or `steps=N` changes ago |

### Inspecting and scripting

| Tool | Autosaved | What it does |
|---|---|---|
| `validate` | | Non-manifold and boundary edges, loose and duplicate vertices, n-gons, zero-area faces, inconsistent normals, unapplied or negative scale, unapplied rotation, missing UVs, missing textures, objects far from the origin |
| `run_python` | yes | Run `bpy` code in the live scene; assign to `result` to return JSON. Interrupted after `timeout_s` (default 60) |
| `api_describe` | | Parameters, types, defaults, ranges and enum values of an operator or type, read from Blender's RNA |
| `api_search` | | Find operators and types by keyword |

---

## How it works

```text
AI agent (Claude Code, Cursor, ...)
        |  MCP: JSON-RPC over stdio
        v
+----------------------- ghostblend (Rust) -----------------------+
|  MCP server       tool schemas, argument checks, image encoding |
|  Supervisor       starts Blender, one call at a time, timeouts, |
|                   restart and restore after a crash or hang     |
|  Render jobs      one separate Blender per render, progress     |
|  Engine manager   finds, or downloads and verifies, Blender     |
+------------+-------------------------------------+--------------+
             | JSON lines over stdin/stdout        | per render job
             v                                     v
   blender -b --python bridge            blender -b scene.blend
   (one persistent worker)               (exits when the job ends)
```

**The Rust side** owns everything about the protocol and the processes: the MCP
server, the JSON Schema of every tool, argument validation, timeouts, restarts,
render jobs, image encoding, and finding or installing the engine.

**The Python bridge** is embedded in the executable and written into a
per-session folder at start-up. It runs inside Blender, reads requests on a
background thread, and executes them with `bpy` on Blender's main thread. It
knows nothing about MCP.

**Protocol hygiene.** Replies travel on a private copy of Blender's original
stdout. Everything else that Blender or an agent's script prints goes to stderr,
so stray output can never be mistaken for a reply.

**Safety net.** After every scene-changing command the bridge saves a copy of
the scene to `autosave/NNNNNN.blend` and keeps the last ten. If a command fails,
it reopens the previous autosave, so a retry starts clean. If Blender hangs past
the timeout, Ghostblend first interrupts Python code; if that does not work it
kills Blender, starts a new one, and restores the latest autosave. The old
process is always dead before the new one starts, so two Blenders never write
the same files.

**Render jobs** run as their own `blender -b` processes against a snapshot of
the scene, so a long render never blocks the agent's other work. Up to two run
at once, each is capped at one hour, and each watches its stdin: when Ghostblend
exits, even abruptly, the renders exit too.

The full design, with the reasoning behind each decision, is in
[`docs/superpowers/specs/2026-09-29-ghostblend-design.md`](docs/superpowers/specs/2026-09-29-ghostblend-design.md)
and the build plan is in [`docs/superpowers/plans/`](docs/superpowers/plans/).
Both are written in Indonesian.

---

## The Blender engine

Ghostblend needs Blender to do the actual 3D work, but you do not have to
install it. Ghostblend looks for an engine in this order:

1. `--blender <path>` or the `GHOSTBLEND_BLENDER` variable, if you set one. This
   is never silently replaced by something else.
2. A `blender/` folder next to the `ghostblend` executable, for offline bundles.
3. Ghostblend's own engine in its data folder.
4. An installed Blender 4.2 or newer. Older installs are skipped.
5. Otherwise, it downloads its own engine.

### Ghostblend's own engine

| | |
|---|---|
| Version | Blender 5.1.2, the official portable build, unmodified |
| Source | download.blender.org |
| Check | SHA-256 pinned in the source code, taken from Blender's published checksums |
| Download | 414 MB, once; an interrupted download resumes where it stopped |
| On disk | about 1.2 GB |
| Location | `%LOCALAPPDATA%\ghostblend\rt\5.1.2` on Windows, `~/.local/share/ghostblend/rt/5.1.2` on Linux |
| Platforms | Windows x64, Windows ARM64, Linux x64 |

While the engine downloads, the server is already running. Tool calls answer
with progress such as "downloading 43% (178 of 414 MB)", so the agent knows to
try again shortly. A stalled connection is detected after 60 seconds and
retried up to four times. A corrupt file fails the checksum and is deleted. The
engine counts as installed only after it has been fully unpacked.

`--runtime managed` always uses Ghostblend's own engine, even if Blender is
installed. `--runtime system` only uses an installed Blender. `--no-download`
turns off automatic downloads.

### Offline bundle

To ship Ghostblend to a machine without internet, unzip the official portable
Blender build into a folder named `blender` next to the executable:

```text
ghostblend/
  ghostblend.exe
  blender/
    blender.exe
    5.1/ ...
```

---

## Configuration

| Flag | Meaning |
|---|---|
| `--blender <path>` | Use this Blender instead of the engine |
| `--runtime <auto\|managed\|system>` | Where the engine comes from (default `auto`) |
| `--no-download` | Never download the engine automatically |
| `--workdir <dir>` | Base directory for relative paths (default: the current directory) |
| `--session-dir <dir>` | Exact folder for this session's autosaves, previews and renders |
| `--resume <id>` | Continue an earlier session and restore its latest autosave |
| `--ephemeral` | Delete the session folder when the server exits |
| `--autosave-keep <n>` | Autosaves kept per session (default 10) |
| `--max-jobs <n>` | Render jobs running at the same time (default 2) |
| `--log-file <file>` | Write logs to a file instead of stderr |

| Environment variable | Meaning |
|---|---|
| `GHOSTBLEND_BLENDER` | Same as `--blender` |
| `GHOSTBLEND_HOME` | Move the data folder (engine and sessions) |
| `GHOSTBLEND_LOG` | Log level, for example `debug` |

Commands: `ghostblend` or `ghostblend serve` runs the MCP server,
`ghostblend setup` installs the engine, and `ghostblend doctor` checks everything.

---

## Troubleshooting

Start with `ghostblend doctor`. It shows which engine is in use and whether it
starts and renders.

| Symptom | What to do |
|---|---|
| Tools say the engine is being set up | Wait for the first download to finish, or run `ghostblend setup` in a terminal to watch progress |
| The download stopped | Run `ghostblend setup` again; it resumes |
| "Blender executable not found at ..." | The path in `--blender` or `GHOSTBLEND_BLENDER` is wrong. Fix it, or remove it to use the engine |
| "cannot download its engine automatically on this platform" | On macOS, install Blender 4.2+ and pass `--blender` |
| A command "timed out" | Blender was restarted and your scene restored. Use `checkpoint_list` to see where you are |
| An old Blender is picked up | Versions below 4.2 are skipped. Use `--runtime managed` to always use the engine |
| You need detail | Run with `GHOSTBLEND_LOG=debug`, or `--log-file ghostblend.log` |

Session folders live in `<data>/s/<id>`, next to the engine. Each holds the
autosaves, previews and default render outputs of one server run.

---

## Performance

Measured on Windows 11 with an RTX 4070 and an i5-13400F, Blender 5.1.

| Operation | Time |
|---|---|
| Worker start, from launch to ready | 1.9 to 2.8 s, once per session |
| A typical tool call (add, transform, material) | 10 to 20 ms |
| `render_preview`, one 256 px view | 0.7 to 0.9 s |
| `render_preview`, four 420 px views | 1.5 s |
| Autosave of a small scene | about 5 ms |
| Export of a small scene to glTF | 0.4 s |

---

## Security

`run_python` runs code the agent writes, with the full power of Blender's Python
on your machine, like any scripting tool. Use Ghostblend with agents and inputs
you trust. Ghostblend speaks MCP over stdio only, so it can do nothing beyond
what the agent's own process could already do; it opens no network port. The
engine download goes only to download.blender.org and is checked against a
pinned hash.

---

## Development

```text
src/            Rust: MCP server, supervisor, render jobs, engine manager
bridge/         Python bridge that runs inside Blender (embedded at build time)
tests/bridge/   Bridge tests, executed inside Blender
tests/e2e/      End-to-end test that speaks MCP to the built executable
docs/           Design spec, build plan, images
```

| Test layer | Command | Count |
|---|---|---|
| Rust unit tests | `cargo test --lib` | 48 |
| Supervisor against real Blender | `cargo test --test worker_integration` | 6 |
| Bridge inside Blender | `blender -b --factory-startup --python tests/bridge/run_tests.py` | 74 |
| End to end over MCP | `python tests/e2e/mcp_e2e.py --binary target/release/ghostblend.exe --stage render` | 21 checks |

The end-to-end test covers invalid arguments, a missing Blender, parallel calls,
inline preview images, render jobs, cancellation, and that no Blender process
survives when Ghostblend is killed.

---

## Status and limitations

Ghostblend is at version 0.1. Everything above is implemented and tested on
Windows 11 with Blender 5.1. Known gaps:

- The automatic engine download was tested against a local test server and a
  copied engine, but not yet as a full live download from blender.org.
- Engine setup on Linux is implemented but has not been tested on Linux. macOS
  needs an installed Blender.
- Transport is stdio only. An HTTP transport with authentication, for hosted
  use, is planned.
- One server process drives one scene.
- Dedicated tools for geometry nodes and animation are planned; until then,
  `run_python` covers them.

## License

A license for Ghostblend has not been chosen yet. Blender is a separate program
under the GNU GPL. Ghostblend runs it as a subprocess and contains none of its
code; its engine is the unmodified official build from blender.org. If you
redistribute a bundle with Blender inside, include Blender's license files,
which come with the official archive.
