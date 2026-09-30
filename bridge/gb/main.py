"""Ghostblend worker main loop (runs inside `blender -b`).

Protocol:
  * requests arrive as JSON lines on stdin and are read by a background thread;
  * responses and events leave through a private duplicate of the original stdout
    descriptor, each line prefixed with MARKER;
  * file descriptor 1 is pointed at stderr, so Blender's own prints and anything
    agent code writes can never corrupt the protocol stream;
  * stdin EOF (the ghostblend process went away) ends the worker immediately.
"""
import argparse
import json
import os
import queue
import sys
import threading
import time
import traceback

import bpy

from . import history, registry, runtime, util

MARKER = "@@bhm:"


class _Proto:
    def __init__(self, fd):
        self.fd = fd
        self.lock = threading.Lock()

    def send(self, obj):
        text = json.dumps(obj, ensure_ascii=False, default=util.json_default)
        data = ("\n" + MARKER + text + "\n").encode("utf-8")
        with self.lock:
            view = memoryview(data)
            while view:
                n = os.write(self.fd, view)
                view = view[n:]


PROTO = None


def log(level, msg):
    if PROTO is not None:
        try:
            PROTO.send({"event": "log", "level": level, "msg": str(msg)[:2000]})
            return
        except Exception:
            pass
    try:
        os.write(2, f"[ghostblend-bridge {level}] {msg}\n".encode("utf-8", "replace"))
    except OSError:
        pass


def _import_commands():
    # Importing a module registers its commands.
    from . import scene, pyexec, materials  # noqa: F401
    for name in ("objects", "modifiers", "model_io", "preview", "validate", "api", "render",
                 "editmesh", "sculpt", "paint", "lighting", "bake", "animate"):
        try:
            __import__(f"{__package__}.{name}")
        except ModuleNotFoundError as e:
            if e.name != f"{__package__}.{name}":
                raise


def _label(cmd, args, result):
    for source in (result, args):
        if isinstance(source, dict):
            for key in ("name", "object", "path", "label"):
                v = source.get(key)
                if isinstance(v, str) and v:
                    return v[:120]
    return None


def _format_error(exc):
    from .pyexec import ExecError

    if isinstance(exc, util.UserError):
        d = {"type": exc.kind, "message": str(exc)}
        if exc.hint:
            d["hint"] = exc.hint
        return d
    if isinstance(exc, ExecError):
        return dict(exc.payload)
    if isinstance(exc, runtime.GhostInterrupt):
        return {"type": "Interrupted", "message": "The command was interrupted"}
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return {"type": type(exc).__name__, "message": str(exc) or repr(exc), "traceback": tb[-3000:]}


def process(req):
    """Execute one request and return the response object (without sending it)."""
    rid = req.get("id")
    cmd = req.get("cmd")
    args = req.get("args") or {}
    t0 = time.perf_counter()
    resp = {"id": rid}
    runtime.begin(rid)
    try:
        fn = registry.COMMANDS.get(cmd)
        if fn is None:
            raise util.UserError(f"Unknown command {cmd!r}", kind="UnknownCommand",
                                 hint="Known commands: " + ", ".join(sorted(registry.COMMANDS)))
        result = fn(args)
        resp["ok"] = True
        resp["result"] = {} if result is None else result
        if req.get("autosave"):
            try:
                resp["autosave"] = history.autosave(cmd, label=_label(cmd, args, resp["result"]))
            except Exception as e:  # the command itself succeeded
                resp["warnings"] = [f"autosave failed: {type(e).__name__}: {e}"]
    except BaseException as exc:  # noqa: BLE001 - includes GhostInterrupt and SystemExit
        resp["ok"] = False
        resp.pop("result", None)
        resp["error"] = _format_error(exc)
        if req.get("atomic"):
            try:
                resp["rolled_back"] = history.rollback() is not None
            except BaseException as e2:  # noqa: BLE001
                resp["rolled_back"] = False
                resp["error"]["rollback_error"] = f"{type(e2).__name__}: {e2}"
    finally:
        runtime.end()
    resp["ms"] = int((time.perf_counter() - t0) * 1000)
    return resp


