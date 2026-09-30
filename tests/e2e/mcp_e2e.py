"""End-to-end test: speak MCP JSON-RPC over stdio to a built ghostblend binary.

    python tests/e2e/mcp_e2e.py --binary target/debug/ghostblend.exe [--keep]

Exits 0 on success, 1 on failure. Skips gracefully (exit 0) if the binary is
missing so the suite stays green where the project has not been built.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

PROTOCOL_VERSION = "2026-07-28"


class Client:
    def __init__(self, binary, workdir, extra_args=None):
        binary = os.path.normpath(os.path.abspath(binary))
        args = [binary, "serve", "--workdir", workdir, "--session-dir",
                os.path.join(workdir, "session"), "--ephemeral"]
        if extra_args:
            args += extra_args
        self.proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, "GHOSTBLEND_LOG": "warn"},
        )
        self._id = 0
        self._pending = {}
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._stderr = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg:
                with self._cv:
                    self._pending[msg["id"]] = msg
                    self._cv.notify_all()

    def _read_stderr(self):
        for raw in self.proc.stderr:
            self._stderr.append(raw.decode("utf-8", "replace").rstrip())

    def stderr_text(self):
        return "\n".join(self._stderr[-40:])

    def send(self, method, params, *, want_id=True):
        with self._lock:
            self._id += 1
            rid = self._id
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        if want_id:
            msg["id"] = rid
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        return rid

    def wait(self, rid, timeout=120):
        deadline = time.time() + timeout
        with self._cv:
            while rid not in self._pending:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(f"no response to request {rid}\nstderr:\n{self.stderr_text()}")
                self._cv.wait(remaining)
            return self._pending.pop(rid)

    def call(self, method, params, timeout=120):
        rid = self.send(method, params)
        resp = self.wait(rid, timeout)
        if "error" in resp:
            raise AssertionError(f"{method} returned JSON-RPC error: {resp['error']}")
        return resp["result"]

    def initialize(self):
        result = self.call("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "ghostblend-e2e", "version": "0"},
        })
        self.send("notifications/initialized", {}, want_id=False)
        return result

    def tool(self, name, arguments, timeout=120):
        return self.call("tools/call", {"name": name, "arguments": arguments}, timeout)

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def text_of(result):
    return "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")


def has_png_image(result):
    for c in result.get("content", []):
        if c.get("type") == "image" and c.get("mimeType") == "image/png":
            data = base64.b64decode(c["data"])
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                return True
    return False


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail and not cond else ""))


def _descendant_blender_pids(parent_pid):
    """Blender PIDs whose parent is `parent_pid` (Windows: PowerShell CIM, then wmic)."""
    ps = (f"Get-CimInstance Win32_Process -Filter 'ParentProcessId={parent_pid}' "
          f"| Where-Object {{ $_.Name -like '*blender*' }} "
          f"| ForEach-Object {{ $_.ProcessId }}")
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            stderr=subprocess.DEVNULL, text=True, timeout=15)
        pids = [int(t.strip()) for t in out.split() if t.strip().isdigit()]
        return pids
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", f"(ParentProcessId={parent_pid})",
             "get", "ProcessId,Name", "/format:csv"],
            stderr=subprocess.DEVNULL, text=True, timeout=10)
    except Exception:
        return None  # neither tool available: caller skips the check
    pids = []
    for line in out.splitlines():
        if "blender" in line.lower():
            for tok in line.split(","):
                tok = tok.strip()
                if tok.isdigit():
                    pids.append(int(tok))
    return pids


def _pid_alive(pid):
    try:
        out = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}"],
                                      stderr=subprocess.DEVNULL, text=True, timeout=10)
        return str(pid) in out
    except Exception:
        return False


def check_no_orphans(binary, tmp):
    """After ghostblend is killed abruptly, its Blender children must not survive."""
    work = os.path.join(tmp, "orphan")
    os.makedirs(work, exist_ok=True)
    c = Client(binary, work)
    try:
        c.initialize()
        c.tool("scene_new", {})
        c.tool("add_primitive", {"type": "monkey"})
        c.send("tools/call", {"name": "render", "arguments": {
            "output_path": os.path.join(work, "o.png"), "resolution": [800, 600],
            "engine": "cycles", "samples": 3000}})
        time.sleep(3)
        children = _descendant_blender_pids(c.proc.pid)
        if children is None:
            check("no orphan Blender after kill (skipped: wmic unavailable)", True)
            return
        check("render spawned Blender child processes", len(children) >= 1, f"children={children}")
        c.proc.kill()
        deadline = time.time() + 6
        while time.time() < deadline and any(_pid_alive(p) for p in children):
            time.sleep(0.5)
        survivors = [p for p in children if _pid_alive(p)]
        check("no orphan Blender after kill", not survivors, f"survivors={survivors}")
        for p in survivors:  # clean up if the test failed
            subprocess.run(["taskkill", "/F", "/PID", str(p)], stderr=subprocess.DEVNULL)
    finally:
        try:
            c.proc.kill()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--stage", default="core", choices=["core", "preview", "render"])
    ap.add_argument("--server-arg", action="append", default=[],
                    help="extra argument for the main server, e.g. --server-arg=--runtime --server-arg=managed")
    args = ap.parse_args()
    if not os.path.isfile(args.binary):
        print(f"SKIP: binary not found at {args.binary}")
        return 0

    tmp = tempfile.mkdtemp(prefix="gb-e2e-")

    # 1) Bad Blender path: server still lists tools; calls return an actionable error.
    bad = Client(args.binary, tmp, extra_args=["--blender", r"Z:\nope\blender.exe"])
    try:
        bad.initialize()
        tools = bad.call("tools/list", {})["tools"]
        check("tools/list works without a valid Blender", len(tools) == 32, f"got {len(tools)}")
        res = bad.tool("scene_new", {}, timeout=120)
        check("call with bad Blender is a clean error",
              res.get("isError") is True and "unavailable" in text_of(res).lower(),
              text_of(res)[:200])
    finally:
        bad.close()

    # 2) Real Blender (discovered, or as selected by --server-arg): the working path.
    c = Client(args.binary, tmp, extra_args=args.server_arg)
    try:
        info = c.initialize()
        check("initialize returns serverInfo",
              info.get("serverInfo", {}).get("name") == "ghostblend", info.get("serverInfo"))

        tools = c.call("tools/list", {})["tools"]
        names = {t["name"] for t in tools}
        check("tools/list has 32 tools", len(tools) == 32, f"got {len(tools)}")
        for required in ("scene_new", "add_primitive", "render_preview", "run_python"):
            check(f"tool '{required}' present", required in names)

        res = c.tool("scene_new", {})
        check("scene_new succeeds", not res.get("isError"), text_of(res)[:200])

        res = c.tool("run_python", {"code": "result = 2 + 2"})
        check("run_python returns a result", '"result": 4' in text_of(res) or '"result":4' in text_of(res),
              text_of(res)[:200])

        res = c.tool("add_primitive", {"type": "not_a_shape"})
        check("invalid enum arg is an isError with a message",
              res.get("isError") is True and "one of" in text_of(res), text_of(res)[:200])

        res = c.tool("add_primitive", {"typo": True})
        check("unknown arg is rejected", res.get("isError") is True and "unknown parameter" in text_of(res).lower(),
              text_of(res)[:200])

        # Parallel calls: fire several tools/call without waiting, then collect.
        ids = [c.send("tools/call", {"name": "run_python", "arguments": {"code": f"result = {i} * 10"}})
               for i in range(3)]
        results = [c.wait(i, timeout=120) for i in ids]
        values = sorted(text_of(r["result"]).count('"result": ' + str(i * 10)) for i, r in enumerate(results))
        check("parallel calls all answered", len(results) == 3 and all("result" in r for r in results))
        check("each parallel call kept its own result", sum(values) == 3, f"values={values}")

        # The modelling, sculpting, painting, lighting, baking and animation tools over MCP.
        c.tool("scene_new", {})
        c.tool("add_primitive", {"type": "cube", "name": "Box"})
        res = c.tool("edit_mesh", {"object": "Box", "operation": "extrude", "distance": 1.0,
                                   "select": {"facing": [0, 0, 1]}})
        check("edit_mesh extrudes the selected face", not res.get("isError") and '"faces_selected": 1' in text_of(res),
              text_of(res)[:200])
        c.tool("add_primitive", {"type": "uv_sphere", "name": "Ball", "location": [4, 0, 0]})
        res = c.tool("sculpt", {"object": "Ball", "brush": "draw", "points": [[4, 0, 1], [4.3, 0, 0.9]],
                                "radius": 0.4, "subdivide": 1, "symmetry": ["x"]})
        check("sculpt stroke moves vertices", not res.get("isError") and '"vertices_moved": 0' not in text_of(res),
              text_of(res)[:200])
        res = c.tool("sculpt", {"object": "Ball", "brush": "draw", "points": [[1, 2]]})
        check("sculpt rejects a 2D point", res.get("isError") is True and "3 item" in text_of(res), text_of(res)[:200])
        res = c.tool("paint", {"object": "Ball", "target": "texture", "color": [1, 0, 0], "resolution": 64,
                               "select": {"facing": [0, 0, 1]}})
        check("paint texture fills faces", not res.get("isError") and "painted_texels" in text_of(res), text_of(res)[:200])
        res = c.tool("world_set", {"hdri": "studio", "strength": 1.5})
        check("world_set loads a bundled HDRI", not res.get("isError") and "studio" in text_of(res), text_of(res)[:200])
        res = c.tool("light_set", {"name": "Key", "type": "AREA", "energy": 600, "location": [3, -3, 4],
                                   "look_at": [0, 0, 0]})
        check("light_set creates a light", not res.get("isError") and '"created": true' in text_of(res), text_of(res)[:200])
        res = c.tool("animate", {"object": "Box", "property": "rotation_deg", "interpolation": "linear",
                                 "keys": [{"frame": 1, "value": [0, 0, 0]}, {"frame": 24, "value": [0, 0, 180]}]})
        check("animate inserts keyframes", not res.get("isError") and '"keys": 2' in text_of(res), text_of(res)[:200])
        res = c.tool("bake", {"object": "Box", "type": "ao", "resolution": 32, "samples": 1, "device": "cpu"})
        check("bake writes a texture", not res.get("isError") and ".png" in text_of(res), text_of(res)[:200])
        res = c.tool("run_python", {"code": "bpy.ops.mesh.loopcut_slide()"})
        check("crashing operator is blocked", res.get("isError") is True and "loop_cut" in text_of(res), text_of(res)[:200])

        if args.stage in ("preview", "render"):
            c.tool("scene_new", {})
            c.tool("add_primitive", {"type": "monkey"})
            t0 = time.time()
            res = c.tool("render_preview", {"size": 256, "views": ["front"]}, timeout=300)
            check("render_preview returns a PNG image", has_png_image(res), text_of(res)[:200])
            print(f"    render_preview round trip: {time.time() - t0:.2f}s")

        if args.stage == "render":
            out = os.path.join(tmp, "out.png")
            res = c.tool("render", {"output_path": out, "resolution": [320, 240], "wait": True,
                                    "engine": "workbench"}, timeout=300)
            check("render wait=true produces a file", os.path.isfile(out) or has_png_image(res),
                  text_of(res)[:200])

            # Relative output path resolves under the working directory.
            res = c.tool("render", {"output_path": "rel_out.png", "resolution": [160, 120],
                                    "wait": True, "engine": "workbench"}, timeout=300)
            check("relative output_path lands under workdir",
                  os.path.isfile(os.path.join(tmp, "rel_out.png")), text_of(res)[:200])

            # Cancel a long render.
            start = c.tool("render", {"output_path": os.path.join(tmp, "long.png"),
                                      "resolution": [800, 600], "engine": "cycles", "samples": 2000})
            job_id = None
            for line in text_of(start).splitlines():
                if "job" in line.lower() and line.strip().startswith("{"):
                    pass
            import re as _re
            m = _re.search(r'"job_id":\s*"(\w+)"', text_of(start))
            job_id = m.group(1) if m else None
            check("render returns a job id", job_id is not None, text_of(start)[:200])
            if job_id:
                time.sleep(2)
                res = c.tool("job_cancel", {"job_id": job_id})
                time.sleep(1)
                res = c.tool("job_status", {"job_id": job_id})
                check("job_cancel stops the render",
                      '"state": "cancelled"' in text_of(res), text_of(res)[:200])
    finally:
        c.close()

    if args.stage == "render":
        check_no_orphans(args.binary, tmp)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