def _send_response(resp):
    try:
        PROTO.send(resp)
    except Exception as e:  # result not serializable
        PROTO.send({"id": resp.get("id"), "ok": False, "ms": resp.get("ms", 0),
                    "error": {"type": "SerializationError", "message": f"{type(e).__name__}: {e}"}})


def _control(msg):
    kind = msg.get("control")
    if kind == "interrupt":
        runtime.request_interrupt(msg.get("id"))
    elif kind == "quit":
        os._exit(0)


def _reader(q):
    stream = sys.stdin.buffer if sys.stdin is not None else os.fdopen(0, "rb", buffering=0)
    while True:
        try:
            line = stream.readline()
        except Exception:
            line = b""
        if not line:
            os._exit(0)  # ghostblend went away: never linger as an orphan
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except Exception as e:
            log("warn", f"ignoring malformed request line: {e}")
            continue
        if "control" in msg:
            _control(msg)
        else:
            q.put(msg)


def _setup_stdio():
    """Keep the original stdout for the protocol and send everything else to stderr."""
    try:
        sys.stdout.flush()
    except Exception:
        pass
    proto_fd = os.dup(1)
    os.dup2(2, 1)
    if os.name == "nt":
        try:
            import ctypes
            import msvcrt

            msvcrt.setmode(proto_fd, os.O_BINARY)
            msvcrt.setmode(0, os.O_BINARY)
            ctypes.windll.kernel32.SetStdHandle(-11, ctypes.c_void_p(msvcrt.get_osfhandle(2)))
        except Exception:
            pass
    sys.stdout = sys.stderr
    return proto_fd


def bootstrap(session_dir, workdir, keep, restore_latest):
    """Shared start-up for the real worker and the test runner. Returns restore info or None."""
    util.WORKDIR = os.path.abspath(workdir)
    runtime.set_main_thread()
    _import_commands()
    history.init(session_dir, keep)
    from . import scene

    restored = None
    latest = history.latest()
    if restore_latest and latest is not None:
        try:
            restored = history.restore_seq(latest)
        except Exception as e:
            log("error", f"could not restore autosave #{latest}: {e}")
    if restored is None:
        scene.reset_scene(empty=True)
        history.S["logical_path"] = None
        history.S["saved_seq"] = None
        history.autosave("init", label="empty scene")
    return restored


def main(argv):
    ap = argparse.ArgumentParser(prog="ghostblend-bridge")
    ap.add_argument("--session", required=True)
    ap.add_argument("--workdir", default=os.getcwd())
    ap.add_argument("--autosave-keep", type=int, default=10)
    ap.add_argument("--restore-latest", action="store_true")
    ns = ap.parse_args(argv)

    global PROTO
    PROTO = _Proto(_setup_stdio())
    try:
        restored = bootstrap(ns.session, ns.workdir, ns.autosave_keep, ns.restore_latest)
    except BaseException as e:  # noqa: BLE001
        PROTO.send({"event": "fatal", "msg": f"{type(e).__name__}: {e}",
                    "traceback": traceback.format_exc()[-3000:]})
        os._exit(1)

    q = queue.Queue()
    threading.Thread(target=_reader, args=(q,), daemon=True, name="ghostblend-stdin").start()
    PROTO.send({"event": "ready", "blender": bpy.app.version_string, "python": sys.version.split()[0],
                "pid": os.getpid(), "seq": history.S["seq"], "restored": restored})
    while True:
        try:
            req = q.get()
            _send_response(process(req))
        except BaseException as e:  # noqa: BLE001 - the loop must survive stray interrupts
            try:
                log("error", f"main loop: {type(e).__name__}: {e}")
            except BaseException:  # noqa: BLE001
                pass
